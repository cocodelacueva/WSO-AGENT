"""UI de aprobación de permisos en la terminal.

Cuando una tool necesita confirmación, esta función:
    1. Renderiza un panel con la acción concreta y sus argumentos.
    2. Lee la respuesta del usuario (códigos de un caracter o texto libre).
    3. Devuelve un `ApprovalResponse` estructurado.

Códigos aceptados:
    y       → aprobar una vez (APPROVE_ONCE)
    s       → aprobar para esta sesión (APPROVE_SESSION)
    a       → aprobar siempre, persistente (APPROVE_ALWAYS)
    n       → rechazar (DENY)
    Enter   → equivale a 'n' (DENY)
    [texto] → rechazar y mandar el texto como feedback al modelo

Los códigos son case-insensitive. Cualquier cosa que no sea exactamente
y/s/a/n/empty se trata como feedback.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from wso.tools.base import ToolDefinition


class ApprovalChoice(Enum):
    APPROVE_ONCE = "y"
    APPROVE_SESSION = "s"
    APPROVE_ALWAYS = "a"
    DENY = "n"
    DENY_WITH_FEEDBACK = "feedback"


@dataclass(frozen=True)
class ApprovalResponse:
    choice: ApprovalChoice
    feedback: str | None = None
    """Texto libre si choice == DENY_WITH_FEEDBACK; None en otros casos."""


# Códigos válidos (case-insensitive)
_CODE_TO_CHOICE: dict[str, ApprovalChoice] = {
    "y": ApprovalChoice.APPROVE_ONCE,
    "s": ApprovalChoice.APPROVE_SESSION,
    "a": ApprovalChoice.APPROVE_ALWAYS,
    "n": ApprovalChoice.DENY,
}


def parse_approval_input(raw: str) -> ApprovalResponse:
    """Parsear el input crudo del usuario en una `ApprovalResponse`.

    Lógica:
        - Empty (o solo whitespace) → DENY
        - "y"/"s"/"a"/"n" (case-insensitive) → su choice correspondiente
        - Cualquier otra cosa → DENY_WITH_FEEDBACK con el texto original

    Esta función está separada para que sea testeable sin I/O.
    """
    stripped = raw.strip()
    if not stripped:
        return ApprovalResponse(choice=ApprovalChoice.DENY)

    code = stripped.lower()
    if code in _CODE_TO_CHOICE:
        return ApprovalResponse(choice=_CODE_TO_CHOICE[code])

    return ApprovalResponse(
        choice=ApprovalChoice.DENY_WITH_FEEDBACK,
        feedback=stripped,
    )


def render_approval_panel(
    tool: ToolDefinition,
    args: dict[str, str],
    console: Console,
) -> None:
    """Renderizar el panel de aprobación (sin leer input)."""
    args_str = ", ".join(f"{k}={_truncate(str(v))!r}" for k, v in args.items())

    body = Text()
    body.append("El agente quiere ejecutar:\n\n", style="bold yellow")
    body.append(f"  {tool.name}(", style="cyan")
    body.append(args_str, style="white")
    body.append(")\n\n", style="cyan")
    body.append(f"Categoría: ", style="dim")
    body.append(f"{tool.category.value}\n", style="bold")
    body.append(f"Descripción: ", style="dim")
    body.append(f"{tool.description}\n\n", style="dim italic")
    body.append("Opciones:\n", style="bold")
    body.append("  y       — aprobar una vez\n")
    body.append("  s       — aprobar para esta sesión\n")
    body.append("  a       — aprobar siempre (persiste en config)\n")
    body.append("  n       — rechazar\n")
    body.append("  [texto] — rechazar con feedback al agente\n")

    console.print(Panel(body, title="⚠  Permiso requerido", border_style="yellow"))


def ask_approval(
    tool: ToolDefinition,
    args: dict[str, str],
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
) -> ApprovalResponse:
    """Mostrar prompt de aprobación, leer respuesta del usuario, devolver decisión.

    Args:
        tool: Definición de la tool que requiere aprobación.
        args: Args con los que se va a ejecutar.
        console: Console de Rich para rendering. Se crea uno default si None.
        input_func: Función para leer input. Default: input() builtin.
            Inyectable para facilitar tests.
    """
    if console is None:
        console = Console()
    if input_func is None:
        input_func = input

    render_approval_panel(tool, args, console)
    raw = input_func("> ")
    return parse_approval_input(raw)


def _truncate(value: str, max_length: int = 80) -> str:
    """Truncar valores largos para el display (ej: contenido de write_file)."""
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"
