"""Tests del ConsoleRenderer."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from wso.ui.console import ConsoleRenderer


@pytest.fixture
def renderer() -> ConsoleRenderer:
    console = Console(file=StringIO(), force_terminal=False, no_color=True, width=100)
    return ConsoleRenderer(console=console)


def output(r: ConsoleRenderer) -> str:
    return r.console.file.getvalue()  # type: ignore[union-attr]


class TestStreaming:
    def test_text_chunks_concatenate(self, renderer: ConsoleRenderer) -> None:
        renderer.render_text_chunk("Hola ")
        renderer.render_text_chunk("mundo")
        renderer.end_stream()
        assert output(renderer) == "Hola mundo\n"

    def test_newline_between_thinking_and_text(self, renderer: ConsoleRenderer) -> None:
        renderer.render_thinking_chunk("pienso")
        renderer.render_text_chunk("respondo")
        renderer.end_stream()
        assert output(renderer) == "pienso\nrespondo\n"

    def test_end_stream_noop_when_idle(self, renderer: ConsoleRenderer) -> None:
        renderer.end_stream()
        assert output(renderer) == ""

    def test_markup_not_interpreted(self, renderer: ConsoleRenderer) -> None:
        renderer.render_text_chunk("lista[0] = [bold]x[/bold]")
        assert "[bold]x[/bold]" in output(renderer)

    def test_tool_call_closes_open_stream(self, renderer: ConsoleRenderer) -> None:
        renderer.render_text_chunk("voy a leer")
        renderer.render_tool_call("read_file", {"path": "a.py"})
        assert output(renderer) == "voy a leer\n▶ read_file a.py\n"


class TestToolCall:
    def test_main_arg_shown_bare(self, renderer: ConsoleRenderer) -> None:
        renderer.render_tool_call("bash", {"command": "pytest -q", "timeout_s": 60})
        assert "▶ bash pytest -q timeout_s=60" in output(renderer)

    def test_long_content_hidden(self, renderer: ConsoleRenderer) -> None:
        renderer.render_tool_call("write_file", {"path": "a.py", "content": "x" * 500})
        assert "xxxx" not in output(renderer)

    def test_long_main_arg_truncated(self, renderer: ConsoleRenderer) -> None:
        renderer.render_tool_call("grep", {"pattern": "p" * 300})
        assert "…" in output(renderer)


class TestObservation:
    def test_short_result(self, renderer: ConsoleRenderer) -> None:
        renderer.render_observation("una línea")
        assert output(renderer) == "  ← una línea\n"

    def test_truncated_by_lines(self, renderer: ConsoleRenderer) -> None:
        renderer.render_observation("\n".join(f"l{i}" for i in range(50)))
        out = output(renderer)
        assert "l0" in out
        assert "l49" not in out
        assert "líneas más" in out

    def test_error_marker(self, renderer: ConsoleRenderer) -> None:
        renderer.render_observation("ERROR: algo", is_error=True)
        assert "✗ ERROR: algo" in output(renderer)


class TestSystemMessages:
    def test_error(self, renderer: ConsoleRenderer) -> None:
        renderer.render_error("falló")
        assert "✗ falló" in output(renderer)

    def test_info_and_separator(self, renderer: ConsoleRenderer) -> None:
        renderer.render_info("dato")
        renderer.render_separator()
        out = output(renderer)
        assert "dato" in out
        assert "─" in out
