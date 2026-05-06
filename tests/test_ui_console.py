"""Tests del ConsoleRenderer."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from wso.ui.console import ConsoleRenderer


@pytest.fixture
def buffer() -> StringIO:
    return StringIO()


@pytest.fixture
def renderer(buffer: StringIO) -> ConsoleRenderer:
    console = Console(file=buffer, force_terminal=False, no_color=True, width=80)
    return ConsoleRenderer(console=console)


class TestUserInput:
    def test_renders_user_input_with_prefix(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_user_input("hola WSO")
        output = buffer.getvalue()
        assert "hola WSO" in output
        assert "›" in output


class TestThinking:
    def test_chunks_concatenate_without_newlines(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_thinking_chunk("Voy a ")
        renderer.render_thinking_chunk("pensar ")
        renderer.render_thinking_chunk("en esto.")
        output = buffer.getvalue()

        # Los chunks deben estar todos en el output sin newlines entre ellos
        assert "Voy a pensar en esto." in output

    def test_thinking_end_adds_newline(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_thinking_chunk("razonando")
        before = buffer.getvalue()
        renderer.render_thinking_end()
        after = buffer.getvalue()

        # Después de render_thinking_end hay un newline más
        assert after.count("\n") > before.count("\n")


class TestToolCall:
    def test_renders_tool_name_and_args(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_tool_call("read_file", {"path": "/x.md"})
        output = buffer.getvalue()

        assert "read_file" in output
        assert "/x.md" in output
        assert "▶" in output

    def test_truncates_long_arg_values(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        long_content = "a" * 500
        renderer.render_tool_call(
            "write_file", {"path": "/x", "content": long_content}
        )
        output = buffer.getvalue()

        # El contenido completo no debe estar
        assert "a" * 500 not in output
        assert "…" in output

    def test_handles_no_args(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_tool_call("ping", {})
        output = buffer.getvalue()
        assert "ping" in output
        assert "()" in output


class TestObservation:
    def test_renders_short_result(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_observation("read_file", "contenido del archivo")
        output = buffer.getvalue()

        assert "contenido del archivo" in output
        assert "←" in output

    def test_truncates_long_result_by_lines(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        long_text = "\n".join(f"línea {i}" for i in range(50))
        renderer.render_observation("read_file", long_text)
        output = buffer.getvalue()

        assert "líneas más" in output
        assert "línea 49" not in output

    def test_error_observation_uses_error_marker(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_observation("read_file", "FileNotFound: /x", is_error=True)
        output = buffer.getvalue()
        assert "✗" in output
        assert "FileNotFound" in output


class TestError:
    def test_renders_error_with_marker(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_error("modelo no responde")
        output = buffer.getvalue()
        assert "✗" in output
        assert "modelo no responde" in output


class TestFinalAnswer:
    def test_renders_in_panel(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_final_answer("Listo, terminé.")
        output = buffer.getvalue()
        assert "Respuesta" in output
        assert "Listo, terminé." in output
        # Algún tipo de borde de panel
        assert "╭" in output or "─" in output

    def test_preserves_multiline_content(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_final_answer("Línea 1\nLínea 2\nLínea 3")
        output = buffer.getvalue()
        assert "Línea 1" in output
        assert "Línea 3" in output


class TestQuestion:
    def test_renders_question_with_options(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_question(
            "¿Qué tono querés?",
            ["Formal", "Casual"],
            permite_respuesta_libre=True,
        )
        output = buffer.getvalue()

        assert "¿Qué tono querés?" in output
        assert "Formal" in output
        assert "Casual" in output
        assert "texto libre" in output

    def test_no_options_section_when_empty(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_question(
            "Pregunta abierta",
            [],
            permite_respuesta_libre=True,
        )
        output = buffer.getvalue()

        assert "Pregunta abierta" in output
        assert "Opciones:" not in output

    def test_no_free_text_hint_when_disallowed(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_question(
            "Elegí una",
            ["A", "B"],
            permite_respuesta_libre=False,
        )
        output = buffer.getvalue()

        assert "texto libre" not in output


class TestSeparatorAndInfo:
    def test_separator_renders(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_separator()
        output = buffer.getvalue()
        # rule() produce una línea de guiones
        assert "─" in output

    def test_info_renders(
        self, renderer: ConsoleRenderer, buffer: StringIO
    ) -> None:
        renderer.render_info("modelo cargado")
        output = buffer.getvalue()
        assert "modelo cargado" in output
