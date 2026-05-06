"""Tests de las tools de filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.tools.filesystem import delete_file, list_directory, read_file, write_file


class TestReadFile:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "hello.txt"
        target.write_text("Hola mundo", encoding="utf-8")

        assert read_file(str(target)) == "Hola mundo"

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            read_file("relative/path.txt")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_file(str(tmp_path / "nope.txt"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_file(str(tmp_path))

    def test_unicode_content(self, tmp_path: Path) -> None:
        target = tmp_path / "spanish.txt"
        content = "Café — niño — ¿qué tal?"
        target.write_text(content, encoding="utf-8")

        assert read_file(str(target)) == content


class TestWriteFile:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        result = write_file(str(target), "contenido")

        assert target.exists()
        assert target.read_text(encoding="utf-8") == "contenido"
        assert "9" in result  # 9 caracteres

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "existing.txt"
        target.write_text("viejo", encoding="utf-8")

        write_file(str(target), "nuevo")
        assert target.read_text(encoding="utf-8") == "nuevo"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "deep.txt"
        write_file(str(target), "hola")

        assert target.exists()

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            write_file("rel.txt", "x")

    def test_writing_to_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            write_file(str(tmp_path), "x")


class TestDeleteFile:
    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "del.txt"
        target.write_text("x", encoding="utf-8")

        delete_file(str(target))
        assert not target.exists()

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            delete_file(str(tmp_path / "nope.txt"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            delete_file(str(tmp_path))

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            delete_file("rel.txt")


class TestListDirectory:
    def test_lists_files_and_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "file_a.txt").write_text("x")
        (tmp_path / "file_b.txt").write_text("y")
        (tmp_path / "subdir").mkdir()

        result = list_directory(str(tmp_path))

        assert "[F] file_a.txt" in result
        assert "[F] file_b.txt" in result
        assert "[D] subdir" in result

    def test_directories_listed_first(self, tmp_path: Path) -> None:
        (tmp_path / "z_file.txt").write_text("x")
        (tmp_path / "a_dir").mkdir()

        result = list_directory(str(tmp_path))
        lines = result.splitlines()

        # Encontrar las posiciones de cada entry
        dir_idx = next(i for i, line in enumerate(lines) if "a_dir" in line)
        file_idx = next(i for i, line in enumerate(lines) if "z_file.txt" in line)
        assert dir_idx < file_idx

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = list_directory(str(tmp_path))
        assert "vacío" in result

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            list_directory(str(tmp_path / "nope"))

    def test_file_path_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("x")

        with pytest.raises(NotADirectoryError):
            list_directory(str(target))

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absoluta"):
            list_directory("rel/")
