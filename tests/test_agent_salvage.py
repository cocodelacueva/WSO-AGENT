"""Tests del rescate de tool calls escritas como texto."""

from __future__ import annotations

from wso.agent.salvage import salvage_tool_calls

KNOWN = ["read_file", "bash"]


def test_fenced_json() -> None:
    text = '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```'
    calls, rest = salvage_tool_calls(text, KNOWN)
    assert [(c.name, c.args) for c in calls] == [("read_file", {"path": "a.py"})]
    assert rest == ""


def test_tool_call_tags_and_parameters_key() -> None:
    text = '<tool_call>{"name": "bash", "parameters": {"command": "ls"}}</tool_call>'
    calls, _ = salvage_tool_calls(text, KNOWN)
    assert calls[0].args == {"command": "ls"}


def test_arguments_as_json_string() -> None:
    text = '{"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}'
    calls, _ = salvage_tool_calls(text, KNOWN)
    assert calls[0].args == {"command": "ls"}


def test_short_preamble_kept_as_text() -> None:
    text = 'Voy a leerlo.\n{"name": "read_file", "arguments": {"path": "a.py"}}'
    calls, rest = salvage_tool_calls(text, KNOWN)
    assert len(calls) == 1
    assert rest == "Voy a leerlo."


def test_unknown_tool_ignored() -> None:
    calls, rest = salvage_tool_calls('{"name": "rm_rf", "arguments": {}}', KNOWN)
    assert calls == ()


def test_explanation_with_json_example_not_salvaged() -> None:
    text = (
        "Para llamar la API tenés que mandar un objeto así:\n"
        '{"name": "read_file", "arguments": {"path": "a.py"}}\n'
        + "Después el servidor responde con el contenido del archivo y lo podés "
        "procesar como quieras; fijate que el campo name tiene que coincidir. " * 3
    )
    calls, rest = salvage_tool_calls(text, KNOWN)
    assert calls == ()
    assert rest == text


def test_plain_text() -> None:
    assert salvage_tool_calls("Listo, terminé.", KNOWN) == ((), "Listo, terminé.")
