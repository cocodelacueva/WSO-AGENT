"""Tests del PermissionManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from wso.permissions.manager import (
    AlwaysAllowRule,
    PermissionDecision,
    PermissionManager,
)
from wso.tools.registry import load_builtin_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeSettings:
    """Settings sintético apuntando a un tmp_path."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._context_dir = root / "workspace/context"
        self._automations_dir = root / "workspace/automations"
        self._output_dir = root / "workspace/output"
        self._permissions_file = root / "config/permissions.toml"
        for d in [self._context_dir, self._automations_dir, self._output_dir]:
            d.mkdir(parents=True, exist_ok=True)

    @property
    def context_dir(self) -> Path:
        return self._context_dir

    @property
    def automations_dir(self) -> Path:
        return self._automations_dir

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def permissions_file(self) -> Path:
        return self._permissions_file


@pytest.fixture
def settings(tmp_path: Path) -> FakeSettings:
    return FakeSettings(tmp_path)


@pytest.fixture
def manager(settings: FakeSettings) -> PermissionManager:
    return PermissionManager(settings=settings)  # type: ignore[arg-type]


@pytest.fixture
def registry():
    return load_builtin_tools()


# ---------------------------------------------------------------------------
# Reglas básicas: FLOW, READ whitelist, WRITE/DELETE
# ---------------------------------------------------------------------------


class TestBasicRules:
    def test_flow_tools_always_auto_approved(
        self, manager: PermissionManager, registry
    ) -> None:
        for name in ["responder_al_usuario", "preguntar_al_usuario"]:
            tool = registry.get(name)
            decision = manager.check(tool, {"mensaje": "hola"})
            assert decision == PermissionDecision.AUTO_APPROVED

    def test_read_inside_whitelist_auto_approved(
        self,
        manager: PermissionManager,
        registry,
        settings: FakeSettings,
    ) -> None:
        target = settings.output_dir / "report.md"
        target.write_text("x")

        decision = manager.check(registry.get("read_file"), {"path": str(target)})
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_read_outside_whitelist_needs_approval(
        self,
        manager: PermissionManager,
        registry,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path / "private.txt"
        outside.write_text("x")

        decision = manager.check(
            registry.get("read_file"), {"path": str(outside)}
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_write_always_needs_approval_initially(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        decision = manager.check(
            registry.get("write_file"),
            {"path": str(settings.output_dir / "new.txt"), "content": "x"},
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_delete_always_needs_approval_initially(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        decision = manager.check(
            registry.get("delete_file"), {"path": str(settings.output_dir / "x.txt")}
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_list_directory_inside_whitelist_auto_approved(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        decision = manager.check(
            registry.get("list_directory"), {"path": str(settings.output_dir)}
        )
        assert decision == PermissionDecision.AUTO_APPROVED


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


class TestPathNormalization:
    def test_relative_path_components_resolved(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        # Path con `../` que termina dentro del whitelist
        sub = settings.output_dir / "sub"
        sub.mkdir()
        weird_path = str(sub / ".." / "file.txt")

        decision = manager.check(registry.get("read_file"), {"path": weird_path})
        assert decision == PermissionDecision.AUTO_APPROVED


# ---------------------------------------------------------------------------
# Session allows
# ---------------------------------------------------------------------------


class TestSessionAllows:
    def test_remember_session_approves_exact_match(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        args = {"path": str(settings.output_dir / "x.txt"), "content": "data"}

        manager.remember_session("write_file", args)

        decision = manager.check(registry.get("write_file"), args)
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_session_allows_does_not_match_different_args(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        args1 = {"path": str(settings.output_dir / "x.txt"), "content": "v1"}
        args2 = {"path": str(settings.output_dir / "x.txt"), "content": "v2"}

        manager.remember_session("write_file", args1)

        decision = manager.check(registry.get("write_file"), args2)
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_session_allows_does_not_persist_across_managers(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        args = {"path": str(settings.output_dir / "x.txt"), "content": "v"}
        manager.remember_session("write_file", args)

        # Nuevo manager (simula cierre y reapertura de sesión)
        manager2 = PermissionManager(settings=settings)  # type: ignore[arg-type]
        decision = manager2.check(registry.get("write_file"), args)
        assert decision == PermissionDecision.NEEDS_APPROVAL


# ---------------------------------------------------------------------------
# Always allows + persistencia TOML
# ---------------------------------------------------------------------------


class TestAlwaysAllows:
    def test_remember_always_creates_pattern_for_path_tools(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        args = {
            "path": str(settings.output_dir / "q3.pptx"),
            "content": "data",
        }
        manager.remember_always("write_file", args)

        rules = manager.always_allow_rules
        assert len(rules) == 1
        assert rules[0].tool == "write_file"
        assert rules[0].path_pattern is not None
        assert rules[0].path_pattern.endswith("/*")

    def test_remember_always_persists_to_toml(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        manager.remember_always(
            "write_file",
            {"path": str(settings.output_dir / "x.txt"), "content": "v"},
        )

        assert settings.permissions_file.exists()
        content = settings.permissions_file.read_text()
        assert "[[allow]]" in content
        assert 'tool = "write_file"' in content

    def test_always_allow_loads_from_toml_on_startup(
        self, settings: FakeSettings, registry
    ) -> None:
        # Pre-poblar el TOML antes de crear el manager
        settings.permissions_file.parent.mkdir(parents=True, exist_ok=True)
        settings.permissions_file.write_text(
            f"""[[allow]]
tool = "write_file"
path_pattern = "{settings.output_dir}/*"
"""
        )
        manager = PermissionManager(settings=settings)  # type: ignore[arg-type]

        decision = manager.check(
            registry.get("write_file"),
            {
                "path": str(settings.output_dir / "any_file.txt"),
                "content": "v",
            },
        )
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_always_allow_pattern_does_not_match_outside_dir(
        self, settings: FakeSettings, registry
    ) -> None:
        settings.permissions_file.parent.mkdir(parents=True, exist_ok=True)
        settings.permissions_file.write_text(
            f"""[[allow]]
tool = "write_file"
path_pattern = "{settings.output_dir}/*"
"""
        )
        manager = PermissionManager(settings=settings)  # type: ignore[arg-type]

        decision = manager.check(
            registry.get("write_file"),
            {"path": "/etc/passwd", "content": "evil"},
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_corrupt_toml_is_ignored_silently(
        self, settings: FakeSettings, registry
    ) -> None:
        settings.permissions_file.parent.mkdir(parents=True, exist_ok=True)
        settings.permissions_file.write_text("this is not valid TOML [[[")

        # No debería crashear
        manager = PermissionManager(settings=settings)  # type: ignore[arg-type]
        assert manager.always_allow_rules == []

    def test_remember_always_also_applies_to_current_session(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        # Misma instancia de manager, mismo path → debe estar aprobado inmediatamente
        args = {
            "path": str(settings.output_dir / "exact.txt"),
            "content": "v",
        }
        manager.remember_always("write_file", args)

        decision = manager.check(registry.get("write_file"), args)
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_remember_always_does_not_duplicate(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        args = {
            "path": str(settings.output_dir / "x.txt"),
            "content": "v",
        }
        manager.remember_always("write_file", args)
        manager.remember_always("write_file", args)

        # La regla debería existir solo una vez
        matching = [r for r in manager.always_allow_rules if r.tool == "write_file"]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# AlwaysAllowRule.matches en aislamiento
# ---------------------------------------------------------------------------


class TestAlwaysAllowRule:
    def test_matches_tool_name(self) -> None:
        rule = AlwaysAllowRule(tool="write_file", path_pattern=None)
        assert rule.matches("write_file", {})
        assert not rule.matches("delete_file", {})

    def test_matches_with_path_pattern(self, tmp_path: Path) -> None:
        rule = AlwaysAllowRule(
            tool="write_file", path_pattern=str(tmp_path / "*")
        )
        assert rule.matches("write_file", {"path": str(tmp_path / "a.txt")})
        assert not rule.matches("write_file", {"path": "/other/dir/a.txt"})

    def test_pattern_none_matches_any_args(self) -> None:
        rule = AlwaysAllowRule(tool="ping", path_pattern=None)
        assert rule.matches("ping", {})
        assert rule.matches("ping", {"x": "y"})

    def test_pattern_set_but_no_path_arg_does_not_match(self) -> None:
        rule = AlwaysAllowRule(tool="ping", path_pattern="/*")
        assert not rule.matches("ping", {"other": "value"})


# ---------------------------------------------------------------------------
# Browser sticky (v0.3)
# ---------------------------------------------------------------------------


class TestBrowserSticky:
    def test_browser_nav_needs_approval_initially(
        self, manager: PermissionManager, registry
    ) -> None:
        decision = manager.check(
            registry.get("browser_navigate"),
            {"url": "https://www.linkedin.com/sales/"},
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_remember_browser_session_approves_same_domain(
        self, manager: PermissionManager, registry
    ) -> None:
        manager.remember_browser_session(
            "browser_navigate", {"url": "https://www.linkedin.com/sales/home"}
        )
        # Otra URL del mismo dominio (sin www) → auto
        decision = manager.check(
            registry.get("browser_navigate"),
            {"url": "https://linkedin.com/sales/lead/123"},
        )
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_browser_sticky_covers_subdomains(
        self, manager: PermissionManager, registry
    ) -> None:
        manager.remember_browser_session(
            "browser_navigate", {"url": "https://linkedin.com/feed"}
        )
        decision = manager.check(
            registry.get("browser_navigate"),
            {"url": "https://www.linkedin.com/in/someone"},
        )
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_open_tab_and_navigate_share_group(
        self, manager: PermissionManager, registry
    ) -> None:
        # Aprobar open_tab a un dominio cubre navigate al mismo dominio
        manager.remember_browser_session(
            "browser_open_tab", {"url": "https://example.com/a"}
        )
        decision = manager.check(
            registry.get("browser_navigate"), {"url": "https://example.com/b"}
        )
        assert decision == PermissionDecision.AUTO_APPROVED

    def test_browser_sticky_does_not_leak_to_other_domain(
        self, manager: PermissionManager, registry
    ) -> None:
        manager.remember_browser_session(
            "browser_navigate", {"url": "https://linkedin.com/sales/"}
        )
        decision = manager.check(
            registry.get("browser_navigate"), {"url": "https://evil.com/"}
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_browser_action_without_url_needs_approval(
        self, manager: PermissionManager, registry
    ) -> None:
        # click no tiene url: aun con sticky de un dominio, sigue pidiendo aprobación
        manager.remember_browser_session(
            "browser_navigate", {"url": "https://linkedin.com/"}
        )
        decision = manager.check(
            registry.get("browser_click"), {"selector": "button.connect"}
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_remember_browser_session_action_falls_back_to_exact_args(
        self, manager: PermissionManager, registry
    ) -> None:
        args = {"selector": "button.connect"}
        manager.remember_browser_session("browser_click", args)
        # Mismo selector → auto (exact-args session)
        assert (
            manager.check(registry.get("browser_click"), args)
            == PermissionDecision.AUTO_APPROVED
        )
        # Otro selector → sigue pidiendo
        assert (
            manager.check(registry.get("browser_click"), {"selector": ".other"})
            == PermissionDecision.NEEDS_APPROVAL
        )

    def test_browser_sticky_does_not_persist_across_managers(
        self, manager: PermissionManager, registry, settings: FakeSettings
    ) -> None:
        manager.remember_browser_session(
            "browser_navigate", {"url": "https://linkedin.com/"}
        )
        manager2 = PermissionManager(settings=settings)  # type: ignore[arg-type]
        decision = manager2.check(
            registry.get("browser_navigate"), {"url": "https://linkedin.com/feed"}
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL

    def test_browser_screenshot_path_outside_whitelist_needs_approval(
        self, manager: PermissionManager, registry, tmp_path: Path
    ) -> None:
        # READ category, pero el path se valida como escritura → fuera de
        # whitelist pide aprobación.
        decision = manager.check(
            registry.get("browser_screenshot"),
            {"output_path": str(tmp_path / "out" / "shot.png")},
        )
        assert decision == PermissionDecision.NEEDS_APPROVAL
