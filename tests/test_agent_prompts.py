"""Tests del system prompt y la carga de instrucciones del proyecto."""

from __future__ import annotations

from pathlib import Path

from wso.agent.prompts import (
    MAX_INSTRUCTIONS_CHARS,
    build_system_prompt,
    detect_git_branch,
    load_project_instructions,
)


class TestBuildSystemPrompt:
    def test_includes_environment(self, tmp_path: Path) -> None:
        prompt = build_system_prompt(tmp_path, git_branch="main")
        assert str(tmp_path) in prompt
        assert "rama: main" in prompt

    def test_no_git(self, tmp_path: Path) -> None:
        assert "No es un repo de git" in build_system_prompt(tmp_path)

    def test_instructions_section_only_when_present(self, tmp_path: Path) -> None:
        assert "Instrucciones del proyecto" not in build_system_prompt(tmp_path)
        prompt = build_system_prompt(tmp_path, instructions="Usá pytest -q.")
        assert "# Instrucciones del proyecto\nUsá pytest -q." in prompt

    def test_is_short(self, tmp_path: Path) -> None:
        # Con tools nativas el prompt no describe tools: tiene que ser chico
        # para dejar contexto libre en modelos locales.
        assert len(build_system_prompt(tmp_path)) < 3500

    def test_explains_how_the_turn_ends(self, tmp_path: Path) -> None:
        assert "sin llamar tools" in build_system_prompt(tmp_path)


class TestLoadProjectInstructions:
    def test_none(self, tmp_path: Path) -> None:
        assert load_project_instructions(tmp_path) == ("", None)

    def test_priority_wso_md_first(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (tmp_path / "WSO.md").write_text("wso", encoding="utf-8")
        text, path = load_project_instructions(tmp_path)
        assert text == "wso"
        assert path == tmp_path / "WSO.md"

    def test_falls_back_to_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
        assert load_project_instructions(tmp_path)[0] == "agents"

    def test_long_file_clipped(self, tmp_path: Path) -> None:
        (tmp_path / "WSO.md").write_text("x" * (MAX_INSTRUCTIONS_CHARS + 100), encoding="utf-8")
        text, _ = load_project_instructions(tmp_path)
        assert text.endswith("[… recortado]")


class TestDetectGitBranch:
    def test_not_a_repo(self, tmp_path: Path) -> None:
        assert detect_git_branch(tmp_path) is None
