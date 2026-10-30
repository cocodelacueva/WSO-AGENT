"""Tests de las tools de archivos: read_file, write_file, edit_file."""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.tools.filesystem import (
    DEFAULT_READ_LINES,
    apply_edit,
    edit_file,
    read_file,
    resolve_path,
    write_file,
)


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada test corre con el cwd (= proyecto) en un tmp."""
    monkeypatch.chdir(tmp_path)


class TestResolvePath:
    def test_relative_resolves_against_cwd(self, tmp_path: Path) -> None:
        assert resolve_path("src/a.py") == tmp_path.resolve() / "src" / "a.py"

    def test_absolute_kept(self, tmp_path: Path) -> None:
        assert resolve_path(str(tmp_path / "x")) == (tmp_path / "x").resolve()


class TestReadFile:
    def test_numbers_lines(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("uno\ndos\n", encoding="utf-8")
        assert read_file("a.py") == "    1\tuno\n    2\tdos"

    def test_default_limit_and_continuation_hint(self, tmp_path: Path) -> None:
        lines = [f"l{i}" for i in range(1, DEFAULT_READ_LINES + 51)]
        (tmp_path / "big.txt").write_text("\n".join(lines), encoding="utf-8")
        out = read_file("big.txt")
        assert f"l{DEFAULT_READ_LINES}" in out
        assert f"l{DEFAULT_READ_LINES + 1}\n" not in out
        assert f"offset={DEFAULT_READ_LINES + 1}" in out

    def test_offset_and_limit(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
        out = read_file("f.txt", offset=2, limit=2)
        assert out.splitlines()[:2] == ["    2\tb", "    3\tc"]
        assert "líneas 2-3 de 4" in out
        assert "offset=4" in out

    def test_offset_past_end_raises(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
        with pytest.raises(ValueError, match="supera"):
            read_file("f.txt", offset=10)

    def test_long_lines_clipped(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("x" * 2000, encoding="utf-8")
        assert "recortada" in read_file("f.txt")

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "e.txt").write_text("", encoding="utf-8")
        assert "vacío" in read_file("e.txt")

    def test_missing_file_suggests_search(self) -> None:
        with pytest.raises(FileNotFoundError, match="glob o grep"):
            read_file("nope.txt")

    def test_directory_raises(self, tmp_path: Path) -> None:
        (tmp_path / "d").mkdir()
        with pytest.raises(IsADirectoryError):
            read_file("d")

    def test_binary_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
        with pytest.raises(ValueError, match="binario"):
            read_file("b.bin")

    def test_unicode(self, tmp_path: Path) -> None:
        (tmp_path / "s.txt").write_text("Café — ¿qué tal?", encoding="utf-8")
        assert "Café — ¿qué tal?" in read_file("s.txt")


class TestWriteFile:
    def test_creates_file_and_parents(self, tmp_path: Path) -> None:
        result = write_file("a/b/new.py", "x = 1\n")
        assert (tmp_path / "a/b/new.py").read_text(encoding="utf-8") == "x = 1\n"
        assert result.startswith("Creado a/b/new.py")

    def test_overwrites(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("viejo", encoding="utf-8")
        assert write_file("f.txt", "nuevo").startswith("Sobrescrito")
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "nuevo"

    def test_directory_raises(self, tmp_path: Path) -> None:
        (tmp_path / "d").mkdir()
        with pytest.raises(IsADirectoryError):
            write_file("d", "x")


class TestApplyEdit:
    def test_single_replacement(self) -> None:
        assert apply_edit("a = 1\nb = 2\n", "b = 2", "b = 3", False) == "a = 1\nb = 3\n"

    def test_not_found_raises(self) -> None:
        with pytest.raises(ValueError, match="no aparece"):
            apply_edit("a = 1", "zzz", "y", False)

    def test_ambiguous_requires_replace_all(self) -> None:
        with pytest.raises(ValueError, match="2 veces"):
            apply_edit("x\nx\n", "x", "y", False)
        assert apply_edit("x\nx\n", "x", "y", True) == "y\ny\n"

    def test_identical_strings_rejected(self) -> None:
        with pytest.raises(ValueError, match="iguales"):
            apply_edit("x", "x", "x", False)

    def test_empty_old_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="vacío"):
            apply_edit("x", "", "y", False)

    def test_tolerates_surrounding_whitespace(self) -> None:
        # Modelos chicos suelen agregar saltos de línea en los bordes.
        text = "def f():\n    return 1\n"
        assert apply_edit(text, "\n    return 1\n\n", "    return 2", False) == (
            "def f():\n    return 2\n"
        )


class TestEditFile:
    def test_edits_and_shows_snippet(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        result = edit_file("m.py", "b = 2", "b = 20")
        assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"
        assert "    2\tb = 20" in result

    def test_failed_edit_leaves_file_untouched(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
        with pytest.raises(ValueError):
            edit_file("m.py", "nope", "x")
        assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\n"

    def test_alias_file_path(self, tmp_path: Path) -> None:
        from wso.tools.base import get_tool_definition

        (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
        defn = get_tool_definition(edit_file)
        assert defn is not None
        defn.validate_and_call({"file_path": "m.py", "old_string": "1", "new_string": "2"})
        assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 2\n"
