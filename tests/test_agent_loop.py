"""Tests de integración del AgentLoop.

Usan un `ScriptedModel` (model client fake que devuelve respuestas
pre-programadas) para verificar el comportamiento end-to-end del loop
sin depender de un modelo real.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from wso.agent.loop import AgentLoop
from wso.agent.model.base import Message, ModelClient
from wso.agent.prompts import build_system_prompt
from wso.permissions.manager import PermissionManager
from wso.session_log import SessionLogger
from wso.tools.registry import load_builtin_tools
from wso.ui.console import ConsoleRenderer

# ---------------------------------------------------------------------------
# Fakes y fixtures
# ---------------------------------------------------------------------------


class ScriptedModel(ModelClient):
    """Model client fake con respuestas pre-programadas.

    Cada llamada a `stream_chat` devuelve la siguiente respuesta del
    script. Si se acaban, devuelve un fallback que termina el turno.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.last_messages: list[list[Message]] = []

    @property
    def model_name(self) -> str:
        return "scripted-test-model"

    async def stream_chat(
        self, messages: list[Message]
    ) -> AsyncIterator[str]:
        self.last_messages.append(list(messages))
        if self.calls < len(self._responses):
            response = self._responses[self.calls]
            self.calls += 1
        else:
            # Fallback para no quedarse en loop infinito
            response = (
                '<thinking>fallback</thinking>'
                '<tool name="responder_al_usuario">'
                "<mensaje>(fallback final)</mensaje>"
                "</tool>"
            )
        yield response


class FakeSettings:
    """Settings sintético sin pydantic."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._context = root / "context"
        self._automations = root / "automations"
        self._output = root / "output"
        self._permissions_file = root / "permissions.toml"
        for d in [self._context, self._automations, self._output]:
            d.mkdir(parents=True, exist_ok=True)

    @property
    def context_dir(self) -> Path:
        return self._context

    @property
    def automations_dir(self) -> Path:
        return self._automations

    @property
    def output_dir(self) -> Path:
        return self._output

    @property
    def permissions_file(self) -> Path:
        return self._permissions_file


@pytest.fixture
def settings(tmp_path: Path) -> FakeSettings:
    return FakeSettings(tmp_path)


@pytest.fixture
def buffer() -> StringIO:
    return StringIO()


@pytest.fixture
def renderer(buffer: StringIO) -> ConsoleRenderer:
    console = Console(file=buffer, force_terminal=False, no_color=True, width=100)
    return ConsoleRenderer(console=console)


@pytest.fixture
def tools_registry():
    return load_builtin_tools()


def make_loop(
    responses: list[str],
    settings: FakeSettings,
    renderer: ConsoleRenderer,
    tools_registry,
    auto_approve_input: str = "y",
    session_log: SessionLogger | None = None,
) -> tuple[AgentLoop, ScriptedModel]:
    """Construir un AgentLoop con un ScriptedModel."""
    model = ScriptedModel(responses)
    permissions = PermissionManager(settings=settings)  # type: ignore[arg-type]
    system_prompt = build_system_prompt(tools_registry)
    kwargs: dict = dict(
        model=model,
        tools=tools_registry,
        permissions=permissions,
        renderer=renderer,
        system_prompt=system_prompt,
    )
    if session_log is not None:
        kwargs["session_log"] = session_log
    loop = AgentLoop(**kwargs)
    return loop, model


# ---------------------------------------------------------------------------
# Single-step turns (one tool then end)
# ---------------------------------------------------------------------------


class TestSimpleTurn:
    @pytest.mark.asyncio
    async def test_responder_terminates_turn(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        responses = [
            "<thinking>Respondo directo.</thinking>"
            '<tool name="responder_al_usuario">'
            "<mensaje>Hola, soy WSO.</mensaje>"
            "</tool>"
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn("hola")

        # Solo se llamó al modelo una vez (el responder terminó)
        assert model.calls == 1
        # El final answer aparece renderizado
        assert "Hola, soy WSO." in buffer.getvalue()
        # El budget registró el step
        assert loop.budget.used == 1
        assert loop.budget.history[0].tool_name == "responder_al_usuario"

    @pytest.mark.asyncio
    async def test_preguntar_terminates_turn(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        responses = [
            '<tool name="preguntar_al_usuario">'
            "<pregunta>¿Qué tono?</pregunta>"
            "<opciones>Formal|Casual</opciones>"
            "</tool>"
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn("haceme un deck")

        assert model.calls == 1
        output = buffer.getvalue()
        assert "¿Qué tono?" in output
        assert "Formal" in output


# ---------------------------------------------------------------------------
# Multi-step turns (read tool, then respond)
# ---------------------------------------------------------------------------


class TestMultiStepTurn:
    @pytest.mark.asyncio
    async def test_read_then_respond(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        # Crear un archivo en el whitelist (output_dir)
        target = settings.output_dir / "data.md"
        target.write_text("contenido del archivo", encoding="utf-8")

        responses = [
            f'<thinking>Leo el archivo.</thinking>'
            f'<tool name="read_file"><path>{target}</path></tool>',
            '<thinking>Listo, respondo.</thinking>'
            '<tool name="responder_al_usuario">'
            "<mensaje>El archivo dice: contenido del archivo</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn(f"leeme el archivo {target}")

        assert model.calls == 2
        output = buffer.getvalue()
        assert "read_file" in output
        assert "El archivo dice" in output

        # History debería tener: user (input) + assistant + observation + assistant (final)
        assert len(loop.history) >= 4
        assert loop.history[0].role == "user"
        assert loop.history[1].role == "assistant"
        # La observation queda como mensaje de "user" para que el modelo la vea
        assert "<observation" in loop.history[2].content


# ---------------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------------


class TestPermissionGating:
    @pytest.mark.asyncio
    async def test_write_requires_approval_and_proceeds_if_approved(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = settings.output_dir / "new.txt"

        responses = [
            f'<tool name="write_file">'
            f"<path>{target}</path>"
            f"<content>hola</content>"
            f"</tool>",
            '<tool name="responder_al_usuario">'
            "<mensaje>escrito</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        # Mockear el input de aprobación: "y" (aprobar una vez)
        monkeypatch.setattr("builtins.input", lambda _: "y")

        await loop.execute_turn("escribí hola en new.txt")

        assert target.exists()
        assert target.read_text() == "hola"

    @pytest.mark.asyncio
    async def test_write_denied_does_not_execute(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = settings.output_dir / "new.txt"

        responses = [
            f'<tool name="write_file">'
            f"<path>{target}</path>"
            f"<content>secret</content>"
            f"</tool>",
            '<tool name="responder_al_usuario">'
            "<mensaje>no se pudo</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        monkeypatch.setattr("builtins.input", lambda _: "n")

        await loop.execute_turn("escribí algo")

        assert not target.exists()
        # El modelo recibió el DENIED como observation
        denied_obs = [
            msg for msg in loop.history
            if msg.role == "user" and "DENIED" in msg.content
        ]
        assert len(denied_obs) == 1

    @pytest.mark.asyncio
    async def test_deny_with_feedback_propagates(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = settings.output_dir / "x.txt"
        responses = [
            f'<tool name="write_file">'
            f"<path>{target}</path>"
            f"<content>v</content>"
            f"</tool>",
            '<tool name="responder_al_usuario">'
            "<mensaje>OK</mensaje>"
            "</tool>",
        ]
        loop, _ = make_loop(responses, settings, renderer, tools_registry)

        monkeypatch.setattr("builtins.input", lambda _: "usá otro path")

        await loop.execute_turn("hacé algo")

        # El feedback debe estar en el history
        feedback_msgs = [
            msg for msg in loop.history
            if "usá otro path" in msg.content
        ]
        assert len(feedback_msgs) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_crash(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        responses = [
            '<tool name="frobnicate"><x>1</x></tool>',
            '<tool name="responder_al_usuario">'
            "<mensaje>oops</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn("hacé algo")

        # Debería haber un error en el output, pero el turno termina limpio
        output = buffer.getvalue()
        assert "frobnicate" in output
        assert "no existe" in output or "desconocida" in output
        # El modelo se llamó dos veces (la unknown tool + el responder)
        assert model.calls == 2

    @pytest.mark.asyncio
    async def test_no_tool_emitted_provides_feedback(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        responses = [
            "<thinking>solo pienso, no actúo</thinking>",
            '<tool name="responder_al_usuario">'
            "<mensaje>fix</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn("hola")

        # El primer paso recibió error, el segundo terminó normal
        assert model.calls == 2
        # History tiene un mensaje "ERROR: tu respuesta no contenía"
        error_msgs = [
            msg for msg in loop.history
            if "ERROR" in msg.content and "ningún <tool>" in msg.content
        ]
        assert len(error_msgs) == 1

    @pytest.mark.asyncio
    async def test_tool_execution_error_becomes_observation(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        buffer: StringIO,
    ) -> None:
        # Pedirle al modelo que lea un archivo que no existe
        nonexistent = settings.output_dir / "does_not_exist.txt"

        responses = [
            f'<tool name="read_file"><path>{nonexistent}</path></tool>',
            '<tool name="responder_al_usuario">'
            "<mensaje>no existía</mensaje>"
            "</tool>",
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        await loop.execute_turn("leelo")

        # El error de read_file debería estar en el history como observation
        error_obs = [
            msg for msg in loop.history
            if msg.role == "user"
            and "FileNotFoundError" in msg.content
        ]
        assert len(error_obs) == 1
        # El budget registró el fallo
        assert any(not s.success for s in loop.budget.history)


# ---------------------------------------------------------------------------
# Budget exhaustion
# ---------------------------------------------------------------------------


class TestBudgetExhaustion:
    @pytest.mark.asyncio
    async def test_continuation_continue_extends_budget(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 10 lecturas que fallan + responder al final
        nonexistent = settings.output_dir / "x.txt"
        bad_response = (
            f'<tool name="read_file"><path>{nonexistent}</path></tool>'
        )
        responses = [bad_response] * 10 + [
            '<tool name="responder_al_usuario">'
            "<mensaje>fin</mensaje>"
            "</tool>"
        ]
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        # Cuando se agote el budget, el usuario dice "y"
        monkeypatch.setattr("builtins.input", lambda _: "y")

        await loop.execute_turn("intentá leer ese archivo")

        assert model.calls == 11
        assert loop.budget.limit == 20  # 10 + 10 extra

    @pytest.mark.asyncio
    async def test_continuation_abort_ends_turn(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        nonexistent = settings.output_dir / "x.txt"
        responses = [
            f'<tool name="read_file"><path>{nonexistent}</path></tool>'
        ] * 30  # mucho más que el budget
        loop, model = make_loop(responses, settings, renderer, tools_registry)

        # En el prompt de continuación, el usuario abandona
        monkeypatch.setattr("builtins.input", lambda _: "n")

        await loop.execute_turn("loop infinito")

        # Solo se hicieron los 10 originales antes del abort
        assert model.calls == 10


# ---------------------------------------------------------------------------
# SessionLogger integration
# ---------------------------------------------------------------------------


def _read_events(log_path: Path) -> list[dict]:
    """Leer un archivo JSONL y devolver la lista de eventos."""
    import json

    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


class TestSessionLogIntegration:
    @pytest.mark.asyncio
    async def test_no_op_logger_by_default(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        """Sin pasar session_log, el loop usa un logger no-op y no crea archivos."""
        responses = [
            '<tool name="responder_al_usuario">'
            "<mensaje>OK</mensaje>"
            "</tool>"
        ]
        loop, _ = make_loop(responses, settings, renderer, tools_registry)
        await loop.execute_turn("hola")

        # No se creó ningún archivo de log
        assert loop.session_log.enabled is False
        assert loop.session_log.path is None

    @pytest.mark.asyncio
    async def test_turn_emits_expected_events(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        """Un turno con responder_al_usuario emite turn_start, model_response,
        tool_call, permission, observation (vía flow), turn_end."""
        log_dir = tmp_path / "logs"
        logger = SessionLogger(log_dir=log_dir)

        responses = [
            "<thinking>respondo</thinking>"
            '<tool name="responder_al_usuario">'
            "<mensaje>OK</mensaje>"
            "</tool>"
        ]
        loop, _ = make_loop(
            responses, settings, renderer, tools_registry, session_log=logger
        )

        await loop.execute_turn("hola")
        logger.close()

        events = _read_events(logger.path)
        event_types = [e["event"] for e in events]

        assert "turn_start" in event_types
        assert "model_response" in event_types
        assert "tool_call" in event_types
        assert "permission" in event_types
        assert "turn_end" in event_types

    @pytest.mark.asyncio
    async def test_tool_call_event_captures_args(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        log_dir = tmp_path / "logs"
        logger = SessionLogger(log_dir=log_dir)

        # Crear un archivo en el whitelist (output_dir) para que read_file funque
        target = settings.output_dir / "data.md"
        target.write_text("x", encoding="utf-8")

        responses = [
            f'<tool name="read_file"><path>{target}</path></tool>',
            '<tool name="responder_al_usuario">'
            "<mensaje>fin</mensaje>"
            "</tool>",
        ]
        loop, _ = make_loop(
            responses, settings, renderer, tools_registry, session_log=logger
        )

        await loop.execute_turn("leelo")
        logger.close()

        events = _read_events(logger.path)
        tool_calls = [e for e in events if e["event"] == "tool_call"]

        # Debería haber al menos read_file y responder_al_usuario
        names = [e["name"] for e in tool_calls]
        assert "read_file" in names
        assert "responder_al_usuario" in names

        read_event = next(e for e in tool_calls if e["name"] == "read_file")
        assert read_event["args"]["path"] == str(target)

    @pytest.mark.asyncio
    async def test_observation_event_includes_success_flag(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        """Una tool que falla debería loguear observation con success=False."""
        log_dir = tmp_path / "logs"
        logger = SessionLogger(log_dir=log_dir)

        nonexistent = settings.output_dir / "no.txt"
        responses = [
            f'<tool name="read_file"><path>{nonexistent}</path></tool>',
            '<tool name="responder_al_usuario">'
            "<mensaje>error</mensaje>"
            "</tool>",
        ]
        loop, _ = make_loop(
            responses, settings, renderer, tools_registry, session_log=logger
        )

        await loop.execute_turn("intentá leerlo")
        logger.close()

        events = _read_events(logger.path)
        obs = [e for e in events if e["event"] == "observation"]
        read_obs = next(e for e in obs if e["tool"] == "read_file")
        assert read_obs["success"] is False
        assert "FileNotFoundError" in read_obs["result"]

    @pytest.mark.asyncio
    async def test_turn_end_has_duration_and_steps(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        log_dir = tmp_path / "logs"
        logger = SessionLogger(log_dir=log_dir)

        responses = [
            '<tool name="responder_al_usuario">'
            "<mensaje>x</mensaje>"
            "</tool>"
        ]
        loop, _ = make_loop(
            responses, settings, renderer, tools_registry, session_log=logger
        )

        await loop.execute_turn("hola")
        logger.close()

        events = _read_events(logger.path)
        turn_end = next(e for e in events if e["event"] == "turn_end")
        assert "duration_ms" in turn_end
        assert turn_end["duration_ms"] >= 0
        assert turn_end["steps"] >= 1
        assert turn_end["turn"] == 1

    @pytest.mark.asyncio
    async def test_permission_event_with_auto_approval(
        self,
        settings: FakeSettings,
        renderer: ConsoleRenderer,
        tools_registry,
        tmp_path: Path,
    ) -> None:
        """Flow tools (responder/preguntar) son auto-approved."""
        log_dir = tmp_path / "logs"
        logger = SessionLogger(log_dir=log_dir)

        responses = [
            '<tool name="responder_al_usuario">'
            "<mensaje>hi</mensaje>"
            "</tool>"
        ]
        loop, _ = make_loop(
            responses, settings, renderer, tools_registry, session_log=logger
        )

        await loop.execute_turn("hola")
        logger.close()

        events = _read_events(logger.path)
        perm = next(e for e in events if e["event"] == "permission")
        assert perm["decision"] == "auto_approved"
        assert perm["tool"] == "responder_al_usuario"
