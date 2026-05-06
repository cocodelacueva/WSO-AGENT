"""UI de aprobación de permisos en la terminal.

Renderiza el prompt cuando una tool necesita confirmación, lee la
respuesta del usuario, y devuelve la decisión.

Formato esperado en pantalla:

    ⚠ El agente quiere ejecutar:
      delete_file(path="/Users/coco/.../old_draft.pptx")
      Categoría: delete

    [y] aprobar una vez
    [s] aprobar para esta sesión
    [a] aprobar siempre (sticky persistente)
    [n] rechazar
    [escribir feedback] rechazar y mandarle texto al agente

    >
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wso.tools.base import ToolDefinition


class ApprovalChoice(Enum):
    APPROVE_ONCE = "y"
    APPROVE_SESSION = "s"
    APPROVE_ALWAYS = "a"
    DENY = "n"
    DENY_WITH_FEEDBACK = "feedback"


@dataclass
class ApprovalResponse:
    choice: ApprovalChoice
    feedback: str | None = None
    """Texto libre si el usuario eligió DENY_WITH_FEEDBACK."""


def ask_approval(
    tool: ToolDefinition,
    args: dict[str, str],
) -> ApprovalResponse:
    """Mostrar el prompt y esperar la respuesta del usuario.

    TODO(v1): implementar usando rich.prompt.
    """
    raise NotImplementedError("Prompt de aprobación pendiente de implementar.")
