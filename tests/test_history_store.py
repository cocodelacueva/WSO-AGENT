"""Tests de HistoryStore (persistencia de historial) + integración en el loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from wso.agent.loop import AgentLoop
from wso.agent.model.base import Message, ModelClient
from wso.agent.prompts import build_system_prompt
from wso.history_store import HistoryStore
from wso.permissions.manager import PermissionManager
from wso.tools.registry import load_builtin_tools
from wso.ui.console import ConsoleRenderer


def _msgs(*pairs: tuple[str, str]) -> list[Message]:
    return [Message(role=r, content=c) for r, c in pairs]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit: save / load / clear
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_roundtrip(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        store.save(_msgs(("user", "hola"), ("assistant", "qué tal")))
        loaded = store.load()
        assert [(m.role, m.content) for m in loaded] == [
            ("user", "hola"),
            ("assistant", "qué tal"),
        ]

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert HistoryStore(path=tmp_path / "nope.json").load() == []

    def test_system_messages_not_persisted(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        store.save(_msgs(("system", "SYS"), ("user", "u"), ("assistant", "a")))
        roles = [m.role for m in store.load()]
        assert "system" not in roles
        assert roles == ["user", "assistant"]

    def test_cap_keeps_last_n(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json", max_messages=2)
        store.save(_msgs(("user", "1"), ("assistant", "2"), ("user", "3")))
        loaded = store.load()
        assert [m.content for m in loaded] == ["2", "3"]

    def test_cap_applied_on_load_too(self, tmp_path: Path) -> None:
        # Archivo con más mensajes que el cap actual → load recorta.
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                {"messages": [{"role": "user", "content": str(i)} for i in range(10)]}
            ),
            encoding="utf-8",
        )
        loaded = HistoryStore(path=p, max_messages=3).load()
        assert [m.content for m in loaded] == ["7", "8", "9"]

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        store = HistoryStore(path=p)
        store.save(_msgs(("user", "x")))
        assert p.exists()
        store.clear()
        assert not p.exists()
        assert store.load() == []

    def test_atomic_no_tmp_left_behind(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        HistoryStore(path=p).save(_msgs(("user", "x")))
        assert not (tmp_path / "h.json.tmp").exists()


# ---------------------------------------------------------------------------
# Unit: no-op y robustez
# ---------------------------------------------------------------------------


class TestNoOpAndRobustness:
    def test_noop_when_path_none(self, tmp_path: Path) -> None:
        store = HistoryStore(path=None)
        assert not store.enabled
        store.save(_msgs(("user", "x")))  # no explota
        assert store.load() == []

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text("{ no es json válido", encoding="utf-8")
        assert HistoryStore(path=p).load() == []

    def test_non_dict_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert HistoryStore(path=p).load() == []

    def test_malformed_messages_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "ok"},
                        {"role": "user"},  # sin content
                        {"content": "sin role"},
                        "no es dict",
                        {"role": "tool", "content": "rol inválido"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        loaded = HistoryStore(path=p).load()
        assert [(m.role, m.content) for m in loaded] == [("user", "ok")]


# ---------------------------------------------------------------------------
# Integración con el loop
# ---------------------------------------------------------------------------


class _ScriptedModel(ModelClient):
    def __init__(self, response: str) -> None:
        self._response = response

    @property
    def model_name(self) -> str:
        return "scripted"

    async def stream_chat(self, messages: list[Message]) -> AsyncIterator[str]:
        yield self._response


def _make_loop(tmp_path: Path, store: HistoryStore, history=None) -> AgentLoop:
    renderer = ConsoleRenderer(
        console=Console(file=StringIO(), force_terminal=False, no_color=True, width=100)
    )

    class _S:
        context_dir = tmp_path / "ctx"
        automations_dir = tmp_path / "auto"
        output_dir = tmp_path / "out"
        permissions_file = tmp_path / "perms.toml"

    for d in (_S.context_dir, _S.automations_dir, _S.output_dir):
        d.mkdir(parents=True, exist_ok=True)

    tools = load_builtin_tools()
    model = _ScriptedModel(
        "<thinking>ok</thinking>"
        '<tool name="responder_al_usuario"><mensaje>listo</mensaje></tool>'
    )
    return AgentLoop(
        model=model,
        tools=tools,
        permissions=PermissionManager(settings=_S()),  # type: ignore[arg-type]
        renderer=renderer,
        system_prompt=build_system_prompt(tools),
        history=history if history is not None else [],
        history_store=store,
    )


class TestLoopIntegration:
    @pytest.mark.asyncio
    async def test_turn_persists_history(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        loop = _make_loop(tmp_path, store)

        await loop.execute_turn("hola mundo")

        # Tras el turno, el historial quedó persistido y restaurable.
        restored = store.load()
        contents = [m.content for m in restored]
        assert "hola mundo" in contents  # el input del usuario
        assert any(m.role == "assistant" for m in restored)

    @pytest.mark.asyncio
    async def test_restored_history_seeds_new_loop(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        loop1 = _make_loop(tmp_path, store)
        await loop1.execute_turn("primer mensaje")

        # Nueva "sesión": cargamos el historial guardado en un loop nuevo.
        restored = store.load()
        loop2 = _make_loop(tmp_path, store, history=restored)
        assert any("primer mensaje" in m.content for m in loop2.history)

    @pytest.mark.asyncio
    async def test_reset_clears_history(self, tmp_path: Path) -> None:
        store = HistoryStore(path=tmp_path / "h.json")
        loop = _make_loop(tmp_path, store)
        await loop.execute_turn("algo")
        assert store.load()  # hay algo guardado

        loop._reset_history()
        assert loop.history == []
        assert store.load() == []
