"""Тесты командного интерфейса."""

from __future__ import annotations

import json

import pytest

from pdfshrink import cli
from pdfshrink.compressor import MB


def test_default_output_name(fake_tools, make_pdf, tmp_path, capsys):
    src = make_pdf("report.pdf", 40 * MB)
    code = cli.main([str(src), "--target-size", "10MB"])
    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "report_compressed.pdf").exists()
    assert "report.pdf" in out


def test_explicit_output_and_target(fake_tools, make_pdf, tmp_path):
    src = make_pdf("report.pdf", 40 * MB)
    dst = tmp_path / "out" / "tiny.pdf"
    code = cli.main([str(src), "-o", str(dst), "-t", "5MB"])
    assert code == 0
    assert dst.exists() and dst.stat().st_size <= 5 * MB


def test_inplace(fake_tools, make_pdf, tmp_path):
    src = make_pdf("report.pdf", 40 * MB)
    code = cli.main([str(src), "--inplace"])
    assert code == 0
    assert src.stat().st_size <= 10 * MB
    assert not (tmp_path / "report_compressed.pdf").exists()


def test_batch_with_out_dir(fake_tools, make_pdf, tmp_path):
    make_pdf("a.pdf", 20 * MB)
    make_pdf("b.pdf", 30 * MB)
    out_dir = tmp_path / "out"
    code = cli.main([str(tmp_path), "--out-dir", str(out_dir)])
    assert code == 0
    assert {p.name for p in out_dir.glob("*.pdf")} == {"a_compressed.pdf", "b_compressed.pdf"}


def test_json_output(fake_tools, make_pdf, tmp_path, capsys):
    src = make_pdf("report.pdf", 40 * MB)
    code = cli.main([str(src), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["target_size"] == 10 * MB
    assert payload["results"][0]["ok"] is True
    assert payload["results"][0]["final_size"] <= 10 * MB


def test_exit_code_when_target_missed(fake_gs_only, make_pdf, tmp_path):
    make_pdf("huge.pdf", 400 * MB)
    code = cli.main([str(tmp_path / "huge.pdf"), "-t", "1MB", "-q"])
    assert code == cli.EXIT_TARGET_MISSED


def test_exit_code_on_error(fake_tools, tmp_path, capsys):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    code = cli.main([str(bad)])
    assert code == cli.EXIT_ERROR
    assert "ОШИБКА" in capsys.readouterr().err


def test_no_files_found(fake_tools, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main([str(empty)]) == cli.EXIT_ERROR


def test_conflicting_flags(fake_tools, make_pdf):
    src = make_pdf("a.pdf", 1 * MB)
    with pytest.raises(SystemExit):
        cli.main([str(src), "-o", "x.pdf", "--inplace"])
    with pytest.raises(SystemExit):
        cli.main([str(src), "--quiet", "--verbose"])


def test_bad_target_size(fake_tools, make_pdf):
    src = make_pdf("a.pdf", 1 * MB)
    with pytest.raises(SystemExit):
        cli.main([str(src), "-t", "много"])


def test_verbose_logs_attempts(fake_gs_only, make_pdf, tmp_path, capsys):
    src = make_pdf("big.pdf", 40 * MB)
    cli.main([str(src), "-v"])
    out = capsys.readouterr().out
    assert "цель ≤ 10.00 MB" in out
    assert "dpi" in out
