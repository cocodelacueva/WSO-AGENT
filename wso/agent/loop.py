"""Loop principal del agente.

Este módulo une todas las piezas:
    config + model client + parser + tool registry + permissions + UI

Flujo de un turno:
    1. Recibir input del usuario
    2. Armar mensajes (system + historial + input)
    3. Llamar al modelo en streaming
    4. Pasar chunks al parser → emite eventos
    5. Renderizar thinking en vivo
    6. Cuando llega una tool completa:
        a. Chequear permiso (auto / pedir aprobación)
        b. Ejecutar tool (o rechazar)
        c. Anexar <observation> al historial
        d. Decrementar budget
        e. Si budget agotado, prompt de continuación
    7. Si la tool fue responder_al_usuario o preguntar_al_usuario,
       cerrar el turno
    8. Si no, continuar el loop con el contexto actualizado
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from wso.agent.budget import BudgetTracker
from wso.agent.model.base import ModelClient
from wso.permissions.manager import PermissionManager
from wso.tools.registry import ToolRegistry
from wso.ui.console import ConsoleRenderer


@dataclass
class Message:
    """Un mensaje en el historial de la conversación."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class AgentLoop:
    """Orquestador del loop agéntico.

    Dependencias inyectadas en construcción para facilitar testing.
    """

    model: ModelClient
    tools: ToolRegistry
    permissions: PermissionManager
    renderer: ConsoleRenderer
    budget: BudgetTracker = field(default_factory=BudgetTracker)
    history: list[Message] = field(default_factory=list)

    async def run_repl(self) -> None:
        """Loop conversacional principal: lee input del usuario, ejecuta turno, repite.

        TODO(v1): implementar.
        """
        raise NotImplementedError("REPL pendiente de implementar.")

    async def execute_turn(self, user_input: str) -> None:
        """Ejecutar un turno completo hasta que el modelo cierre con
        responder_al_usuario o preguntar_al_usuario.

        TODO(v1): implementar.
        """
        raise NotImplementedError("Ejecución de turno pendiente de implementar.")
