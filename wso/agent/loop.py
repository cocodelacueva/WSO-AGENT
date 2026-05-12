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
import json
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
from wso.permissions.manager import PermissionDecision, PermissionManager
from wso.permissions.prompts import ApprovalChoice, ask_approval
from wso.tools.base import PermissionCategory, ToolDefinition
from wso.tools.registry import ToolRegistry
from wso.ui.console import ConsoleRenderer

_USER_PROMPT_MARKUP = "[bold cyan]›[/] "


@dataclass
class AgentLoop:
    """Orquestador del loop agéntico.

    Dependencias inyectadas en construcción para facilitar testing.
    """

    model: ModelClient
    tools: ToolRegistry
    permissions: PermissionManager
    renderer: ConsoleRenderer
    system_prompt: str
    budget: BudgetTracker = field(default_factory=BudgetTracker)
    history: list[Message] = field(default_factory=list)

    # ---- API pública ----

    async def run_repl(self) -> None:
        """Loop conversacional principal: lee input, ejecuta turno, repite."""
        self.renderer.render_info(
            f"Modelo: {self.model.model_name}. "
            f"Tools: {len(self.tools)}. "
            f"Escribí algo (o Ctrl+C para salir)."
        )
        self.renderer.render_separator()

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

            self.renderer.render_separator()

            try:
                await self.execute_turn(user_input)
            except KeyboardInterrupt:
                self.renderer.console.print()
                self.renderer.render_info("Turno interrumpido.")
            except Exception as e:  # noqa: BLE001
                self.renderer.render_error(f"Error inesperado en el turno: {e}")

            self.renderer.render_separator()

    async def execute_turn(self, user_input: str) -> None:
        """Ejecutar un turno completo desde un input del usuario.

        El turno termina cuando el modelo invoca `responder_al_usuario`
        o `preguntar_al_usuario`, o cuando el usuario aborta el budget.
        """
        self.budget.start_turn(goal=user_input)
        self.history.append(Message(role="user", content=user_input))

        while True:
            # 1. Chequear budget
            if self.budget.is_exhausted():
                response = ask_continuation(
                    self.budget,
                    console=self.renderer.console,
                    additional=10,
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

    # ---- Loop interno: un paso ----

    async def _execute_step(self) -> bool:
        """Ejecutar una iteración del loop: model → parser → tool → observation.

        Returns:
            True si el turno terminó (flow tool invocada o error fatal),
            False si el loop debe continuar.
        """
        executed_tool = await self._run_model_until_tool()
        if executed_tool is None:
            return self._handle_no_tool_emitted()

        return await self._handle_tool_call(executed_tool)

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
            # Anexar error como observación para que el modelo lo vea si reintentamos
            self.history.append(
                Message(role="assistant", content=full_response or "(sin respuesta)")
            )
            return None

        self.history.append(Message(role="assistant", content=full_response))
        return executed_tool

    def _handle_no_tool_emitted(self) -> bool:
        """Recovery cuando el modelo no emitió ninguna tool.

        Le mandamos un mensaje recordándole el contrato y permitimos que
        reintente. Cuenta como un step usado.
        """
        self.renderer.render_error(
            "El modelo no emitió ninguna tool. Reintentando con feedback."
        )
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
            return True

        # NEEDS_APPROVAL: pedirle al usuario
        response = ask_approval(
            tool_def,
            args,
            console=self.renderer.console,
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

        # Aprobaciones que persisten
        if response.choice == ApprovalChoice.APPROVE_SESSION:
            self.permissions.remember_session(tool_def.name, args)
        elif response.choice == ApprovalChoice.APPROVE_ALWAYS:
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
        """Leer una línea del usuario con prompt formateado.

        Usa `asyncio.to_thread` para no bloquear el event loop con `input()`.
        """
        return await asyncio.to_thread(
            self.renderer.console.input, _USER_PROMPT_MARKUP
        )


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
