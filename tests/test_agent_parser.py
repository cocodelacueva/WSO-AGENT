"""Tests del parser XML streaming."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from wso.agent.parser import (
    ParseError,
    ParseEvent,
    ParseState,
    PlainTextChunk,
    StreamingXMLParser,
    ThinkingChunk,
    ThinkingEnd,
    ToolCallComplete,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_chunks(*chunks: str) -> list[ParseEvent]:
    """Alimentar el parser con los chunks dados y devolver todos los eventos."""
    parser = StreamingXMLParser()
    events: list[ParseEvent] = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.finalize())
    return events


def collapse_thinking(events: Iterable[ParseEvent]) -> str:
    """Concatenar el contenido de todos los ThinkingChunk (útil para tests
    de streaming char-by-char donde el contenido viene fragmentado)."""
    return "".join(e.text for e in events if isinstance(e, ThinkingChunk))


# ---------------------------------------------------------------------------
# Casos básicos: parsing en un solo chunk
# ---------------------------------------------------------------------------


class TestSingleChunk:
    def test_thinking_only(self) -> None:
        events = parse_chunks("<thinking>razonando</thinking>")
        assert events == [ThinkingChunk("razonando"), ThinkingEnd()]

    def test_tool_with_one_arg(self) -> None:
        events = parse_chunks(
            '<tool name="read_file"><path>/foo.md</path></tool>'
        )
        assert events == [ToolCallComplete(name="read_file", args={"path": "/foo.md"})]

    def test_tool_with_multiple_args(self) -> None:
        events = parse_chunks(
            '<tool name="write_file">'
            "<path>/x.md</path>"
            "<content>hola</content>"
            "</tool>"
        )
        assert events == [
            ToolCallComplete(
                name="write_file",
                args={"path": "/x.md", "content": "hola"},
            )
        ]

    def test_thinking_then_tool(self) -> None:
        events = parse_chunks(
            "<thinking>voy a leer</thinking>"
            '<tool name="read_file"><path>/x</path></tool>'
        )
        assert events == [
            ThinkingChunk("voy a leer"),
            ThinkingEnd(),
            ToolCallComplete(name="read_file", args={"path": "/x"}),
        ]

    def test_self_closing_tool(self) -> None:
        events = parse_chunks('<tool name="ping" />')
        assert events == [ToolCallComplete(name="ping", args={})]

    def test_self_closing_tool_no_space(self) -> None:
        events = parse_chunks('<tool name="ping"/>')
        assert events == [ToolCallComplete(name="ping", args={})]


# ---------------------------------------------------------------------------
# Streaming: chunks fragmentados en lugares delicados
# ---------------------------------------------------------------------------


class TestStreaming:
    def test_split_mid_thinking_open(self) -> None:
        events = parse_chunks("<thi", "nking>hi</thinking>")
        assert events == [ThinkingChunk("hi"), ThinkingEnd()]

    def test_split_mid_thinking_close(self) -> None:
        events = parse_chunks("<thinking>foo</thi", "nking>")
        assert events == [ThinkingChunk("foo"), ThinkingEnd()]

    def test_split_at_angle_bracket(self) -> None:
        events = parse_chunks("<", "thinking>hi</thinking>")
        assert events == [ThinkingChunk("hi"), ThinkingEnd()]

    def test_char_by_char_streaming(self) -> None:
        text = "<thinking>una idea larga</thinking>"
        parser = StreamingXMLParser()
        events: list[ParseEvent] = []
        for char in text:
            events.extend(parser.feed(char))
        events.extend(parser.finalize())

        # El contenido completo debe estar reconstruible
        assert collapse_thinking(events) == "una idea larga"
        assert any(isinstance(e, ThinkingEnd) for e in events)

    def test_split_inside_tool_args(self) -> None:
        events = parse_chunks(
            '<tool name="write_file"><path>/x</pa',
            "th><content>data</content></tool>",
        )
        assert events == [
            ToolCallComplete(
                name="write_file", args={"path": "/x", "content": "data"}
            )
        ]

    def test_split_inside_tool_close_tag(self) -> None:
        events = parse_chunks(
            '<tool name="ping"><a>1</a></to',
            "ol>",
        )
        assert events == [ToolCallComplete(name="ping", args={"a": "1"})]


# ---------------------------------------------------------------------------
# Múltiples bloques: lo que pidió el user (thinking intercalado)
# ---------------------------------------------------------------------------


class TestMultipleBlocks:
    def test_two_thinking_blocks_then_tool(self) -> None:
        events = parse_chunks(
            "<thinking>paso 1</thinking>"
            "<thinking>paso 2</thinking>"
            '<tool name="write_file"><path>/x</path><content>y</content></tool>'
        )
        assert events == [
            ThinkingChunk("paso 1"),
            ThinkingEnd(),
            ThinkingChunk("paso 2"),
            ThinkingEnd(),
            ToolCallComplete(
                name="write_file", args={"path": "/x", "content": "y"}
            ),
        ]

    def test_three_thinking_blocks(self) -> None:
        events = parse_chunks(
            "<thinking>a</thinking>"
            "<thinking>b</thinking>"
            "<thinking>c</thinking>"
        )
        thinkings = [e for e in events if isinstance(e, ThinkingChunk)]
        ends = [e for e in events if isinstance(e, ThinkingEnd)]
        assert [t.text for t in thinkings] == ["a", "b", "c"]
        assert len(ends) == 3


# ---------------------------------------------------------------------------
# Tolerancia: whitespace, case, y otras inconsistencias
# ---------------------------------------------------------------------------


class TestTolerance:
    def test_uppercase_thinking(self) -> None:
        events = parse_chunks("<THINKING>hola</THINKING>")
        assert events == [ThinkingChunk("hola"), ThinkingEnd()]

    def test_mixed_case_thinking(self) -> None:
        events = parse_chunks("<Thinking>hola</thinking>")
        assert events == [ThinkingChunk("hola"), ThinkingEnd()]

    def test_whitespace_in_open_tag(self) -> None:
        events = parse_chunks("< thinking >hola< / thinking >")
        assert events == [ThinkingChunk("hola"), ThinkingEnd()]

    def test_single_quotes_in_tool_name(self) -> None:
        events = parse_chunks("<tool name='ping' />")
        assert events == [ToolCallComplete(name="ping", args={})]

    def test_extra_whitespace_in_tool(self) -> None:
        events = parse_chunks(
            '<tool   name = "read_file"  ><path>/x</path></tool>'
        )
        assert events == [
            ToolCallComplete(name="read_file", args={"path": "/x"})
        ]


# ---------------------------------------------------------------------------
# Contenido especial: multilínea, unicode, etc.
# ---------------------------------------------------------------------------


class TestSpecialContent:
    def test_multiline_content_arg(self) -> None:
        events = parse_chunks(
            '<tool name="write_file">'
            "<path>/notes.md</path>"
            "<content>línea 1\nlínea 2\nlínea 3</content>"
            "</tool>"
        )
        assert events == [
            ToolCallComplete(
                name="write_file",
                args={
                    "path": "/notes.md",
                    "content": "línea 1\nlínea 2\nlínea 3",
                },
            )
        ]

    def test_unicode_in_thinking(self) -> None:
        events = parse_chunks("<thinking>café — niño 🎯</thinking>")
        assert events == [ThinkingChunk("café — niño 🎯"), ThinkingEnd()]

    def test_arg_value_with_internal_whitespace(self) -> None:
        events = parse_chunks(
            '<tool name="echo"><msg>  hola   mundo  </msg></tool>'
        )
        # El parser hace strip del valor
        assert events == [
            ToolCallComplete(name="echo", args={"msg": "hola   mundo"})
        ]

    def test_cdata_wrap_is_stripped(self) -> None:
        """Algunos modelos envuelven args en CDATA. El parser lo limpia."""
        events = parse_chunks(
            '<tool name="write_file">'
            "<path>/x.html</path>"
            "<content><![CDATA[<html>contenido</html>]]></content>"
            "</tool>"
        )
        assert events == [
            ToolCallComplete(
                name="write_file",
                args={"path": "/x.html", "content": "<html>contenido</html>"},
            )
        ]

    def test_cdata_wrap_with_multiline_content(self) -> None:
        events = parse_chunks(
            '<tool name="write_file">'
            "<path>/x.css</path>"
            "<content><![CDATA[\nbody { color: red; }\nh1 { font-size: 2em; }\n]]></content>"
            "</tool>"
        )
        tool = next(e for e in events if isinstance(e, ToolCallComplete))
        assert tool.args["content"] == "body { color: red; }\nh1 { font-size: 2em; }"
        assert "CDATA" not in tool.args["content"]
        assert "]]>" not in tool.args["content"]

    def test_value_without_cdata_unchanged(self) -> None:
        """Los valores sin CDATA wrap no se tocan."""
        events = parse_chunks(
            '<tool name="write_file">'
            "<path>/x.txt</path>"
            "<content>contenido normal sin CDATA</content>"
            "</tool>"
        )
        tool = next(e for e in events if isinstance(e, ToolCallComplete))
        assert tool.args["content"] == "contenido normal sin CDATA"


# ---------------------------------------------------------------------------
# Casos de error y recovery
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    def test_unclosed_tool_emits_parse_error_on_finalize(self) -> None:
        events = parse_chunks('<tool name="x"><a>1</a>')
        errors = [e for e in events if isinstance(e, ParseError)]
        assert len(errors) == 1
        assert "x" in errors[0].message

    def test_unclosed_thinking_emits_implicit_end(self) -> None:
        events = parse_chunks("<thinking>incomplete")
        # El finalize cierra implícitamente
        assert any(isinstance(e, ThinkingChunk) for e in events)
        assert any(isinstance(e, ThinkingEnd) for e in events)

    def test_plain_text_outside_tags(self) -> None:
        events = parse_chunks("Hello world, no tags here.")
        plains = [e for e in events if isinstance(e, PlainTextChunk)]
        assert len(plains) == 1
        assert "Hello world" in plains[0].text

    def test_text_then_thinking(self) -> None:
        events = parse_chunks("preamble <thinking>hi</thinking>")
        plains = [e for e in events if isinstance(e, PlainTextChunk)]
        thinkings = [e for e in events if isinstance(e, ThinkingChunk)]
        assert plains == [PlainTextChunk("preamble ")]
        assert thinkings == [ThinkingChunk("hi")]


# ---------------------------------------------------------------------------
# Estado interno del parser
# ---------------------------------------------------------------------------


class TestParserState:
    def test_initial_state_is_outside(self) -> None:
        parser = StreamingXMLParser()
        assert parser.state == ParseState.OUTSIDE

    def test_state_after_open_tag(self) -> None:
        parser = StreamingXMLParser()
        list(parser.feed("<thinking>"))
        assert parser.state == ParseState.IN_THINKING

    def test_state_returns_to_outside_after_close(self) -> None:
        parser = StreamingXMLParser()
        list(parser.feed("<thinking>x</thinking>"))
        assert parser.state == ParseState.OUTSIDE

    def test_state_after_tool_open(self) -> None:
        parser = StreamingXMLParser()
        list(parser.feed('<tool name="x">'))
        assert parser.state == ParseState.IN_TOOL

    def test_finalize_resets_state(self) -> None:
        parser = StreamingXMLParser()
        list(parser.feed("<thinking>incomplete"))
        list(parser.finalize())
        assert parser.state == ParseState.OUTSIDE


# ---------------------------------------------------------------------------
# Combinación realista: simulación de output de un modelo
# ---------------------------------------------------------------------------


class TestRealisticScenarios:
    def test_full_turn_simulation(self) -> None:
        """Simular un turno completo que un modelo bien comportado emitiría."""
        events = parse_chunks(
            "<thinking>El usuario quiere ver el contenido de notes.md. "
            "Voy a leerlo primero.</thinking>"
            "<tool name=\"read_file\">"
            "<path>/Users/coco/notes.md</path>"
            "</tool>"
        )
        assert len(events) == 3
        assert isinstance(events[0], ThinkingChunk)
        assert isinstance(events[1], ThinkingEnd)
        assert isinstance(events[2], ToolCallComplete)
        tool = events[2]
        assert isinstance(tool, ToolCallComplete)
        assert tool.name == "read_file"
        assert tool.args == {"path": "/Users/coco/notes.md"}

    def test_responder_al_usuario_pattern(self) -> None:
        events = parse_chunks(
            "<thinking>Listo, terminé.</thinking>"
            '<tool name="responder_al_usuario">'
            "<mensaje>Generé el archivo en /output/x.md</mensaje>"
            "</tool>"
        )
        tool = next(e for e in events if isinstance(e, ToolCallComplete))
        assert tool.name == "responder_al_usuario"
        assert "Generé el archivo" in tool.args["mensaje"]

    def test_preguntar_al_usuario_pattern(self) -> None:
        events = parse_chunks(
            '<tool name="preguntar_al_usuario">'
            "<pregunta>¿Tono formal o casual?</pregunta>"
            "<opciones>Formal|Casual</opciones>"
            "<permite_respuesta_libre>true</permite_respuesta_libre>"
            "</tool>"
        )
        tool = next(e for e in events if isinstance(e, ToolCallComplete))
        assert tool.name == "preguntar_al_usuario"
        assert tool.args["pregunta"] == "¿Tono formal o casual?"
        assert tool.args["opciones"] == "Formal|Casual"
        assert tool.args["permite_respuesta_libre"] == "true"


# ---------------------------------------------------------------------------
# Determinismo: mismo input → mismos eventos sin importar la fragmentación
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(
        "split_at",
        [1, 2, 5, 10, 15, 20, 30, 50],
    )
    def test_same_events_regardless_of_split(self, split_at: int) -> None:
        full = (
            "<thinking>razonando un poco</thinking>"
            '<tool name="read_file"><path>/foo.md</path></tool>'
        )
        # Comparar contra parsing de todo el string en un chunk
        baseline = parse_chunks(full)

        if split_at >= len(full):
            pytest.skip("split point fuera del input")

        chunk1 = full[:split_at]
        chunk2 = full[split_at:]
        split = parse_chunks(chunk1, chunk2)

        # El total de eventos puede diferir si ThinkingChunk se emite en
        # múltiples partes; comparamos por contenido reconstruido.
        baseline_thinking = collapse_thinking(baseline)
        split_thinking = collapse_thinking(split)
        assert baseline_thinking == split_thinking

        # ToolCallComplete debe ser idéntico
        baseline_tools = [e for e in baseline if isinstance(e, ToolCallComplete)]
        split_tools = [e for e in split if isinstance(e, ToolCallComplete)]
        assert baseline_tools == split_tools
