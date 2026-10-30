"""Tests del ToolRegistry y load_builtin_tools."""

from __future__ import annotations

import types

import pytest

from wso.tools.base import PermissionCategory, ToolDefinition, tool
from wso.tools.registry import ToolRegistry, load_builtin_tools


def _make_def(name: str, category: PermissionCategory = PermissionCategory.READ) -> ToolDefinition:
    """Crear una ToolDefinition mínima para tests."""

    @tool(name=name, category=category, description=f"tool {name}")
    def handler(arg: str) -> str:
        return arg

    defn = handler._tool_def  # type: ignore[attr-defined]
    return defn


class TestToolRegistry:
    def test_empty_registry(self) -> None:
        registry = ToolRegistry()
        assert len(registry) == 0
        assert registry.all() == []
        assert registry.get("foo") is None
        assert "foo" not in registry

    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        defn = _make_def("foo")
        registry.register(defn)

        assert len(registry) == 1
        assert "foo" in registry
        assert registry.get("foo") is defn
        assert registry.all() == [defn]

    def test_duplicate_registration_raises(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_def("dup"))
        with pytest.raises(ValueError, match="ya registrada"):
            registry.register(_make_def("dup"))

    def test_get_unknown_returns_none(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_def("foo"))
        assert registry.get("bar") is None

    def test_register_module_scans_decorated_functions(self) -> None:
        # Crear un módulo sintético con dos tools y una función no-tool
        module = types.ModuleType("synthetic_tools")

        @tool(name="tool_a", category=PermissionCategory.READ, description="A")
        def tool_a(x: str) -> str:
            return x

        @tool(name="tool_b", category=PermissionCategory.WRITE, description="B")
        def tool_b(x: str) -> str:
            return x

        def not_a_tool(x: str) -> str:
            return x

        module.tool_a = tool_a
        module.tool_b = tool_b
        module.not_a_tool = not_a_tool
        module._private = lambda: None

        registry = ToolRegistry()
        count = registry.register_module(module)

        assert count == 2
        assert "tool_a" in registry
        assert "tool_b" in registry
        assert "not_a_tool" not in registry

    def test_register_module_skips_private_attrs(self) -> None:
        module = types.ModuleType("synth")

        @tool(name="public_tool", category=PermissionCategory.READ, description="x")
        def public_tool(x: str) -> str:
            return x

        # Adjuntar la misma tool con nombre privado para confirmar que se skipea
        module.public_tool = public_tool
        module._private_tool = public_tool

        registry = ToolRegistry()
        count = registry.register_module(module)
        assert count == 1

    def test_specs_empty(self) -> None:
        assert ToolRegistry().specs() == []

    def test_specs_in_registration_order(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_def("alpha"))
        registry.register(_make_def("beta"))
        assert [s.name for s in registry.specs()] == ["alpha", "beta"]


class TestLoadBuiltinTools:
    def test_loads_coding_tools(self) -> None:
        registry = load_builtin_tools()
        assert {t.name for t in registry.all()} == {
            "read_file", "write_file", "edit_file", "glob", "grep", "bash",
        }

    def test_categories_are_correct(self) -> None:
        registry = load_builtin_tools()
        cats = {t.name: t.category for t in registry.all()}
        assert cats["read_file"] == PermissionCategory.READ
        assert cats["glob"] == PermissionCategory.READ
        assert cats["grep"] == PermissionCategory.READ
        assert cats["write_file"] == PermissionCategory.WRITE
        assert cats["edit_file"] == PermissionCategory.WRITE
        assert cats["bash"] == PermissionCategory.EXECUTE

    def test_every_tool_has_valid_spec(self) -> None:
        for spec in load_builtin_tools().specs():
            assert spec.parameters["type"] == "object"
            assert spec.description

    def test_idempotent_creates_independent_registries(self) -> None:
        r1 = load_builtin_tools()
        r2 = load_builtin_tools()
        assert r1 is not r2
        assert len(r1) == len(r2) == 6
