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

    def test_to_prompt_section_empty(self) -> None:
        registry = ToolRegistry()
        section = registry.to_prompt_section()
        assert "ninguna" in section.lower()

    def test_to_prompt_section_combines_all(self) -> None:
        registry = ToolRegistry()
        registry.register(_make_def("alpha"))
        registry.register(_make_def("beta"))

        section = registry.to_prompt_section()
        assert "## alpha" in section
        assert "## beta" in section


class TestLoadBuiltinTools:
    def test_loads_all_builtin_tools(self) -> None:
        registry = load_builtin_tools()

        expected = {
            # filesystem
            "read_file",
            "write_file",
            "delete_file",
            "list_directory",
            # flow
            "responder_al_usuario",
            "preguntar_al_usuario",
            # code (v0.3)
            "run_python",
            # pptx (v0.2)
            "generate_pptx",
            "read_pptx",
            "edit_pptx_slide",
            "generate_pptx_from_template",
            # xlsx (v0.2)
            "generate_xlsx",
            "read_xlsx",
            "edit_xlsx_cell",
            "append_xlsx_rows",
            # pdf / docx (v0.2)
            "read_pdf",
            "read_docx",
            # browser (v0.3)
            "browser_open_tab",
            "browser_navigate",
            "browser_close_tab",
            "browser_read_page",
            "browser_screenshot",
            "browser_click",
            "browser_type",
            "browser_wait_for",
        }
        actual = {t.name for t in registry.all()}
        assert actual == expected

    def test_categories_are_correct(self) -> None:
        registry = load_builtin_tools()

        assert registry.get("read_file").category == PermissionCategory.READ
        assert registry.get("list_directory").category == PermissionCategory.READ
        assert registry.get("write_file").category == PermissionCategory.WRITE
        assert registry.get("delete_file").category == PermissionCategory.DELETE
        assert registry.get("responder_al_usuario").category == PermissionCategory.FLOW
        assert registry.get("preguntar_al_usuario").category == PermissionCategory.FLOW
        # pptx tools
        assert registry.get("generate_pptx").category == PermissionCategory.WRITE
        assert registry.get("read_pptx").category == PermissionCategory.READ
        assert registry.get("edit_pptx_slide").category == PermissionCategory.WRITE
        assert registry.get("generate_pptx_from_template").category == PermissionCategory.WRITE
        # xlsx tools
        assert registry.get("generate_xlsx").category == PermissionCategory.WRITE
        assert registry.get("read_xlsx").category == PermissionCategory.READ
        assert registry.get("edit_xlsx_cell").category == PermissionCategory.WRITE
        assert registry.get("append_xlsx_rows").category == PermissionCategory.WRITE
        # pdf / docx tools
        assert registry.get("read_pdf").category == PermissionCategory.READ
        assert registry.get("read_docx").category == PermissionCategory.READ
        # browser tools (v0.3)
        assert registry.get("browser_open_tab").category == PermissionCategory.BROWSER
        assert registry.get("browser_navigate").category == PermissionCategory.BROWSER
        assert registry.get("browser_close_tab").category == PermissionCategory.BROWSER
        assert registry.get("browser_click").category == PermissionCategory.BROWSER
        assert registry.get("browser_type").category == PermissionCategory.BROWSER
        assert registry.get("browser_read_page").category == PermissionCategory.READ
        assert registry.get("browser_screenshot").category == PermissionCategory.READ
        assert registry.get("browser_wait_for").category == PermissionCategory.READ
        # code (v0.3)
        assert registry.get("run_python").category == PermissionCategory.EXECUTE

    def test_idempotent_creates_independent_registries(self) -> None:
        # Cada llamada devuelve un registry nuevo (no comparten estado)
        r1 = load_builtin_tools()
        r2 = load_builtin_tools()
        assert r1 is not r2
        assert len(r1) == len(r2) == 25
