"""Tests for DTUG IDE editor utilities."""

from dtug_ide.utils import ensure_dir, write_file_text, read_file_text
from pathlib import Path
import tempfile


def test_ensure_dir(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b" / "c"
    ensure_dir(d)
    assert d.is_dir()


def test_write_read_file_text(tmp_path: Path) -> None:
    p = tmp_path / "test.txt"
    write_file_text(p, "hello")
    assert read_file_text(p) == "hello"
