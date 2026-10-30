"""Tests de glob y grep."""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.tools import search
from wso.tools.search import glob, grep


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def load_user():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "util.py").write_text("LOAD = 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\nload_user docs\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("def load_user(): ...\n", encoding="utf-8")
    return tmp_path


class TestGlob:
    def test_pattern_without_slash_is_recursive(self, project: Path) -> None:
        assert set(glob("*.py").splitlines()) == {"src/app.py", "src/util.py"}

    def test_ignores_node_modules(self, project: Path) -> None:
        assert "node_modules" not in glob("**/*.py")

    def test_no_results(self, project: Path) -> None:
        assert "Sin resultados" in glob("*.rs")

    def test_not_a_directory(self, project: Path) -> None:
        with pytest.raises(NotADirectoryError):
            glob("*", path="README.md")


@pytest.fixture(params=["rg", "python"])
def grep_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Correr los tests de grep con ripgrep (si está) y con el fallback Python."""
    if request.param == "python":
        monkeypatch.setattr(search.shutil, "which", lambda _name: None)
    elif search.shutil.which("rg") is None:
        pytest.skip("ripgrep no instalado")
    return request.param


class TestGrep:
    def test_finds_matches_with_line_numbers(self, project: Path, grep_backend: str) -> None:
        out = grep("def load_user")
        assert out.splitlines() == ["src/app.py:1:def load_user():"]

    def test_include_filter(self, project: Path, grep_backend: str) -> None:
        out = grep("load_user", include="*.md")
        assert out.splitlines() == ["README.md:2:load_user docs"]

    def test_ignore_case(self, project: Path, grep_backend: str) -> None:
        assert "src/util.py:1:LOAD = 2" in grep("load =", ignore_case=True)

    def test_no_matches(self, project: Path, grep_backend: str) -> None:
        assert "Sin coincidencias" in grep("nada_de_esto")

    def test_invalid_regex(self, project: Path) -> None:
        with pytest.raises(ValueError, match="Regex inválida"):
            grep("(")

    def test_caps_results(self, project: Path, grep_backend: str) -> None:
        (project / "many.txt").write_text("hit\n" * 300, encoding="utf-8")
        out = grep("hit")
        assert "coincidencias más" in out


class TestGlobModelPatterns:
    """Patrones que usan los modelos chicos (sacados de logs reales)."""

    def test_dir_double_star(self, project: Path) -> None:
        (project / "arbolito").mkdir()
        (project / "arbolito" / "index.html").write_text("x")
        assert "arbolito/index.html" in glob("arbolito/**")

    def test_folder_name_lists_its_files(self, project: Path) -> None:
        (project / "arbolito").mkdir()
        (project / "arbolito" / "index.html").write_text("x")
        assert "arbolito/index.html" in glob("arbolito")

    def test_dotfile_pattern_keeps_dot(self, project: Path) -> None:
        (project / ".env").write_text("x")
        assert ".env" in glob(".env")


def test_grep_no_match_mentions_include_filter(project: Path) -> None:
    (project / "index.html").write_text("const deerLegs = [];\n")
    out = grep("deerLegs", include="*.js")
    assert "include" in out
    assert "deerLegs" in grep("deerLegs")
