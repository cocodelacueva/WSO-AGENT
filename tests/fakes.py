"""Fakes compartidos para tests del loop: modelo guionado y armado del loop."""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from typing import Any

from rich.console import Console

from wso.agent.loop import AgentLoop
from wso.agent.model.base import (
    Message,
    ModelClient,
    ModelError,
    ResponseDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from wso.permissions.manager import PermissionManager
from wso.session_log import SessionLogger
from wso.tools.registry import load_builtin_tools
from wso.ui.console import ConsoleRenderer


def say(text: str) -> Message:
    """Respuesta final en texto (cierra el turno)."""
    return Message(role="assistant", content=text)


def call(name: str, _id: str = "c1", text: str = "", **args: Any) -> Message:
    """Respuesta con una tool call."""
    return Message(role="assistant", content=text, tool_calls=(ToolCall(_id, name, args),))


class ScriptedModel(ModelClient):
    """Devuelve respuestas pre-programadas, una por llamada.

    Cada item del guion es un `Message` (respuesta), un `ModelError` (se
    lanza) o un `ResponseDone` (para controlar usage/truncated).
    """

    def __init__(self, script: list[Message | ModelError | ResponseDone], window: int = 200_000):
        self._script = list(script)
        self._window = window
        self.calls: list[list[Message]] = []
        self.tools_seen: list[list[ToolSpec]] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    @property
    def context_window(self) -> int:
        return self._window

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        self.tools_seen.append(list(tools))
        item = self._script.pop(0) if self._script else say("(fin del guion)")
        if isinstance(item, ModelError):
            raise item
        done = item if isinstance(item, ResponseDone) else ResponseDone(message=item)
        if done.message.content:
            yield TextDelta(done.message.content)
        yield done


class FakeSettings:
    def __init__(self, root: Path) -> None:
        self.permissions_file = root / "_config" / "permissions.toml"


def make_loop(
    project: Path,
    script: list[Message | ModelError | ResponseDone],
    *,
    answers: list[str] | None = None,
    log_dir: Path | None = None,
    **kwargs: Any,
) -> tuple[AgentLoop, ScriptedModel, StringIO]:
    """Armar un AgentLoop con modelo guionado, consola a buffer y respuestas
    del usuario (aprobaciones/continuación) pre-programadas."""
    model = ScriptedModel(script, window=kwargs.pop("window", 200_000))
    out = StringIO()
    console = Console(file=out, force_terminal=False, no_color=True, width=120)
    pending = list(answers or [])

    def input_func(_prompt: str) -> str:
        return pending.pop(0) if pending else "n"

    loop = AgentLoop(
        model=model,
        tools=load_builtin_tools(),
        permissions=PermissionManager(settings=FakeSettings(project), project_root=project),  # type: ignore[arg-type]
        renderer=ConsoleRenderer(console=console),
        system_prompt="SYSTEM",
        session_log=SessionLogger(log_dir=log_dir),
        input_func=input_func,
        **kwargs,
    )
    return loop, model, out


def roles(messages: list[Message]) -> list[str]:
    return [m.role for m in messages]


