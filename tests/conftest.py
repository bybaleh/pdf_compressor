"""Фикстуры: подменяем Ghostscript и qpdf заглушками, чтобы тесты шли без них."""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

FAKE_GS = textwrap.dedent(
    """\
    #!SHEBANG
    # Заглушка Ghostscript: размер результата зависит от запрошенного dpi,
    # что позволяет проверить логику подбора профиля без настоящего gs.
    import sys
    from pathlib import Path

    args = sys.argv[1:]
    out = None
    src = None
    dpi = 300
    gray = False
    for i, a in enumerate(args):
        if a == "-o":
            out = args[i + 1]
        elif a == "-f":
            src = args[i + 1]
        elif a.startswith("-dColorImageResolution="):
            dpi = int(a.split("=")[1])
        elif a == "-sColorConversionStrategy=Gray":
            gray = True

    if out is None or src is None:
        sys.exit("fake gs: нет -o или -f")

    original = Path(src).stat().st_size
    factor = (dpi / 300.0) ** 2
    if gray:
        factor *= 0.5
    size = max(1200, int(original * factor))
    Path(out).write_bytes(b"%PDF-1.7\\n" + b"x" * (size - 9))
    """
)

FAKE_QPDF = textwrap.dedent(
    """\
    #!SHEBANG
    # Заглушка qpdf: lossless-сжатие «экономит» 5%.
    import sys
    from pathlib import Path

    paths = [a for a in sys.argv[1:] if not a.startswith("-")]
    src, out = Path(paths[0]), Path(paths[1])
    data = src.read_bytes()
    size = max(1000, int(len(data) * 0.95))
    out.write_bytes(b"%PDF-1.7\\n" + b"q" * (size - 9))
    """
)

FAKE_GS_BROKEN = textwrap.dedent(
    """\
    #!SHEBANG
    import sys
    print("fake gs: unrecoverable error", file=sys.stderr)
    sys.exit(1)
    """
)


def _install(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    # Запускаем заглушки тем же интерпретатором, что и тесты: PATH в тестах
    # может быть урезан, поэтому "/usr/bin/env python3" не годится.
    path.write_text(body.replace("#!SHEBANG", f"#!{sys.executable}", 1))
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """PATH с заглушками gs и qpdf."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install(bin_dir, "gs", FAKE_GS)
    _install(bin_dir, "qpdf", FAKE_QPDF)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GHOSTSCRIPT_BIN", raising=False)
    monkeypatch.delenv("QPDF_BIN", raising=False)
    return bin_dir


@pytest.fixture
def fake_gs_only(tmp_path, monkeypatch):
    """PATH только с gs (qpdf недоступен)."""
    bin_dir = tmp_path / "bin-gs"
    bin_dir.mkdir()
    _install(bin_dir, "gs", FAKE_GS)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("GHOSTSCRIPT_BIN", raising=False)
    monkeypatch.delenv("QPDF_BIN", raising=False)
    return bin_dir


@pytest.fixture
def broken_gs(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin-broken"
    bin_dir.mkdir()
    _install(bin_dir, "gs", FAKE_GS_BROKEN)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


@pytest.fixture
def make_pdf(tmp_path):
    """Создаёт псевдо-PDF заданного размера."""

    def _make(name: str, size: int) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\n" + b"a" * (size - 9))
        return path

    return _make
