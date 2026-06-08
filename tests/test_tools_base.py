"""Tests del decorador @tool y ToolDefinition."""

from __future__ import annotations

import pytest

from wso.tools.base import (
    PermissionCategory,
    ToolValidationError,
    get_tool_definition,
    tool,
    truncate_with_notice,
)


class TestToolDecorator:
    def test_basic_registration(self) -> None:
        @tool(
            name="read_file",
            category=PermissionCategory.READ,
            description="Lee un archivo.",
            args_schema={"path": "ruta del archivo"},
        )
        def read_file(path: str) -> str:
            return f"<{path}>"

        defn = get_tool_definition(read_file)
        assert defn is not None
        assert defn.name == "read_file"
        assert defn.category == PermissionCategory.READ
        assert defn.description == "Lee un archivo."
        assert defn.args_schema == {"path": "ruta del archivo"}

    def test_function_remains_callable(self) -> None:
        @tool(name="echo", category=PermissionCategory.READ, description="x")
        def echo(msg: str) -> str:
            return msg

        # La función decorada se puede usar normal (importante para tests)
        assert echo("hola") == "hola"

    def test_args_schema_auto_generated_when_omitted(self) -> None:
        @tool(name="no_schema", category=PermissionCategory.READ, description="x")
        def no_schema(path: str, count: int) -> str:
            return path

        defn = get_tool_definition(no_schema)
        assert defn is not None
        assert defn.args_schema == {"path": "", "count": ""}

    def test_args_schema_mismatch_missing_keys(self) -> None:
        with pytest.raises(ValueError, match="Faltan"):

            @tool(
                name="bad",
                category=PermissionCategory.READ,
                description="x",
                args_schema={"path": "x"},  # falta 'count'
            )
            def bad(path: str, count: int) -> str:
                return path

    def test_args_schema_mismatch_extra_keys(self) -> None:
        with pytest.raises(ValueError, match="Sobran"):

            @tool(
                name="bad",
                category=PermissionCategory.READ,
                description="x",
                args_schema={"path": "x", "extra": "y"},
            )
            def bad(path: str) -> str:
                return path

    def test_missing_type_hint_rejected(self) -> None:
        with pytest.raises(TypeError, match="type hint"):

            @tool(name="no_hint", category=PermissionCategory.READ, description="x")
            def no_hint(arg) -> str:  # type: ignore[no-untyped-def]
                return ""

    def test_var_positional_rejected(self) -> None:
        with pytest.raises(TypeError, match=r"\*args ni \*\*kwargs"):

            @tool(name="varargs", category=PermissionCategory.READ, description="x")
            def varargs(*args: str) -> str:
                return ""

    def test_var_keyword_rejected(self) -> None:
        with pytest.raises(TypeError, match=r"\*args ni \*\*kwargs"):

            @tool(name="kwargs", category=PermissionCategory.READ, description="x")
            def kwargs_tool(**kw: str) -> str:
                return ""


class TestValidateAndCall:
    def test_valid_args_executes_handler(self) -> None:
        @tool(name="add", category=PermissionCategory.READ, description="x")
        def add(a: int, b: int) -> int:
            return a + b

        defn = get_tool_definition(add)
        assert defn is not None
        assert defn.validate_and_call({"a": 2, "b": 3}) == 5

    def test_missing_required_arg_raises(self) -> None:
        @tool(name="add", category=PermissionCategory.READ, description="x")
        def add(a: int, b: int) -> int:
            return a + b

        defn = get_tool_definition(add)
        assert defn is not None
        with pytest.raises(ToolValidationError) as exc_info:
            defn.validate_and_call({"a": 1})
        assert "add" in str(exc_info.value)
        assert "b" in str(exc_info.value)

    def test_wrong_type_raises(self) -> None:
        @tool(name="strlen", category=PermissionCategory.READ, description="x")
        def strlen(text: str) -> int:
            return len(text)

        defn = get_tool_definition(strlen)
        assert defn is not None
        # Pydantic puede coercer int a str en algunos casos; usamos un dict
        # para forzar el error de tipo.
        with pytest.raises(ToolValidationError):
            defn.validate_and_call({"text": {"not": "a string"}})

    def test_default_value_used_when_omitted(self) -> None:
        @tool(name="greet", category=PermissionCategory.READ, description="x")
        def greet(name: str, greeting: str = "Hola") -> str:
            return f"{greeting}, {name}"

        defn = get_tool_definition(greet)
        assert defn is not None
        assert defn.validate_and_call({"name": "Coco"}) == "Hola, Coco"
        assert defn.validate_and_call({"name": "Coco", "greeting": "Buen día"}) == "Buen día, Coco"


class TestPromptSection:
    def test_prompt_includes_all_fields(self) -> None:
        @tool(
            name="read_file",
            category=PermissionCategory.READ,
            description="Lee un archivo.",
            args_schema={"path": "ruta del archivo"},
        )
        def read_file(path: str) -> str:
            return path

        defn = get_tool_definition(read_file)
        assert defn is not None
        section = defn.to_prompt_section()

        assert "## read_file" in section
        assert "Lee un archivo." in section
        assert "Categoría: read" in section
        assert "path (str, requerido)" in section
        assert "ruta del archivo" in section
        assert '<tool name="read_file">' in section
        assert "<path>...</path>" in section

    def test_prompt_marks_optional_args(self) -> None:
        @tool(name="t", category=PermissionCategory.READ, description="x")
        def t(a: str, b: int = 5) -> str:
            return a

        defn = get_tool_definition(t)
        assert defn is not None
        section = defn.to_prompt_section()
        assert "a (str, requerido)" in section
        assert "b (int, opcional, default=5)" in section

    def test_prompt_for_tool_without_args(self) -> None:
        @tool(name="ping", category=PermissionCategory.READ, description="x")
        def ping() -> str:
            return "pong"

        defn = get_tool_definition(ping)
        assert defn is not None
        section = defn.to_prompt_section()
        assert "(ninguno)" in section
        assert '<tool name="ping" />' in section


class TestArgAliases:
    """Aliases de args: tolerar que el modelo use otro nombre de arg."""

    def _make(self):
        @tool(
            name="gen",
            category=PermissionCategory.WRITE,
            description="x",
            args_schema={"path": "ruta", "data_json": "json"},
            aliases={"data": "data_json", "payload": "data_json"},
        )
        def gen(path: str, data_json: str) -> str:
            return f"{path}|{data_json}"

        return gen

    def test_alias_is_remapped_on_call(self) -> None:
        gen = self._make()
        defn = get_tool_definition(gen)
        assert defn is not None
        # El modelo emite `data` en vez de `data_json`.
        out = defn.validate_and_call({"path": "/x", "data": "[]"})
        assert out == "/x|[]"

    def test_canonical_wins_over_alias(self) -> None:
        gen = self._make()
        defn = get_tool_definition(gen)
        assert defn is not None
        # Si vienen ambos, gana el canónico; el alias se ignora.
        out = defn.validate_and_call(
            {"path": "/x", "data_json": "CANON", "data": "ALIAS"}
        )
        assert out == "/x|CANON"

    def test_alias_to_nonexistent_arg_raises(self) -> None:
        with pytest.raises(ValueError, match="inexistentes"):

            @tool(
                name="bad",
                category=PermissionCategory.READ,
                description="x",
                args_schema={"path": "ruta"},
                aliases={"foo": "no_existe"},
            )
            def bad(path: str) -> str:
                return path

    def test_alias_clashing_with_real_arg_raises(self) -> None:
        with pytest.raises(ValueError, match="colisionan"):

            @tool(
                name="bad2",
                category=PermissionCategory.READ,
                description="x",
                args_schema={"path": "ruta", "other": "o"},
                aliases={"path": "other"},
            )
            def bad2(path: str, other: str) -> str:
                return path


class TestTruncateWithNotice:
    def test_short_text_unchanged(self) -> None:
        assert truncate_with_notice("hola", 100) == "hola"

    def test_none_or_zero_cap_disables(self) -> None:
        text = "x" * 500
        assert truncate_with_notice(text, None) == text
        assert truncate_with_notice(text, 0) == text

    def test_long_text_truncated_with_notice(self) -> None:
        text = "A" * 1000
        out = truncate_with_notice(text, 100, what="el doc")
        assert out.startswith("A" * 100)
        assert "TRUNCADO" in out
        assert "el doc" in out
        assert len(out) < len(text) + 300  # head + aviso, no el cuerpo entero

    def test_more_hint_is_included(self) -> None:
        out = truncate_with_notice("Z" * 200, 50, more_hint="Subí max_chars.")
        assert "Subí max_chars." in out
