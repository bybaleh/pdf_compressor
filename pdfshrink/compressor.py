"""Ядро pdfshrink: подбор параметров Ghostscript под целевой размер файла."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

KB = 1000
MB = 1000 * 1000
KIB = 1024
MIB = 1024 * 1024

#: Цель по умолчанию — 10 МБ (10 000 000 байт). Это строже, чем 10 MiB,
#: поэтому результат проходит любой лимит «10 Мб».
DEFAULT_TARGET_BYTES = 10 * MB

#: Сколько секунд максимум ждём один запуск Ghostscript.
DEFAULT_TIMEOUT = 900

LogFn = Callable[[str], None]


class CompressionError(RuntimeError):
    """Ошибка, из-за которой файл невозможно обработать."""


@dataclass(frozen=True)
class Profile:
    """Один «шаг качества» для Ghostscript.

    Профили в :data:`LADDER` отсортированы от лучшего качества к худшему,
    размер результата при этом монотонно уменьшается.
    """

    name: str
    pdfsettings: str  # /prepress, /printer, /ebook, /screen
    image_dpi: int  # разрешение цветных и серых изображений
    mono_dpi: int  # разрешение чёрно-белых (1-битных) изображений
    qfactor: float  # качество JPEG: 0.1 — отлично, 3.0 — сильно сжато
    grayscale: bool = False

    def describe(self) -> str:
        gray = ", grayscale" if self.grayscale else ""
        return (
            f"{self.name} (preset={self.pdfsettings}, {self.image_dpi} dpi, "
            f"mono {self.mono_dpi} dpi, QFactor {self.qfactor}{gray})"
        )


#: Лестница качества: индекс 0 — максимальное качество, последний — минимальное.
LADDER: tuple[Profile, ...] = (
    Profile("prepress-300", "/prepress", 300, 1200, 0.4),
    Profile("printer-250", "/printer", 250, 1200, 0.6),
    Profile("ebook-200", "/ebook", 200, 900, 0.9),
    Profile("ebook-150", "/ebook", 150, 600, 1.3),
    Profile("screen-120", "/screen", 120, 600, 1.7),
    Profile("screen-100", "/screen", 100, 400, 2.2),
    Profile("screen-72", "/screen", 72, 300, 2.6),
    Profile("screen-50", "/screen", 50, 300, 3.0),
)

#: Дополнительные шаги, которые включаются флагом --allow-grayscale.
GRAYSCALE_LADDER: tuple[Profile, ...] = (
    Profile("gray-150", "/ebook", 150, 600, 1.3, grayscale=True),
    Profile("gray-100", "/screen", 100, 400, 2.2, grayscale=True),
    Profile("gray-72", "/screen", 72, 300, 2.6, grayscale=True),
    Profile("gray-50", "/screen", 50, 300, 3.2, grayscale=True),
)


@dataclass
class Attempt:
    """Результат одного запуска Ghostscript."""

    profile: Profile | None
    size: int
    path: Path
    seconds: float

    @property
    def label(self) -> str:
        return self.profile.name if self.profile else "lossless"


@dataclass
class Result:
    """Итог обработки одного PDF."""

    source: Path
    output: Path | None
    original_size: int
    final_size: int
    target_size: int
    profile: str
    attempts: int
    ok: bool
    message: str = ""
    seconds: float = 0.0
    log: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        if not self.original_size:
            return 0.0
        return 1.0 - (self.final_size / self.original_size)

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "output": str(self.output) if self.output else None,
            "original_size": self.original_size,
            "final_size": self.final_size,
            "target_size": self.target_size,
            "original_size_human": format_size(self.original_size),
            "final_size_human": format_size(self.final_size),
            "saved_percent": round(self.ratio * 100, 1),
            "profile": self.profile,
            "attempts": self.attempts,
            "seconds": round(self.seconds, 2),
            "ok": self.ok,
            "message": self.message,
        }


# --------------------------------------------------------------------------- #
# Утилиты размеров
# --------------------------------------------------------------------------- #

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Zа-яА-Я]*)\s*$")
_UNITS = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": KB,
    "kb": KB,
    "ki": KIB,
    "kib": KIB,
    "m": MB,
    "mb": MB,
    "mi": MIB,
    "mib": MIB,
    "g": MB * 1000,
    "gb": MB * 1000,
    "gi": MIB * 1024,
    "gib": MIB * 1024,
    # кириллические варианты, чтобы «10мб» тоже работало
    "кб": KB,
    "мб": MB,
    "гб": MB * 1000,
}


def parse_size(value: str | int | float) -> int:
    """Преобразует «10MB», «9.5 MiB», «500k», «10мб», 10485760 в число байт."""
    if isinstance(value, (int, float)):
        size = int(value)
        if size <= 0:
            raise ValueError("размер должен быть больше нуля")
        return size

    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(f"не понимаю размер: {value!r} (примеры: 10MB, 9.5MiB, 800k)")
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit not in _UNITS:
        raise ValueError(f"неизвестная единица измерения: {match.group(2)!r}")
    size = int(number * _UNITS[unit])
    if size <= 0:
        raise ValueError("размер должен быть больше нуля")
    return size


def format_size(num_bytes: int) -> str:
    """Человекочитаемый размер (десятичные единицы, как у лимитов загрузки)."""
    if num_bytes < KB:
        return f"{num_bytes} B"
    for unit, factor in (("GB", MB * 1000), ("MB", MB), ("kB", KB)):
        if num_bytes >= factor:
            return f"{num_bytes / factor:.2f} {unit}"
    return f"{num_bytes} B"


# --------------------------------------------------------------------------- #
# Ghostscript / qpdf
# --------------------------------------------------------------------------- #


def find_tool(name: str, env_var: str | None = None) -> str | None:
    """Ищет бинарник (с возможностью переопределения через переменную среды)."""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return override if Path(override).is_absolute() else shutil.which(override)
    return shutil.which(name)


def require_ghostscript() -> str:
    gs = find_tool("gs", "GHOSTSCRIPT_BIN")
    if not gs:
        raise CompressionError(
            "Ghostscript (gs) не найден. Запускайте инструмент в Docker-образе "
            "pdfshrink или установите ghostscript локально."
        )
    return gs


def build_gs_command(gs_bin: str, src: Path, dst: Path, profile: Profile) -> list[str]:
    """Собирает команду Ghostscript для одного профиля качества."""
    distiller = (
        "<< /ColorImageDict << /QFactor {q} /Blend 1 /HSample [2 1 1 2] /VSample [2 1 1 2] >>"
        "   /GrayImageDict  << /QFactor {q} /Blend 1 /HSample [2 1 1 2] /VSample [2 1 1 2] >> >>"
        " setdistillerparams"
    ).format(q=profile.qfactor)

    cmd = [
        gs_bin,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-dSAFER",
        f"-dPDFSETTINGS={profile.pdfsettings}",
        # шрифты и дубликаты
        "-dEmbedAllFonts=true",
        "-dSubsetFonts=true",
        "-dCompressFonts=true",
        "-dDetectDuplicateImages=true",
        "-dFastWebView=true",
        # цветные изображения
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={profile.image_dpi}",
        "-dAutoFilterColorImages=false",
        "-dColorImageFilter=/DCTEncode",
        # серые изображения
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={profile.image_dpi}",
        "-dAutoFilterGrayImages=false",
        "-dGrayImageFilter=/DCTEncode",
        # чёрно-белые изображения
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={profile.mono_dpi}",
        "-dMonoImageFilter=/CCITTFaxEncode",
    ]
    if profile.grayscale:
        cmd += [
            "-sColorConversionStrategy=Gray",
            "-dProcessColorModel=/DeviceGray",
            "-dOverrideICC=true",
        ]
    cmd += ["-o", str(dst), "-c", distiller, "-f", str(src)]
    return cmd


def run_ghostscript(
    src: Path,
    dst: Path,
    profile: Profile,
    timeout: int = DEFAULT_TIMEOUT,
    gs_bin: str | None = None,
) -> int:
    """Запускает Ghostscript и возвращает размер полученного файла в байтах."""
    gs_bin = gs_bin or require_ghostscript()
    cmd = build_gs_command(gs_bin, src, dst, profile)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - защита от зависаний
        raise CompressionError(
            f"Ghostscript не уложился в {timeout} с на профиле {profile.name}"
        ) from exc

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        output = (proc.stdout or b"").decode("utf-8", "replace").strip()
        tail = output[-800:] if output else "(пустой вывод)"
        raise CompressionError(
            f"Ghostscript завершился с кодом {proc.returncode} на профиле "
            f"{profile.name}:\n{tail}"
        )
    return dst.stat().st_size


def run_lossless(
    src: Path,
    dst: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> int | None:
    """Пробует сжать PDF без потери качества (qpdf: потоки объектов + flate).

    Возвращает размер результата или ``None``, если qpdf недоступен/не справился.
    """
    qpdf = find_tool("qpdf", "QPDF_BIN")
    if not qpdf:
        return None
    cmd = [
        qpdf,
        "--object-streams=generate",
        "--compress-streams=y",
        "--recompress-flate",
        "--compression-level=9",
        "--stream-data=compress",
        "--linearize",
        str(src),
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover
        return None
    # qpdf возвращает 3 при «warnings», файл при этом корректен.
    if proc.returncode not in (0, 3) or not dst.exists() or dst.stat().st_size == 0:
        return None
    return dst.stat().st_size


# --------------------------------------------------------------------------- #
# Основной алгоритм
# --------------------------------------------------------------------------- #


def _validate_pdf(path: Path) -> None:
    if not path.exists():
        raise CompressionError(f"файл не найден: {path}")
    if not path.is_file():
        raise CompressionError(f"это не файл: {path}")
    if path.stat().st_size == 0:
        raise CompressionError(f"файл пустой: {path}")
    with path.open("rb") as fh:
        header = fh.read(5)
    if header[:4] != b"%PDF":
        raise CompressionError(f"это не PDF (нет заголовка %PDF): {path}")


def _ladder(allow_grayscale: bool, min_dpi: int | None) -> list[Profile]:
    profiles = list(LADDER)
    if allow_grayscale:
        profiles += list(GRAYSCALE_LADDER)
    if min_dpi:
        filtered = [p for p in profiles if p.image_dpi >= min_dpi]
        profiles = filtered or [profiles[0]]
    return profiles


def compress_pdf(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    target_size: int = DEFAULT_TARGET_BYTES,
    *,
    allow_grayscale: bool = False,
    min_dpi: int | None = None,
    force: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    log: LogFn | None = None,
) -> Result:
    """Сжимает ``source`` до ``target_size`` байт и сохраняет в ``output``.

    Алгоритм:

    1. Если файл уже меньше цели и нет ``force`` — просто копируем.
    2. Пробуем сжатие без потерь (qpdf).
    3. Бинарным поиском по «лестнице качества» ищем самый качественный
       профиль Ghostscript, который укладывается в целевой размер.

    Возвращает :class:`Result`; исключение бросается только если файл
    невозможно обработать вообще.
    """
    started = time.monotonic()
    src = Path(source).expanduser().resolve()
    dst = Path(output).expanduser()
    messages: list[str] = []

    def emit(msg: str) -> None:
        messages.append(msg)
        if log:
            log(msg)

    _validate_pdf(src)
    original_size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)

    emit(
        f"{src.name}: исходный размер {format_size(original_size)}, "
        f"цель ≤ {format_size(target_size)}"
    )

    # Шаг 1 — файл уже достаточно маленький.
    if original_size <= target_size and not force:
        _finalize(src, dst)
        emit("файл уже меньше цели — копируем без изменений")
        return Result(
            source=src,
            output=dst,
            original_size=original_size,
            final_size=dst.stat().st_size,
            target_size=target_size,
            profile="copy",
            attempts=0,
            ok=True,
            message="файл уже укладывался в целевой размер",
            seconds=time.monotonic() - started,
            log=messages,
        )

    gs_bin = require_ghostscript()
    profiles = _ladder(allow_grayscale, min_dpi)
    attempts = 0
    best_fit: Attempt | None = None  # лучшее качество среди подходящих
    smallest: Attempt | None = None  # самый маленький результат вообще

    with tempfile.TemporaryDirectory(prefix="pdfshrink-", dir=str(dst.parent)) as tmpdir:
        tmp = Path(tmpdir)

        def consider(attempt: Attempt) -> None:
            nonlocal best_fit, smallest
            if smallest is None or attempt.size < smallest.size:
                smallest = attempt
            if attempt.size <= target_size and (
                best_fit is None
                or _quality_index(attempt, profiles) < _quality_index(best_fit, profiles)
            ):
                best_fit = attempt

        # Шаг 2 — попытка без потерь.
        lossless_path = tmp / "lossless.pdf"
        t0 = time.monotonic()
        lossless_size = run_lossless(src, lossless_path, timeout=timeout)
        if lossless_size:
            attempts += 1
            attempt = Attempt(None, lossless_size, lossless_path, time.monotonic() - t0)
            emit(
                f"  lossless (qpdf): {format_size(lossless_size)}"
                + ("  ✓ подходит" if lossless_size <= target_size else "")
            )
            consider(attempt)
            if lossless_size <= target_size:
                _finalize(lossless_path, dst)
                return _result(
                    src, dst, original_size, target_size, "lossless", attempts,
                    True, "сжато без потери качества (qpdf)", started, messages,
                )

        # Шаг 3 — бинарный поиск по лестнице качества.
        lo, hi = 0, len(profiles) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            profile = profiles[mid]
            candidate = tmp / f"try-{mid}.pdf"
            t0 = time.monotonic()
            size = run_ghostscript(src, candidate, profile, timeout=timeout, gs_bin=gs_bin)
            attempts += 1
            fits = size <= target_size
            emit(
                f"  {profile.describe()}: {format_size(size)}"
                + ("  ✓ подходит" if fits else "  ✗ велик")
            )
            consider(Attempt(profile, size, candidate, time.monotonic() - t0))
            if fits:
                hi = mid - 1  # пробуем качество получше
            else:
                lo = mid + 1  # нужно сжимать сильнее

        if best_fit is not None:
            _finalize(best_fit.path, dst)
            return _result(
                src, dst, original_size, target_size, best_fit.label, attempts,
                True, f"уложились в цель на профиле {best_fit.label}", started, messages,
            )

        # Цель не достигнута: отдаём самый маленький из полученных вариантов.
        if smallest is not None and smallest.size < original_size:
            _finalize(smallest.path, dst)
            msg = (
                f"не удалось уложиться в {format_size(target_size)}; "
                f"сохранён лучший результат {format_size(smallest.size)} "
                f"(профиль {smallest.label})"
            )
        else:
            _finalize(src, dst)
            msg = (
                f"сжатие не дало выигрыша; файл скопирован как есть "
                f"({format_size(original_size)})"
            )
        emit(msg)
        hint = "Подсказка: попробуйте --allow-grayscale или меньший --target-size."
        emit(hint)
        return _result(
            src, dst, original_size, target_size,
            smallest.label if smallest else "copy", attempts,
            False, msg, started, messages,
        )


def _quality_index(attempt: Attempt, profiles: Sequence[Profile]) -> int:
    """Чем меньше индекс, тем выше качество (lossless = -1)."""
    if attempt.profile is None:
        return -1
    return profiles.index(attempt.profile)


def _finalize(tmp_file: Path, dst: Path) -> None:
    """Атомарно переносит результат на место назначения."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = dst.with_name(dst.name + ".pdfshrink.tmp")
    shutil.copyfile(tmp_file, staging)
    os.replace(staging, dst)


def _result(
    src: Path,
    dst: Path,
    original_size: int,
    target_size: int,
    profile: str,
    attempts: int,
    ok: bool,
    message: str,
    started: float,
    messages: list[str],
) -> Result:
    return Result(
        source=src,
        output=dst,
        original_size=original_size,
        final_size=dst.stat().st_size,
        target_size=target_size,
        profile=profile,
        attempts=attempts,
        ok=ok,
        message=message,
        seconds=time.monotonic() - started,
        log=messages,
    )


def iter_pdf_files(paths: Iterable[str | os.PathLike[str]], recursive: bool = False) -> list[Path]:
    """Разворачивает список путей (файлы + каталоги) в список PDF-файлов."""
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            files.extend(sorted(p for p in path.glob(pattern) if p.is_file()))
        else:
            files.append(path)
    # убираем дубликаты, сохраняя порядок
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        key = f.absolute()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
