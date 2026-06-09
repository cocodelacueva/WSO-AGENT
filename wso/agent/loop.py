"""Loop principal del agente.

Este módulo une todas las piezas: model client, parser, registry de
tools, permission manager, budget tracker, y UI renderer.

Flujo conceptual de una sesión:

    run_repl()                              # while True
        ├─ leer user input                   #   (await input())
        ├─ execute_turn(input)               #   nuevo turno
        │   └─ while not terminal:
        │       ├─ chequear budget          #     (continuation prompt si agotado)
        │       └─ _execute_step()          #     una iteración:
        │           ├─ stream del modelo     #       chunks → parser → eventos
        │           ├─ render thinking      #       (live)
        │           ├─ detectar tool call   #       (parar al primer ToolCallComplete)
        │           ├─ chequear permisos    #       (auto / pedir aprobación)
        │           ├─ ejecutar tool        #       (validate_and_call)
        │           ├─ render observation   #       (← resultado)
        │           ├─ append a history     #       (assistant + observation)
        │           └─ si flow tool → end   #       (responder/preguntar)
        └─ repetir
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from wso.agent.budget import BudgetTracker, StepRecord, ask_continuation
from wso.agent.model.base import Message, ModelClient
from wso.agent.parser import (
    ParseError,
    StreamingXMLParser,
    ThinkingChunk,
    ThinkingEnd,
    ToolCallComplete,
)
from wso.history_store import HistoryStore
from wso.permissions.manager import PermissionDecision, PermissionManager
from wso.permissions.prompts import ApprovalChoice, ask_approval
from wso.session_log import SessionLogger
from wso.tools.base import PermissionCategory, ToolDefinition
from wso.tools.registry import ToolRegistry
from wso.ui.console import ConsoleRenderer

_USER_PROMPT_MARKUP = "[bold cyan]›[/] "

# Tras esta cantidad de errores de modelo consecutivos (API caída, sin crédito,
# modelo inexistente, auth), abortamos el turno en vez de reintentar en vano y
# quemar llamadas pagas hasta agotar el budget.
_MAX_CONSECUTIVE_MODEL_ERRORS = 3


@dataclass
class AgentLoop:
    """Orquestador del loop agéntico.

    Dependencias inyectadas en construcción para facilitar testing.

    `session_log` es un `SessionLogger`. Por default es un logger
    deshabilitado (no-op): no escribe nada al disco. Pasale uno
    habilitado para tener registro estructurado de la sesión.
    """

    model: ModelClient
    tools: ToolRegistry
    permissions: PermissionManager
    renderer: ConsoleRenderer
    system_prompt: str
    budget: BudgetTracker = field(default_factory=BudgetTracker)
    history: list[Message] = field(default_factory=list)
    session_log: SessionLogger = field(
        default_factory=lambda: SessionLogger(log_dir=None)
    )
    history_store: HistoryStore = field(
        default_factory=lambda: HistoryStore(path=None)
    )
    """Persistencia del historial entre sesiones. No-op por default."""
    _turn_count: int = field(default=0, init=False, repr=False)
    _turn_started_monotonic: float = field(default=0.0, init=False, repr=False)
    _session_started_monotonic: float = field(default=0.0, init=False, repr=False)
    _consecutive_model_errors: int = field(default=0, init=False, repr=False)
    _last_model_error: str = field(default="", init=False, repr=False)

    # ---- API pública ----

    async def run_repl(self) -> None:
        """Loop conversacional principal: lee input, ejecuta turno, repite."""
        self.renderer.render_info(
            f"Modelo: {self.model.model_name}. "
            f"Tools: {len(self.tools)}. "
            f"Escribí algo (o Ctrl+C para salir)."
        )
        self.renderer.render_separator()

        self._session_started_monotonic = time.monotonic()
        self.session_log.log(
            "session_start",
            model=self.model.model_name,
            tools=[t.name for t in self.tools.all()],
            step_budget=self.budget.limit,
        )

        try:
            while True:
                try:
                    user_input = await self._read_user_input()
                except (EOFError, KeyboardInterrupt):
                    self.renderer.console.print()
                    self.renderer.render_info("Hasta luego.")
                    return

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Comando para olvidar el historial (persistido y en memoria).
                if user_input.lower() in ("/reset", "/olvidar"):
                    self._reset_history()
                    continue

                self.renderer.render_separator()

                try:
                    await self.execute_turn(user_input)
                except KeyboardInterrupt:
                    self.renderer.console.print()
                    self.renderer.render_info("Turno interrumpido.")
                    self.session_log.log("turn_interrupted")
                except Exception as e:  # noqa: BLE001
                    self.renderer.render_error(f"Error inesperado en el turno: {e}")
                    self.session_log.log(
                        "error", where="turn", message=str(e), kind=type(e).__name__
                    )

                self.renderer.render_separator()
        finally:
            self.session_log.log(
                "session_end",
                turns=self._turn_count,
                duration_ms=int((time.monotonic() - self._session_started_monotonic) * 1000),
            )
            self.session_log.close()

    def _reset_history(self) -> None:
        """Olvidar el historial: en memoria y el persistido en disco."""
        self.history.clear()
        self.history_store.clear()
        self.session_log.log("history_reset")
        self.renderer.render_info("Historial borrado. Empezamos de cero.")

    async def execute_turn(self, user_input: str) -> None:
        """Ejecutar un turno completo desde un input del usuario.

        El turno termina cuando el modelo invoca `responder_al_usuario`
        o `preguntar_al_usuario`, o cuando el usuario aborta el budget.
        """
        self._turn_count += 1
        self._turn_started_monotonic = time.monotonic()
        self.budget.start_turn(goal=user_input)
        self.history.append(Message(role="user", content=user_input))
        self.session_log.log("turn_start", turn=self._turn_count, input=user_input)

        try:
            while True:
                # 1. Chequear budget
                if self.budget.is_exhausted():
                    response = ask_continuation(
                        self.budget,
                        console=self.renderer.console,
                        additional=10,
                    )
                    self.session_log.log(
                        "budget_continuation",
                        decision=response.decision,
                        feedback=response.feedback,
                    )
                    if response.decision == "abort":
                        if response.feedback:
                            # Tratar feedback como nuevo objetivo del turno
                            self.history.append(
                                Message(role="user", content=response.feedback)
                            )
                            self.budget.start_turn(goal=response.feedback)
                            continue
                        return
                    self.budget.extend(10)

                # 2. Ejecutar un paso
                terminal = await self._execute_step()
                if terminal:
                    return
        finally:
            self.session_log.log(
                "turn_end",
                turn=self._turn_count,
                steps=len(self.budget.history),
                duration_ms=int((time.monotonic() - self._turn_started_monotonic) * 1000),
            )
            # Persistir el historial tras cada turno (no-op si está deshabilitado).
            self.history_store.save(self.history)

    # ---- Loop interno: un paso ----

    async def _execute_step(self) -> bool:
        """Ejecutar una iteración del loop: model → parser → tool → observation.

        Returns:
            True si el turno terminó (flow tool invocada o error fatal),
            False si el loop debe continuar.
        """
        executed_tool = await self._run_model_until_tool()
        if executed_tool is None:
            # Si el modelo viene fallando con errores duros (no un simple
            # "no emitió tool"), abortamos en vez de reintentar al pedo.
            if self._consecutive_model_errors >= _MAX_CONSECUTIVE_MODEL_ERRORS:
                return self._abort_on_model_errors()
            return self._handle_no_tool_emitted()

        return await self._handle_tool_call(executed_tool)

    def _abort_on_model_errors(self) -> bool:
        """Terminar el turno tras errores de modelo consecutivos irrecuperables."""
        self.renderer.render_error(
            f"El modelo falló {self._consecutive_model_errors} veces seguidas. "
            f"Abortando el turno. Último error: {self._last_model_error}"
        )
        self.session_log.log(
            "error",
            where="model_stream_abort",
            message=self._last_model_error,
            count=self._consecutive_model_errors,
        )
        return True

    async def _run_model_until_tool(self) -> ToolCallComplete | None:
        """Llamar al modelo, parsear streaming, parar al primer tool.

        Renderiza thinking en vivo. Anexa la respuesta completa al history
        como mensaje del assistant.

        Returns:
            La tool detectada, o None si el modelo no emitió ninguna.
        """
        parser = StreamingXMLParser()
        executed_tool: ToolCallComplete | None = None
        full_response = ""

        messages = [
            Message(role="system", content=self.system_prompt),
            *self.history,
        ]

        try:
            async for chunk in self.model.stream_chat(messages):
                full_response += chunk
                for event in parser.feed(chunk):
                    if isinstance(event, ThinkingChunk):
                        self.renderer.render_thinking_chunk(event.text)
                    elif isinstance(event, ThinkingEnd):
                        self.renderer.render_thinking_end()
                    elif isinstance(event, ToolCallComplete):
                        executed_tool = event
                        break
                    elif isinstance(event, ParseError):
                        self.renderer.render_error(f"Parser: {event.message}")
                if executed_tool is not None:
                    break

            # Flush parser: si quedó algún thinking sin cerrar
            for event in parser.finalize():
                if isinstance(event, ThinkingChunk):
                    self.renderer.render_thinking_chunk(event.text)
                elif isinstance(event, ThinkingEnd):
                    self.renderer.render_thinking_end()
        except Exception as e:  # noqa: BLE001
            self.renderer.render_error(f"Error de modelo: {e}")
            self.session_log.log(
                "error", where="model_stream", message=str(e), kind=type(e).__name__
            )
            self._consecutive_model_errors += 1
            self._last_model_error = str(e)
            # Anexar error como observación para que el modelo lo vea si reintentamos
            self.history.append(
                Message(role="assistant", content=full_response or "(sin respuesta)")
            )
            return None

        # Respuesta recibida sin excepción: reseteamos el contador de errores.
        self._consecutive_model_errors = 0
        self.history.append(Message(role="assistant", content=full_response))
        self.session_log.log("model_response", text=full_response)
        return executed_tool

    def _handle_no_tool_emitted(self) -> bool:
        """Recovery cuando el modelo no emitió ninguna tool.

        Le mandamos un mensaje recordándole el contrato y permitimos que
        reintente. Cuenta como un step usado.
        """
        self.renderer.render_error(
            "El modelo no emitió ninguna tool. Reintentando con feedback."
        )
        self.session_log.log("error", where="parser", message="no tool emitted")
        self.history.append(
            Message(
                role="user",
                content=(
                    "ERROR: tu respuesta no contenía ningún <tool>. "
                    "Recordá: cada respuesta DEBE incluir exactamente un <tool>. "
                    "Si querés decirme algo, usá responder_al_usuario."
                ),
            )
        )
        self.budget.record(
            StepRecord(
                tool_name="(none)",
                args_summary="",
                success=False,
                error="no tool emitted",
            )
        )
        return False

    async def _handle_tool_call(self, tool_call: ToolCallComplete) -> bool:
        """Procesar una tool call: lookup → permisos → ejecutar → observation.

        Returns:
            True si la tool fue terminal (responder/preguntar).
        """
        self.session_log.log(
            "tool_call", name=tool_call.name, args=tool_call.args
        )

        tool_def = self.tools.get(tool_call.name)
        if tool_def is None:
            return self._handle_unknown_tool(tool_call)

        # Permission check
        if not self._check_or_request_permission(tool_def, tool_call.args):
            return False

        # Render tool call (después de aprobación)
        self.renderer.render_tool_call(tool_call.name, tool_call.args)

        # Ejecutar
        result, success, error_msg = self._execute_tool(tool_def, tool_call.args)
        self.session_log.log(
            "observation",
            tool=tool_call.name,
            success=success,
            result=str(result),
            error=error_msg,
        )

        # Registrar en el budget
        self.budget.record(
            StepRecord(
                tool_name=tool_call.name,
                args_summary=_format_args_summary(tool_call.args),
                success=success,
                error=error_msg,
            )
        )

        # Flow tools: render distintivo + terminar turno
        if tool_def.category == PermissionCategory.FLOW:
            return self._handle_flow_tool(tool_call.name, str(result), success)

        # Tools normales: render observation + anexar a history
        result_str = str(result)
        self.renderer.render_observation(
            tool_call.name, result_str, is_error=not success
        )
        self.history.append(
            Message(
                role="user",
                content=_format_observation(tool_call.name, result_str),
            )
        )
        return False

    def _handle_unknown_tool(self, tool_call: ToolCallComplete) -> bool:
        """Tool con nombre que no existe en el registry."""
        self.renderer.render_error(f"Tool desconocida: {tool_call.name!r}")
        self.session_log.log(
            "error", where="tool_lookup", tool=tool_call.name, message="unknown tool"
        )
        available = ", ".join(t.name for t in self.tools.all())
        self.history.append(
            Message(
                role="user",
                content=_format_observation(
                    tool_call.name,
                    f"ERROR: tool {tool_call.name!r} no existe. "
                    f"Tools disponibles: {available}.",
                ),
            )
        )
        self.budget.record(
            StepRecord(
                tool_name=tool_call.name,
                args_summary="",
                success=False,
                error="unknown tool",
            )
        )
        return False

    def _check_or_request_permission(
        self,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> bool:
        """Aplicar gating de permisos. Retorna True si la tool puede ejecutar."""
        decision = self.permissions.check(tool_def, args)
        if decision == PermissionDecision.AUTO_APPROVED:
            self.session_log.log(
                "permission", tool=tool_def.name, decision="auto_approved"
            )
            return True

        # NEEDS_APPROVAL: pedirle al usuario
        response = ask_approval(
            tool_def,
            args,
            console=self.renderer.console,
        )
        choice_value = (
            response.choice.value
            if hasattr(response.choice, "value")
            else str(response.choice)
        )
        self.session_log.log(
            "permission",
            tool=tool_def.name,
            decision=choice_value,
            feedback=response.feedback,
        )

        if response.choice == ApprovalChoice.DENY:
            self.history.append(
                Message(
                    role="user",
                    content=_format_observation(
                        tool_def.name,
                        "DENIED: el usuario rechazó esta acción.",
                    ),
                )
            )
            self.budget.record(
                StepRecord(
                    tool_name=tool_def.name,
                    args_summary=_format_args_summary(args),
                    success=False,
                    error="denied by user",
                )
            )
            return False

        if response.choice == ApprovalChoice.DENY_WITH_FEEDBACK:
            feedback = response.feedback or "(sin feedback)"
            self.history.append(
                Message(
                    role="user",
                    content=_format_observation(
                        tool_def.name,
                        f"DENIED por el usuario con feedback: {feedback}",
                    ),
                )
            )
            self.budget.record(
                StepRecord(
                    tool_name=tool_def.name,
                    args_summary=_format_args_summary(args),
                    success=False,
                    error=f"denied: {feedback}",
                )
            )
            return False

        # Aprobaciones que persisten.
        # Las tools BROWSER nunca persisten entre sesiones (decisión A.5):
        # tanto 's' como 'a' se recuerdan solo por sesión, con sticky por
        # dominio cuando la tool navega a una URL.
        is_browser = tool_def.category == PermissionCategory.BROWSER
        if response.choice == ApprovalChoice.APPROVE_SESSION:
            if is_browser:
                self.permissions.remember_browser_session(tool_def.name, args)
            else:
                self.permissions.remember_session(tool_def.name, args)
        elif response.choice == ApprovalChoice.APPROVE_ALWAYS:
            if is_browser:
                self.renderer.render_info(
                    "Las acciones de browser solo se recuerdan por sesión "
                    "(no se persisten). Aplicando para esta sesión."
                )
                self.permissions.remember_browser_session(tool_def.name, args)
            else:
                self.permissions.remember_always(tool_def.name, args)
        # APPROVE_ONCE: simplemente seguimos

        return True

    def _execute_tool(
        self,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> tuple[Any, bool, str | None]:
        """Ejecutar la tool capturando excepciones.

        Returns:
            (resultado, success, error_msg). En caso de error, resultado
            es un string formateado tipo "ERROR: ..." que se le devuelve
            al modelo como observation.
        """
        try:
            result = tool_def.validate_and_call(args)
            return result, True, None
        except Exception as e:  # noqa: BLE001
            error_text = f"ERROR: {type(e).__name__}: {e}"
            return error_text, False, str(e)

    def _handle_flow_tool(self, name: str, result: str, success: bool) -> bool:
        """Manejar tools terminales (responder_al_usuario, preguntar_al_usuario)."""
        if not success:
            # Si la tool de flow falló (raro), mostrar error y terminar
            self.renderer.render_error(f"Falló {name}: {result}")
            return True

        if name == "responder_al_usuario":
            self.renderer.render_final_answer(result)
            return True

        if name == "preguntar_al_usuario":
            try:
                data = json.loads(result)
                self.renderer.render_question(
                    question=data["pregunta"],
                    options=data.get("opciones", []),
                    permite_respuesta_libre=data.get("permite_respuesta_libre", True),
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                self.renderer.render_error(
                    f"preguntar_al_usuario devolvió un formato inválido: {e}"
                )
            return True

        # No debería pasar — es un flow tool desconocido
        self.renderer.render_error(f"Flow tool no manejada: {name}")
        return True

    # ---- I/O ----

    async def _read_user_input(self) -> str:
        """Leer input del usuario, juntando líneas pegadas como un solo mensaje.

        Usa `asyncio.to_thread` para no bloquear el event loop con `input()`.
        Después de la primera línea, chequea si hay más data buffereada
        en stdin (señal de paste multilínea) y la concatena. Si el usuario
        solo tipeó una línea y presionó Enter, devuelve solo eso.
        """
        return await asyncio.to_thread(self._read_user_input_sync)

    def _read_user_input_sync(self) -> str:
        """Versión bloqueante: lee primera línea, después drena el resto del paste.

        Un paste multilínea cuya ÚLTIMA línea no termina en '\\n' (no apretaste
        Enter al final) queda parcialmente retenido por el modo canónico del
        terminal: las líneas completas se entregan, pero la última sin newline
        se queda en el buffer. Para capturarla, drenamos lo pendiente en modo
        no-canónico (ver `_drain_pending`). Sin esto, prompts largos pegados se
        cortaban en la última línea — bug real observado en pruebas.
        """
        import select
        import sys

        first = self.renderer.console.input(_USER_PROMPT_MARKUP)

        if not sys.stdin.isatty():
            return first

        # Detección de paste con latencia cero para input normal: si justo
        # después del Enter NO hay más data pendiente, fue una sola línea.
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        except (OSError, ValueError):
            return first
        if not ready:
            return first

        lines = [first]

        # 1) Drenar las líneas COMPLETAS (terminadas en '\n') vía readline.
        #    Este paso ya funcionaba; nunca lo regresamos.
        while True:
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (OSError, ValueError):
                break
            if not ready:
                break
            try:
                line = sys.stdin.readline()
            except (EOFError, OSError):
                break
            if not line:
                break
            lines.append(line.rstrip("\n"))

        # 2) Capturar una posible ÚLTIMA línea sin '\n' que el modo canónico
        #    retiene esperando un Enter (típico al pegar sin newline final).
        tail = _read_incomplete_tail(sys.stdin.fileno())
        if tail:
            lines.extend(tail.split("\n"))

        if len(lines) > 1:
            self.renderer.render_info(
                f"(detecté paste de {len(lines)} líneas, juntando como un mensaje)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers de módulo
# ---------------------------------------------------------------------------


def _format_args_summary(args: dict[str, Any], max_value_length: int = 30) -> str:
    """Resumen truncado de args para el budget history."""
    parts: list[str] = []
    for key, value in args.items():
        text = str(value)
        if len(text) > max_value_length:
            text = text[: max_value_length - 1] + "…"
        parts.append(f"{key}={text!r}")
    return ", ".join(parts)


def _format_observation(tool_name: str, content: str) -> str:
    """Formatear el resultado de una tool como bloque <observation>."""
    return f'<observation tool="{tool_name}">\n{content}\n</observation>'


def _read_incomplete_tail(fd: int) -> str:  # pragma: no cover — I/O de terminal
    """Leer una última línea sin '\\n' que el modo canónico retiene en el buffer.

    Cambia la tty a modo no-canónico con `TCSANOW` (que NO descarta el buffer
    de entrada, a diferencia del `TCSAFLUSH` default de `tty.setcbreak`) para
    que `os.read` entregue los bytes pendientes, y restaura el modo original.
    Si termios no está disponible (Windows) o algo falla, devuelve "" — en ese
    caso el caller se queda con las líneas completas que ya drenó (sin regresión).
    """
    import os
    import select

    try:
        import termios
    except ImportError:  # Windows
        return ""

    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return ""

    # Construimos los atributos no-canónicos a mano y aplicamos con TCSANOW
    # para no flushear el input pendiente.
    new = termios.tcgetattr(fd)
    new[3] = new[3] & ~(termios.ICANON | termios.ECHO)  # lflags
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0

    chunks: list[str] = []
    try:
        termios.tcsetattr(fd, termios.TCSANOW, new)
        while True:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                break
            data = os.read(fd, 4096)
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
    except OSError:
        pass
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, old)

    return "".join(chunks).rstrip("\n")
