"""Loop principal del agente.

Une las piezas: model client, registry de tools, permisos, contexto,
budget y UI.

Flujo de un turno (mismo modelo que Claude Code):

    execute_turn(input)
        └─ while True:
            ├─ ajustar el historial a la ventana de contexto
            ├─ llamar al modelo (streaming: texto y thinking en vivo)
            ├─ ¿pidió tools?
            │     no → su texto es la respuesta: fin del turno
            │     sí → para cada tool call: permisos → ejecutar → resultado
            └─ repetir con los resultados en el historial

El turno termina cuando el modelo responde sin pedir tools, cuando el
usuario rechaza una acción (sin feedback), o cuando el usuario decide no
seguir tras agotar el budget de pasos.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from wso.agent.budget import BudgetTracker, StepRecord, ask_continuation
from wso.agent.context import ContextManager
from wso.agent.model.base import (
    Message,
    ModelClient,
    ModelError,
    ResponseDone,
    TextDelta,
    ThinkingDelta,
    ToolCall,
)
from wso.agent.salvage import salvage_tool_calls
from wso.history_store import HistoryStore
from wso.permissions.manager import PermissionDecision, PermissionManager
from wso.permissions.prompts import ApprovalChoice, ask_approval
from wso.session_log import SessionLogger
from wso.tools import shell
from wso.tools.base import ToolDefinition
from wso.tools.registry import ToolRegistry
from wso.ui.console import ConsoleRenderer

# Errores de modelo seguidos antes de abortar el turno (API caída, etc).
_MAX_CONSECUTIVE_MODEL_ERRORS = 3

_DENIED = "El usuario rechazó esta acción. No sigas: esperá nuevas instrucciones."
_CANCELLED = "No se ejecutó: el turno se cortó antes."
_EMPTY_NUDGE = (
    "Tu respuesta llegó vacía. Seguí con la tarea usando las tools, o respondé "
    "en texto si ya terminaste."
)


@dataclass
class AgentLoop:
    """Orquestador del loop agéntico. Dependencias inyectadas para testing."""

    model: ModelClient
    tools: ToolRegistry
    permissions: PermissionManager
    renderer: ConsoleRenderer
    system_prompt: str
    budget: BudgetTracker = field(default_factory=BudgetTracker)
    history: list[Message] = field(default_factory=list)
    session_log: SessionLogger = field(default_factory=lambda: SessionLogger(log_dir=None))
    history_store: HistoryStore = field(default_factory=lambda: HistoryStore(path=None))
    context: ContextManager | None = None
    """None = se arma desde `model.context_window`."""
    step_budget: int = 25
    input_func: Any = None
    """Para leer aprobaciones/continuaciones. None = `input` (inyectable en tests)."""

    _turn_count: int = field(default=0, init=False, repr=False)
    _consecutive_model_errors: int = field(default=0, init=False, repr=False)
    _prompt_session: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.context is None:
            self.context = ContextManager.for_window(self.model.context_window)
        self._specs = self.tools.specs()

    # ---- API pública ----

    async def run_repl(self) -> None:
        """Loop conversacional principal: lee input, ejecuta turno, repite."""
        self.renderer.render_info(
            f"Modelo: {self.model.model_name} · contexto {_k(self.context.window)} tokens. "
            "Ctrl+C interrumpe el turno; Ctrl+D sale. /reset borra el historial."
        )
        self.renderer.render_separator()
        started = time.monotonic()
        self.session_log.log(
            "session_start",
            model=self.model.model_name,
            tools=[t.name for t in self.tools.all()],
            context_window=self.context.window,
        )
        try:
            while True:
                try:
                    user_input = (await self._read_user_input()).strip()
                except (EOFError, KeyboardInterrupt):
                    self.renderer.render_info("Hasta luego.")
                    return
                if not user_input:
                    continue
                if user_input.lower() in ("/reset", "/olvidar", "/clear"):
                    self._reset_history()
                    continue
                if user_input.lower() in ("/exit", "/salir", "/quit"):
                    return

                try:
                    await self.execute_turn(user_input)
                except Exception as e:  # noqa: BLE001
                    self.renderer.render_error(f"Error inesperado en el turno: {e}")
                    self.session_log.log(
                        "error", where="turn", message=str(e), kind=type(e).__name__
                    )
                self._render_context_usage()
                self.renderer.render_separator()
        finally:
            self.session_log.log(
                "session_end",
                turns=self._turn_count,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self.session_log.close()

    async def execute_turn(self, user_input: str) -> None:
        """Ejecutar un turno completo desde un input del usuario."""
        self._turn_count += 1
        started = time.monotonic()
        self.budget.start_turn(goal=user_input, limit=self.step_budget)
        self.history.append(Message(role="user", content=user_input))
        self.session_log.log("turn_start", turn=self._turn_count, input=user_input)
        self._consecutive_model_errors = 0
        nudged = False

        try:
            while True:
                if self.budget.is_exhausted() and not self._ask_continue():
                    return
                outcome = await self._step(allow_nudge=not nudged)
                if outcome == "nudged":
                    nudged = True
                elif outcome == "done":
                    return
        except (KeyboardInterrupt, asyncio.CancelledError):
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            shell.kill_running()
            self.renderer.render_info("Turno interrumpido.")
            self.session_log.log("turn_interrupted")
        finally:
            self.renderer.end_stream()
            self._close_pending_tool_calls()
            self.session_log.log(
                "turn_end",
                turn=self._turn_count,
                steps=len(self.budget.history),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self.history_store.save(self.history)

    # ---- Un paso: modelo → tools ----

    async def _step(self, allow_nudge: bool) -> str:
        """Una iteración. Devuelve 'continue', 'done' o 'nudged'."""
        self._fit_context()

        response = await self._call_model()
        if response is None:
            return "done" if self._should_abort() else "continue"

        message = response.message
        if not message.tool_calls and message.content:
            calls, rest = salvage_tool_calls(message.content, (t.name for t in self.tools.all()))
            if calls:
                self.session_log.log("salvaged_tool_calls", count=len(calls))
                message = Message(role="assistant", content=rest, tool_calls=calls)

        self.history.append(message)
        self.session_log.log(
            "model_response",
            text=message.content,
            tool_calls=[{"name": c.name, "args": c.args} for c in message.tool_calls],
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_tokens,
        )

        if not message.tool_calls:
            if message.content.strip():
                return "done"
            if allow_nudge:
                self.history.append(Message(role="user", content=_EMPTY_NUDGE))
                return "nudged"
            self.renderer.render_error("El modelo devolvió una respuesta vacía.")
            return "done"

        if response.truncated:
            # La respuesta se cortó por límite de salida: los args pueden estar
            # incompletos (ej: un write_file a medias). No ejecutar.
            for call in message.tool_calls:
                self._append_result(
                    call,
                    "ERROR: tu respuesta se cortó por el límite de tokens de salida y "
                    "esta tool call quedó incompleta. Hacé cambios más chicos "
                    "(edit_file por partes en vez de write_file de un archivo entero).",
                    is_error=True,
                )
            return "continue"

        for i, call in enumerate(message.tool_calls):
            keep_going = await self._run_tool_call(call)
            if not keep_going:
                for skipped in message.tool_calls[i + 1 :]:
                    self._append_result(skipped, _CANCELLED, is_error=True)
                return "done"
        return "continue"

    async def _call_model(self) -> ResponseDone | None:
        """Llamar al modelo en streaming. None si falló."""
        messages = [Message(role="system", content=self.system_prompt), *self.history]
        final: ResponseDone | None = None
        try:
            with self.renderer.status("pensando…") as _:
                stream = self.model.stream(messages, self._specs)
                first = await anext(stream, None)
            events = [first] if first is not None else []
            async for event in _chain(events, stream):
                if isinstance(event, TextDelta):
                    self.renderer.render_text_chunk(event.text)
                elif isinstance(event, ThinkingDelta):
                    self.renderer.render_thinking_chunk(event.text)
                elif isinstance(event, ResponseDone):
                    final = event
        except ModelError as e:
            self._on_model_error(str(e), retryable=e.retryable)
            return None
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001 — bug o error inesperado del SDK
            self._on_model_error(f"{type(e).__name__}: {e}", retryable=True)
            return None
        finally:
            self.renderer.end_stream()

        if final is None:
            self._on_model_error("El stream terminó sin respuesta final.", retryable=True)
            return None
        self._consecutive_model_errors = 0
        return final

    def _on_model_error(self, message: str, *, retryable: bool) -> None:
        self.renderer.render_error(f"Error de modelo: {message}")
        self.session_log.log("error", where="model", message=message, retryable=retryable)
        self._consecutive_model_errors = (
            _MAX_CONSECUTIVE_MODEL_ERRORS if not retryable else self._consecutive_model_errors + 1
        )

    def _should_abort(self) -> bool:
        if self._consecutive_model_errors >= _MAX_CONSECUTIVE_MODEL_ERRORS:
            self.renderer.render_error("Abortando el turno por errores del modelo.")
            return True
        return False

    # ---- Ejecución de una tool call ----

    async def _run_tool_call(self, call: ToolCall) -> bool:
        """Permisos → ejecutar → resultado. False si el turno debe terminar."""
        self.session_log.log("tool_call", id=call.id, name=call.name, args=call.args)

        tool_def = self.tools.get(call.name)
        if tool_def is None:
            available = ", ".join(t.name for t in self.tools.all())
            self._append_result(
                call, f"ERROR: la tool {call.name!r} no existe. Disponibles: {available}.",
                is_error=True,
            )
            self.budget.record(StepRecord(call.name, "", success=False, error="unknown tool"))
            return True

        approval = self._check_permission(tool_def, call.args)
        if approval is not None:
            choice, feedback = approval
            summary = _format_args_summary(call.args)
            if choice == ApprovalChoice.DENY:
                self._append_result(call, _DENIED, is_error=True)
                self.budget.record(StepRecord(call.name, summary, False, "denied"))
                return False
            if choice == ApprovalChoice.DENY_WITH_FEEDBACK:
                self._append_result(
                    call,
                    f"El usuario rechazó esta acción y dice: {feedback}",
                    is_error=True,
                )
                self.budget.record(StepRecord(call.name, summary, False, f"denied: {feedback}"))
                return True

        self.renderer.render_tool_call(call.name, call.args)
        try:
            with self.renderer.status(f"{call.name}…"):
                result = await asyncio.to_thread(tool_def.validate_and_call, call.args)
            text, ok, error = str(result), True, None
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as e:  # noqa: BLE001 — el error vuelve al modelo
            text, ok, error = f"ERROR: {type(e).__name__}: {e}", False, str(e)

        self.renderer.render_observation(text, is_error=not ok)
        self._append_result(call, text, is_error=not ok)
        self.session_log.log("observation", id=call.id, tool=call.name, success=ok, result=text)
        self.budget.record(StepRecord(call.name, _format_args_summary(call.args), ok, error))
        return True

    def _check_permission(
        self, tool_def: ToolDefinition, args: dict[str, Any]
    ) -> tuple[ApprovalChoice, str | None] | None:
        """None si puede ejecutar; (choice, feedback) si el usuario rechazó."""
        if self.permissions.check(tool_def, args) == PermissionDecision.AUTO_APPROVED:
            self.session_log.log("permission", tool=tool_def.name, decision="auto")
            return None

        self.renderer.end_stream()
        response = ask_approval(
            tool_def,
            args,
            console=self.renderer.console,
            input_func=self.input_func,
            session_hint=self.permissions.session_scope_hint(tool_def, args),
        )
        self.session_log.log(
            "permission", tool=tool_def.name, decision=response.choice.value,
            feedback=response.feedback,
        )
        if response.choice == ApprovalChoice.APPROVE_SESSION:
            self.permissions.remember_session(tool_def, args)
        elif response.choice == ApprovalChoice.APPROVE_ALWAYS:
            self.permissions.remember_always(tool_def, args)
        elif response.choice in (ApprovalChoice.DENY, ApprovalChoice.DENY_WITH_FEEDBACK):
            return response.choice, response.feedback
        return None

    # ---- Historial ----

    def _append_result(self, call: ToolCall, content: str, *, is_error: bool = False) -> None:
        self.history.append(
            Message(
                role="tool", content=content, tool_call_id=call.id, name=call.name,
                is_error=is_error,
            )
        )

    def _close_pending_tool_calls(self) -> None:
        """Toda tool call necesita su resultado (las APIs lo exigen). Si el turno
        se cortó a mitad (Ctrl+C), completar las que quedaron sin respuesta."""
        answered = {m.tool_call_id for m in self.history if m.role == "tool"}
        for m in reversed(self.history):
            if m.role == "assistant":
                for call in m.tool_calls:
                    if call.id not in answered:
                        self._append_result(call, _CANCELLED, is_error=True)
                break

    def _fit_context(self) -> None:
        report = self.context.fit(self.system_prompt, self._specs, self.history)
        if report.changed:
            self.renderer.render_info(
                f"[contexto: ~{_k(report.tokens_before)} → ~{_k(report.tokens_after)} tokens; "
                f"{report.elided} resultados resumidos, "
                f"{report.dropped} mensajes viejos descartados]"
            )
            self.session_log.log(
                "context_fit", before=report.tokens_before, after=report.tokens_after,
                elided=report.elided, dropped=report.dropped,
            )

    def _render_context_usage(self) -> None:
        used = self.context.estimate(self.system_prompt, self._specs, self.history)
        self.renderer.render_info(f"contexto ~{_k(used)}/{_k(self.context.window)} tokens")

    def _reset_history(self) -> None:
        self.history.clear()
        self.history_store.clear()
        self.session_log.log("history_reset")
        self.renderer.render_info("Historial borrado. Empezamos de cero.")

    # ---- Budget ----

    def _ask_continue(self) -> bool:
        response = ask_continuation(
            self.budget,
            console=self.renderer.console,
            input_func=self.input_func,
            additional=self.step_budget,
        )
        self.session_log.log(
            "budget_continuation", decision=response.decision, feedback=response.feedback
        )
        if response.decision == "continue":
            self.budget.extend(self.step_budget)
            return True
        if response.feedback:
            # El feedback se convierte en una instrucción nueva dentro del turno.
            self._close_pending_tool_calls()
            self.history.append(Message(role="user", content=response.feedback))
            self.budget.start_turn(goal=response.feedback, limit=self.step_budget)
            return True
        return False

    # ---- I/O ----

    async def _read_user_input(self) -> str:
        """Leer input con prompt_toolkit (pegado multilínea, historial, edición)."""
        session = self._ensure_prompt_session()
        if session is None:  # pragma: no cover — fallback sin prompt_toolkit
            return await asyncio.to_thread(input, "› ")
        from prompt_toolkit.formatted_text import HTML  # noqa: PLC0415

        return await session.prompt_async(HTML("<ansicyan><b>› </b></ansicyan>"))

    def _ensure_prompt_session(self) -> object | None:
        if self._prompt_session is None:
            try:
                from prompt_toolkit import PromptSession  # noqa: PLC0415

                self._prompt_session = PromptSession()
            except ImportError:  # pragma: no cover
                return None
        return self._prompt_session


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


async def _chain(first: list[Any], rest: Any) -> Any:
    for event in first:
        yield event
    async for event in rest:
        yield event


def _format_args_summary(args: dict[str, Any], max_value_length: int = 30) -> str:
    """Resumen truncado de args para el panel del budget."""
    parts: list[str] = []
    for key, value in args.items():
        text = str(value).replace("\n", " ")
        if len(text) > max_value_length:
            text = text[: max_value_length - 1] + "…"
        parts.append(f"{key}={text!r}")
    return ", ".join(parts)


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)
