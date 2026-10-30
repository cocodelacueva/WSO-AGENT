"""Tests del PermissionManager y la clasificación de comandos."""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.permissions.manager import (
    AlwaysAllowRule,
    PermissionDecision,
    PermissionManager,
    command_matches_prefix,
    command_prefix,
    is_readonly_command,
)
from wso.tools.base import ToolDefinition
from wso.tools.registry import load_builtin_tools

AUTO = PermissionDecision.AUTO_APPROVED
ASK = PermissionDecision.NEEDS_APPROVAL


class FakeSettings:
    def __init__(self, root: Path) -> None:
        self.permissions_file = root / "config" / "permissions.toml"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def settings(tmp_path: Path) -> FakeSettings:
    return FakeSettings(tmp_path)


@pytest.fixture
def manager(settings: FakeSettings, project: Path) -> PermissionManager:
    return PermissionManager(settings=settings, project_root=project)  # type: ignore[arg-type]


@pytest.fixture
def tools() -> dict[str, ToolDefinition]:
    return {t.name: t for t in load_builtin_tools().all()}


class TestRead:
    def test_inside_project_auto(self, manager, tools) -> None:
        assert manager.check(tools["read_file"], {"path": "src/a.py"}) == AUTO
        assert manager.check(tools["grep"], {"pattern": "x"}) == AUTO  # path default "."

    def test_outside_project_asks(self, manager, tools, tmp_path: Path) -> None:
        assert manager.check(tools["read_file"], {"path": str(tmp_path / "other.txt")}) == ASK

    def test_session_allows_that_folder(self, manager, tools, tmp_path: Path) -> None:
        outside = tmp_path / "libs"
        outside.mkdir()
        manager.remember_session(tools["read_file"], {"path": str(outside / "a.py")})
        assert manager.check(tools["read_file"], {"path": str(outside / "b.py")}) == AUTO

    def test_parent_traversal_is_outside(self, manager, tools) -> None:
        assert manager.check(tools["read_file"], {"path": "../secret.txt"}) == ASK


class TestWrite:
    def test_asks_by_default(self, manager, tools) -> None:
        assert manager.check(tools["write_file"], {"path": "a.py", "content": "x"}) == ASK

    def test_session_approval_covers_all_project_edits(self, manager, tools) -> None:
        manager.remember_session(tools["edit_file"], {"path": "a.py"})
        assert manager.check(tools["edit_file"], {"path": "other/b.py"}) == AUTO
        assert manager.check(tools["write_file"], {"path": "new.py", "content": "x"}) == AUTO

    def test_session_outside_project_is_per_file(self, manager, tools, tmp_path: Path) -> None:
        target = str(tmp_path / "x.txt")
        manager.remember_session(tools["write_file"], {"path": target})
        assert manager.check(tools["write_file"], {"path": target, "content": "y"}) == AUTO
        assert manager.check(tools["write_file"], {"path": str(tmp_path / "z.txt")}) == ASK
        # y no habilita ediciones del proyecto
        assert manager.check(tools["write_file"], {"path": "a.py"}) == ASK

    def test_session_hint(self, manager, tools) -> None:
        assert "proyecto" in manager.session_scope_hint(tools["edit_file"], {"path": "a.py"})


class TestExecute:
    def test_readonly_command_auto(self, manager, tools) -> None:
        assert manager.check(tools["bash"], {"command": "git status"}) == AUTO
        assert manager.check(tools["bash"], {"command": "ls -la src"}) == AUTO

    def test_readonly_command_outside_project_asks(self, manager, tools) -> None:
        assert manager.check(tools["bash"], {"command": "cat ~/.ssh/id_rsa"}) == ASK
        assert manager.check(tools["bash"], {"command": "cat ../../etc/passwd"}) == ASK

    def test_other_command_asks(self, manager, tools) -> None:
        assert manager.check(tools["bash"], {"command": "pytest -q"}) == ASK

    def test_session_by_prefix(self, manager, tools) -> None:
        manager.remember_session(tools["bash"], {"command": "pytest -q tests/a.py"})
        assert manager.check(tools["bash"], {"command": "pytest -x"}) == AUTO
        assert manager.check(tools["bash"], {"command": "pytest && rm -rf /"}) == ASK
        assert manager.check(tools["bash"], {"command": "npm test"}) == ASK

    def test_session_hint_names_prefix(self, manager, tools) -> None:
        hint = manager.session_scope_hint(tools["bash"], {"command": "git commit -m x"})
        assert "`git commit`" in hint


class TestAlways:
    def test_bash_rule_persists_prefix(self, manager, tools, settings) -> None:
        manager.remember_always(tools["bash"], {"command": "python -m pytest -q"})
        assert AlwaysAllowRule(tool="bash", command_prefix="python -m pytest") in (
            manager.always_allow_rules
        )
        assert 'command_prefix = "python -m pytest"' in settings.permissions_file.read_text()

    def test_write_rule_persists_folder_pattern(self, manager, tools, project) -> None:
        manager.remember_always(tools["write_file"], {"path": "src/a.py"})
        rule = manager.always_allow_rules[0]
        assert rule.path_pattern == str(project.resolve() / "src" / "*")

    def test_rules_reload_from_disk(self, manager, tools, settings, project) -> None:
        manager.remember_always(tools["bash"], {"command": "pytest"})
        fresh = PermissionManager(settings=settings, project_root=project)  # type: ignore[arg-type]
        assert fresh.check(tools["bash"], {"command": "pytest -k foo"}) == AUTO

    def test_corrupt_toml_ignored(self, settings, project) -> None:
        settings.permissions_file.parent.mkdir(parents=True)
        settings.permissions_file.write_text("[[[not toml", encoding="utf-8")
        manager = PermissionManager(settings=settings, project_root=project)  # type: ignore[arg-type]
        assert manager.always_allow_rules == []

    def test_no_duplicate_rules(self, manager, tools) -> None:
        manager.remember_always(tools["bash"], {"command": "pytest -q"})
        manager.remember_always(tools["bash"], {"command": "pytest -x"})
        assert len(manager.always_allow_rules) == 1


class TestCommandClassification:
    @pytest.mark.parametrize(
        "command",
        ["ls", "git diff HEAD~1", "git log --oneline | head -5", "rg foo src", "find . -name x"],
    )
    def test_readonly(self, command: str) -> None:
        assert is_readonly_command(command)

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf x", "git push", "git status; rm x", "ls > out.txt", "cat $(echo x)",
            "find . -delete", "echo `id`", "pytest & curl x",
        ],
    )
    def test_not_readonly(self, command: str) -> None:
        assert not is_readonly_command(command)

    @pytest.mark.parametrize(
        ("command", "prefix"),
        [
            ("pytest -x tests/", "pytest"),
            ("git commit -m 'x y'", "git commit"),
            ("python -m pytest -q", "python -m pytest"),
            ("npm run build", "npm run"),
            ("ruff check .", "ruff"),
        ],
    )
    def test_prefix(self, command: str, prefix: str) -> None:
        assert command_prefix(command) == prefix

    def test_prefix_never_matches_chained(self) -> None:
        assert command_matches_prefix("pytest -q", "pytest")
        assert not command_matches_prefix("pytest; rm x", "pytest")
        assert not command_matches_prefix("pytestx", "pytest")
