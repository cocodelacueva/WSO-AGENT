"""Tests de las tools de control de flujo."""

from __future__ import annotations

import json

from wso.tools.base import PermissionCategory, get_tool_definition
from wso.tools.flow import preguntar_al_usuario, responder_al_usuario


class TestResponder:
    def test_returns_message_unchanged(self) -> None:
        msg = "Listo, generé el reporte en /output/q3.pptx"
        assert responder_al_usuario(msg) == msg

    def test_preserves_unicode(self) -> None:
        msg = "Café ☕ con niños — ¿está bien así?"
        assert responder_al_usuario(msg) == msg

    def test_category_is_flow(self) -> None:
        defn = get_tool_definition(responder_al_usuario)
        assert defn is not None
        assert defn.category == PermissionCategory.FLOW


class TestPreguntar:
    def test_returns_valid_json(self) -> None:
        result = preguntar_al_usuario("¿Qué tono?")
        data = json.loads(result)
        assert data["pregunta"] == "¿Qué tono?"
        assert data["opciones"] == []
        assert data["permite_respuesta_libre"] is True

    def test_opciones_split_by_pipe(self) -> None:
        result = preguntar_al_usuario(
            "¿Cuál?",
            opciones="Formal|Casual|Técnico",
        )
        data = json.loads(result)
        assert data["opciones"] == ["Formal", "Casual", "Técnico"]

    def test_opciones_strips_whitespace(self) -> None:
        result = preguntar_al_usuario("x", opciones="  uno  |  dos  |  tres  ")
        data = json.loads(result)
        assert data["opciones"] == ["uno", "dos", "tres"]

    def test_opciones_skips_empty(self) -> None:
        result = preguntar_al_usuario("x", opciones="uno||  ||dos")
        data = json.loads(result)
        assert data["opciones"] == ["uno", "dos"]

    def test_permite_respuesta_libre_false(self) -> None:
        result = preguntar_al_usuario(
            "¿Cuál?",
            opciones="A|B",
            permite_respuesta_libre=False,
        )
        data = json.loads(result)
        assert data["permite_respuesta_libre"] is False

    def test_validate_and_call_coerces_string_bool(self) -> None:
        # Cuando el modelo emite XML, los args llegan como strings.
        # Pydantic debe coercer "false" a False.
        defn = get_tool_definition(preguntar_al_usuario)
        assert defn is not None
        result = defn.validate_and_call({
            "pregunta": "x",
            "opciones": "a|b",
            "permite_respuesta_libre": "false",
        })
        data = json.loads(result)
        assert data["permite_respuesta_libre"] is False

    def test_unicode_in_question_and_options(self) -> None:
        result = preguntar_al_usuario(
            "¿Café o té?",
            opciones="Café ☕|Té 🍵",
        )
        data = json.loads(result)
        assert data["pregunta"] == "¿Café o té?"
        assert data["opciones"] == ["Café ☕", "Té 🍵"]

    def test_category_is_flow(self) -> None:
        defn = get_tool_definition(preguntar_al_usuario)
        assert defn is not None
        assert defn.category == PermissionCategory.FLOW
