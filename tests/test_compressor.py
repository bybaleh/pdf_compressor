"""Тесты ядра pdfshrink."""

from __future__ import annotations

import pytest

from pdfshrink import compressor as C


# --------------------------- разбор размеров ------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10MB", 10_000_000),
        ("10 mb", 10_000_000),
        ("10мб", 10_000_000),
        ("9.5MiB", int(9.5 * 1024 * 1024)),
        ("9,5MiB", int(9.5 * 1024 * 1024)),
        ("800k", 800_000),
        ("1024", 1024),
        (5_000_000, 5_000_000),
    ],
)
def test_parse_size(value, expected):
    assert C.parse_size(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "-5MB", "0", "10 попугаев"])
def test_parse_size_invalid(value):
    with pytest.raises(ValueError):
        C.parse_size(value)


def test_format_size():
    assert C.format_size(999) == "999 B"
    assert C.format_size(10_000_000) == "10.00 MB"
    assert C.format_size(1500) == "1.50 kB"


# ------------------------------ сценарии ----------------------------------- #


def test_small_file_is_copied(fake_tools, make_pdf, tmp_path):
    src = make_pdf("small.pdf", 50_000)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=10 * C.MB)
    assert res.ok and res.profile == "copy" and res.attempts == 0
    assert dst.read_bytes() == src.read_bytes()


def test_lossless_is_enough(fake_tools, make_pdf, tmp_path):
    # Файл всего на 2% больше цели — хватает qpdf (-5%), Ghostscript не нужен.
    target = 1_000_000
    src = make_pdf("slightly-big.pdf", int(target * 1.02))
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=target)
    assert res.ok and res.profile == "lossless" and res.attempts == 1
    assert dst.stat().st_size <= target


def test_binary_search_picks_best_quality(fake_gs_only, make_pdf, tmp_path):
    """Должен быть выбран самый качественный профиль, который влезает в цель."""
    src = make_pdf("big.pdf", 40 * C.MB)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=10 * C.MB)

    assert res.ok
    assert dst.stat().st_size <= 10 * C.MB
    # Заглушка: size = original * (dpi/300)^2 → 40 МБ влезает при dpi <= 150.
    assert res.profile == "ebook-150"
    # Бинарный поиск по 8 профилям — не больше 3 запусков gs.
    assert res.attempts <= 3


def test_target_missed_keeps_smallest(fake_gs_only, make_pdf, tmp_path):
    src = make_pdf("huge.pdf", 400 * C.MB)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=1 * C.MB)
    assert res.ok is False
    assert "не удалось уложиться" in res.message
    # Всё равно отдаём лучший найденный вариант (профиль с минимальным dpi).
    assert res.profile == "screen-50"
    assert dst.stat().st_size < src.stat().st_size


def test_grayscale_ladder_helps(fake_gs_only, make_pdf, tmp_path):
    src = make_pdf("huge.pdf", 400 * C.MB)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=6 * C.MB, allow_grayscale=True)
    assert res.ok
    assert res.profile.startswith("gray-")


def test_min_dpi_is_respected(fake_gs_only, make_pdf, tmp_path):
    src = make_pdf("big.pdf", 40 * C.MB)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=10 * C.MB, min_dpi=200)
    # При 200 dpi 40 МБ не влезают в 10 МБ, зато качество не упало ниже порога.
    assert res.ok is False
    assert res.profile in {"ebook-200", "printer-250", "prepress-300"}


def test_force_recompresses_small_file(fake_tools, make_pdf, tmp_path):
    src = make_pdf("small.pdf", 2 * C.MB)
    dst = tmp_path / "out.pdf"
    res = C.compress_pdf(src, dst, target_size=10 * C.MB, force=True)
    assert res.attempts >= 1
    assert res.profile != "copy"


def test_missing_ghostscript(monkeypatch, make_pdf, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    src = make_pdf("big.pdf", 20 * C.MB)
    with pytest.raises(C.CompressionError, match="Ghostscript"):
        C.compress_pdf(src, tmp_path / "out.pdf", target_size=10 * C.MB)


def test_broken_ghostscript_raises(broken_gs, make_pdf, tmp_path):
    src = make_pdf("big.pdf", 20 * C.MB)
    with pytest.raises(C.CompressionError, match="код"):
        C.compress_pdf(src, tmp_path / "out.pdf", target_size=10 * C.MB)


def test_not_a_pdf(fake_tools, tmp_path):
    bad = tmp_path / "fake.pdf"
    bad.write_bytes(b"hello world")
    with pytest.raises(C.CompressionError, match="не PDF"):
        C.compress_pdf(bad, tmp_path / "out.pdf")


def test_missing_file(fake_tools, tmp_path):
    with pytest.raises(C.CompressionError, match="не найден"):
        C.compress_pdf(tmp_path / "nope.pdf", tmp_path / "out.pdf")


def test_gs_command_contains_key_flags():
    cmd = C.build_gs_command("gs", "in.pdf", "out.pdf", C.LADDER[3])  # type: ignore[arg-type]
    joined = " ".join(cmd)
    assert "-sDEVICE=pdfwrite" in joined
    assert "-dColorImageResolution=150" in joined
    assert "setdistillerparams" in joined
    assert joined.index("-o") < joined.index("-f")


def test_iter_pdf_files(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.pdf").write_bytes(b"%PDF-1.7\n")
    (tmp_path / "note.txt").write_text("x")

    flat = C.iter_pdf_files([tmp_path])
    assert [p.name for p in flat] == ["a.pdf"]

    deep = C.iter_pdf_files([tmp_path], recursive=True)
    assert sorted(p.name for p in deep) == ["a.pdf", "b.pdf"]

    # дубликаты не повторяются
    dup = C.iter_pdf_files([tmp_path / "a.pdf", tmp_path / "a.pdf"])
    assert len(dup) == 1
