"""Tests del prompt de aprobación."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import StringIO

import pytest
from rich.console import Console

from wso.permissions.prompts import (
    ApprovalChoice,
    ApprovalResponse,
    ask_approval,
    parse_approval_input,
    render_approval_panel,
)
from wso.tools.base import PermissionCategory, ToolDefinition, tool


@pytest.fixture
def sample_tool() -> ToolDefinition:
    """ToolDefinition de prueba con args útiles para el panel."""

    @tool(
        name="write_file",
        category=PermissionCategory.WRITE,
        description="Escribe un archivo de prueba.",
        args_schema={"path": "ruta", "content": "contenido"},
    )
    def write_file(path: str, content: str) -> str:
        return path

    defn = write_file._tool_def  # type: ignore[attr-defined]
    return defn


@pytest.fixture
def quiet_console() -> Console:
    """Console que escribe a un buffer (no afecta stdout en tests)."""
    return Console(file=StringIO(), force_terminal=False, no_color=True, width=80)


# ---------------------------------------------------------------------------
# parse_approval_input
# ---------------------------------------------------------------------------


class TestParseApprovalInput:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("y", ApprovalChoice.APPROVE_ONCE),
            ("Y", ApprovalChoice.APPROVE_ONCE),
            (" y ", ApprovalChoice.APPROVE_ONCE),
            ("s", ApprovalChoice.APPROVE_SESSION),
            ("S", ApprovalChoice.APPROVE_SESSION),
            ("a", ApprovalChoice.APPROVE_ALWAYS),
            ("A", ApprovalChoice.APPROVE_ALWAYS),
            ("n", ApprovalChoice.DENY),
            ("N", ApprovalChoice.DENY),
        ],
    )
    def test_single_letter_codes(
        self, raw: str, expected: ApprovalChoice
    ) -> None:
        result = parse_approval_input(raw)
        assert result.choice == expected
        assert result.feedback is None

    @pytest.mark.parametrize("raw", ["", " ", "\t", "\n"])
    def test_empty_input_denies(self, raw: str) -> None:
        result = parse_approval_input(raw)
        assert result.choice == ApprovalChoice.DENY
        assert result.feedback is None

    def test_multichar_text_treated_as_feedback(self) -> None:
        result = parse_approval_input("usa el path correcto")
        assert result.choice == ApprovalChoice.DENY_WITH_FEEDBACK
        assert result.feedback == "usa el path correcto"

    def test_feedback_is_stripped(self) -> None:
        result = parse_approval_input("   probá con /output/  ")
        assert result.choice == ApprovalChoice.DENY_WITH_FEEDBACK
        assert result.feedback == "probá con /output/"

    def test_yes_word_treated_as_feedback(self) -> None:
        # Solo aceptamos códigos de 1 caracter exacto
        result = parse_approval_input("yes")
        assert result.choice == ApprovalChoice.DENY_WITH_FEEDBACK
        assert result.feedback == "yes"


# ---------------------------------------------------------------------------
# ask_approval (con input_func mockeado)
# ---------------------------------------------------------------------------


class TestAskApproval:
    def test_returns_response_from_input_func(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        response = ask_approval(
            sample_tool,
            {"path": "/x", "content": "y"},
            console=quiet_console,
            input_func=lambda prompt: "y",
        )
        assert response.choice == ApprovalChoice.APPROVE_ONCE

    def test_renders_panel_to_console(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        ask_approval(
            sample_tool,
            {"path": "/x", "content": "y"},
            console=quiet_console,
            input_func=lambda prompt: "n",
        )
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]
        assert "Permiso requerido" in output
        assert "write_file" in output
        assert "/x" in output
        assert "Categoría" in output

    def test_feedback_response_propagates(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        response = ask_approval(
            sample_tool,
            {"path": "/x", "content": "y"},
            console=quiet_console,
            input_func=lambda prompt: "deberías usar /output/x.txt",
        )
        assert response.choice == ApprovalChoice.DENY_WITH_FEEDBACK
        assert response.feedback == "deberías usar /output/x.txt"


# ---------------------------------------------------------------------------
# render_approval_panel
# ---------------------------------------------------------------------------


class TestRenderApprovalPanel:
    def test_includes_all_options(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        render_approval_panel(sample_tool, {"path": "/x", "content": "y"}, quiet_console)
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]

        for letter in ["y", "s", "a", "n"]:
            assert letter in output
        assert "feedback" in output

    def test_truncates_long_arg_values(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        long_content = "x" * 500
        render_approval_panel(
            sample_tool,
            {"path": "/x", "content": long_content},
            quiet_console,
        )
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]
        # El contenido no debe aparecer entero (se trunca con ellipsis)
        assert "x" * 500 not in output
        assert "…" in output

    def test_displays_category_and_description(
        self, sample_tool: ToolDefinition, quiet_console: Console
    ) -> None:
        render_approval_panel(sample_tool, {"path": "/x", "content": "y"}, quiet_console)
        output = quiet_console.file.getvalue()  # type: ignore[union-attr]
        assert "write" in output  # categoría
        assert "Escribe un archivo" in output  # descripción


# ---------------------------------------------------------------------------
# ApprovalResponse semantics
# ---------------------------------------------------------------------------


class TestApprovalResponse:
    def test_is_frozen(self) -> None:
        response = ApprovalResponse(choice=ApprovalChoice.APPROVE_ONCE)
        with pytest.raises(FrozenInstanceError):
            response.choice = ApprovalChoice.DENY  # type: ignore[misc]

    def test_feedback_default_none(self) -> None:
        response = ApprovalResponse(choice=ApprovalChoice.APPROVE_ONCE)
        assert response.feedback is None
