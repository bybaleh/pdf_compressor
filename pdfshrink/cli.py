"""Командный интерфейс pdfshrink."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .compressor import (
    DEFAULT_TARGET_BYTES,
    DEFAULT_TIMEOUT,
    CompressionError,
    Result,
    compress_pdf,
    format_size,
    iter_pdf_files,
    parse_size,
)

EXIT_OK = 0
EXIT_TARGET_MISSED = 1
EXIT_ERROR = 2

EPILOG = """\
Примеры:
  pdfshrink doc.pdf                        # doc_compressed.pdf, цель 10 МБ
  pdfshrink doc.pdf -o small.pdf           # явное имя результата
  pdfshrink doc.pdf --target-size 5MB      # другая цель
  pdfshrink *.pdf --out-dir out            # пакетная обработка
  pdfshrink scans/ --recursive --inplace   # перезаписать файлы в каталоге
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfshrink",
        description="Сжимает PDF до заданного размера (по умолчанию 10 МБ) с помощью Ghostscript.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", metavar="PDF", help="PDF-файлы или каталоги с ними")
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="путь результата (только если на входе один файл)",
    )
    parser.add_argument(
        "-d", "--out-dir", metavar="DIR",
        help="каталог для результатов (по умолчанию — рядом с исходником)",
    )
    parser.add_argument(
        "-t", "--target-size", default="10MB", metavar="SIZE",
        help="целевой размер: 10MB, 9.5MiB, 800k, 5000000 (по умолчанию 10MB)",
    )
    parser.add_argument(
        "--suffix", default="_compressed", metavar="TEXT",
        help="суффикс имени результата (по умолчанию _compressed)",
    )
    parser.add_argument(
        "-i", "--inplace", action="store_true",
        help="перезаписать исходные файлы результатом",
    )
    parser.add_argument(
        "--allow-grayscale", action="store_true",
        help="разрешить перевод в оттенки серого, если иначе цель недостижима",
    )
    parser.add_argument(
        "--min-dpi", type=int, metavar="N",
        help="не опускать разрешение изображений ниже N dpi",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="пересжимать, даже если файл уже меньше цели",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="искать PDF в подкаталогах",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SEC",
        help=f"таймаут одного запуска Ghostscript, с (по умолчанию {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог попыток")
    parser.add_argument("-q", "--quiet", action="store_true", help="выводить только ошибки")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод (JSON)")
    parser.add_argument("--version", action="version", version=f"pdfshrink {__version__}")
    return parser


def _target_path(src: Path, args: argparse.Namespace, single: bool) -> Path:
    if args.inplace:
        return src
    if args.output and single:
        return Path(args.output).expanduser()
    name = f"{src.stem}{args.suffix}.pdf"
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else src.parent
    return out_dir / name


def _print_result(result: Result, quiet: bool) -> None:
    if quiet:
        return
    mark = "OK " if result.ok else "!! "
    print(
        f"{mark}{result.source.name}: "
        f"{format_size(result.original_size)} → {format_size(result.final_size)} "
        f"(-{result.ratio * 100:.1f}%, профиль {result.profile}, "
        f"{result.attempts} попыт., {result.seconds:.1f} c)"
    )
    print(f"   → {result.output}")
    if not result.ok:
        print(f"   {result.message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.quiet and args.verbose:
        parser.error("нельзя использовать --quiet вместе с --verbose")
    if args.output and args.inplace:
        parser.error("нельзя использовать --output вместе с --inplace")
    if args.output and args.out_dir:
        parser.error("нельзя использовать --output вместе с --out-dir")

    try:
        target = parse_size(args.target_size)
    except ValueError as exc:
        parser.error(str(exc))
        return EXIT_ERROR  # pragma: no cover

    files = iter_pdf_files(args.inputs, recursive=args.recursive)
    if not files:
        print("Не найдено ни одного PDF-файла.", file=sys.stderr)
        return EXIT_ERROR
    if args.output and len(files) > 1:
        parser.error("--output работает только с одним входным файлом; используйте --out-dir")

    single = len(files) == 1
    results: list[Result] = []
    failures: list[str] = []
    missed = False

    for src in files:
        dst = _target_path(src, args, single)
        logger = (lambda msg: print(msg)) if args.verbose else None
        try:
            result = compress_pdf(
                src,
                dst,
                target_size=target,
                allow_grayscale=args.allow_grayscale,
                min_dpi=args.min_dpi,
                force=args.force,
                timeout=args.timeout,
                log=logger,
            )
        except CompressionError as exc:
            failures.append(f"{src}: {exc}")
            print(f"ОШИБКА {src}: {exc}", file=sys.stderr)
            continue
        except KeyboardInterrupt:  # pragma: no cover
            print("\nПрервано пользователем.", file=sys.stderr)
            return EXIT_ERROR
        results.append(result)
        if not result.ok:
            missed = True
        if not args.json:
            _print_result(result, args.quiet)

    if args.json:
        payload = {
            "target_size": target,
            "results": [r.as_dict() for r in results],
            "errors": failures,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif len(results) > 1 and not args.quiet:
        saved = sum(r.original_size - r.final_size for r in results)
        print(f"\nИтого: {len(results)} файл(ов), сэкономлено {format_size(max(saved, 0))}")

    if failures:
        return EXIT_ERROR
    return EXIT_TARGET_MISSED if missed else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
