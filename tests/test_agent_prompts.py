"""Tests del builder del system prompt."""

from __future__ import annotations

from pathlib import Path

from wso.agent.prompts import build_system_prompt, load_context_files
from wso.tools.base import PermissionCategory, tool
from wso.tools.registry import ToolRegistry, load_builtin_tools


class TestBuildSystemPrompt:
    def test_includes_all_v1_tools(self) -> None:
        registry = load_builtin_tools()
        prompt = build_system_prompt(registry)

        for tool_name in [
            "read_file",
            "write_file",
            "delete_file",
            "list_directory",
            "responder_al_usuario",
            "preguntar_al_usuario",
        ]:
            assert f"## {tool_name}" in prompt

    def test_includes_operation_rules(self) -> None:
        registry = load_builtin_tools()
        prompt = build_system_prompt(registry)

        # Las reglas clave que el modelo debe seguir
        assert "Reglas de operación" in prompt
        assert "thinking" in prompt
        assert "responder_al_usuario" in prompt
        assert "preguntar_al_usuario" in prompt
        assert "UNA sola acción por respuesta" in prompt

    def test_includes_few_shot_example(self) -> None:
        registry = load_builtin_tools()
        prompt = build_system_prompt(registry)

        assert "Ejemplo de un turno bien hecho" in prompt
        # El ejemplo debe mostrar el patrón thinking + tool + observation + thinking + tool
        assert prompt.count("<thinking>") >= 2
        assert "<observation" in prompt

    def test_context_section_with_no_context(self) -> None:
        registry = load_builtin_tools()
        prompt = build_system_prompt(registry, context="")

        assert "(sin contexto adicional cargado)" in prompt

    def test_context_section_with_provided_context(self) -> None:
        registry = load_builtin_tools()
        ctx = "Devs del estudio: María, Pedro, Juan."
        prompt = build_system_prompt(registry, context=ctx)

        assert ctx in prompt
        assert "(sin contexto adicional cargado)" not in prompt

    def test_empty_registry_does_not_crash(self) -> None:
        registry = ToolRegistry()
        prompt = build_system_prompt(registry)

        assert "Tools disponibles" in prompt
        # El registry vacío produce un placeholder
        assert "ninguna" in prompt.lower()

    def test_custom_tool_appears_in_prompt(self) -> None:
        @tool(
            name="my_custom",
            category=PermissionCategory.READ,
            description="Custom tool de test.",
            args_schema={"x": "input"},
        )
        def my_custom(x: str) -> str:
            return x

        registry = ToolRegistry()
        registry.register(my_custom._tool_def)  # type: ignore[attr-defined]
        prompt = build_system_prompt(registry)

        assert "## my_custom" in prompt
        assert "Custom tool de test." in prompt


class TestLoadContextFiles:
    def test_empty_dir_returns_empty_string(self, tmp_path: Path) -> None:
        result = load_context_files(tmp_path)
        assert result == ""

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        result = load_context_files(tmp_path / "nope")
        assert result == ""

    def test_loads_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "studio.md").write_text("Info del estudio.")
        (tmp_path / "devs.md").write_text("Lista de devs.")

        result = load_context_files(tmp_path)
        assert "Info del estudio." in result
        assert "Lista de devs." in result

    def test_files_get_header_with_filename(self, tmp_path: Path) -> None:
        (tmp_path / "x.md").write_text("contenido")

        result = load_context_files(tmp_path)
        assert "## x.md" in result

    def test_ignores_non_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("debería ignorar esto")
        (tmp_path / "studio.md").write_text("incluir esto")

        result = load_context_files(tmp_path)
        assert "incluir esto" in result
        assert "debería ignorar esto" not in result

    def test_files_loaded_in_sorted_order(self, tmp_path: Path) -> None:
        (tmp_path / "z_last.md").write_text("último")
        (tmp_path / "a_first.md").write_text("primero")

        result = load_context_files(tmp_path)
        first_pos = result.index("primero")
        last_pos = result.index("último")
        assert first_pos < last_pos

    def test_skips_empty_files(self, tmp_path: Path) -> None:
        (tmp_path / "empty.md").write_text("")
        (tmp_path / "real.md").write_text("contenido real")

        result = load_context_files(tmp_path)
        assert "## empty.md" not in result
        assert "contenido real" in result
