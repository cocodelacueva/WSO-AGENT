"""UI de aprobación de permisos en la terminal.

Cuando una tool necesita confirmación, esta función:
    1. Renderiza un panel con la acción concreta: el diff para ediciones,
       el contenido para archivos nuevos, el comando para bash.
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

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from wso.tools.base import ToolDefinition
from wso.tools.filesystem import apply_edit, display_path, resolve_path


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


_CODE_TO_CHOICE: dict[str, ApprovalChoice] = {
    "y": ApprovalChoice.APPROVE_ONCE,
    "s": ApprovalChoice.APPROVE_SESSION,
    "a": ApprovalChoice.APPROVE_ALWAYS,
    "n": ApprovalChoice.DENY,
}

_MAX_PREVIEW_LINES = 60


def parse_approval_input(raw: str) -> ApprovalResponse:
    """Parsear el input crudo del usuario en una `ApprovalResponse`.

    Separada para que sea testeable sin I/O.
    """
    stripped = raw.strip()
    if not stripped:
        return ApprovalResponse(choice=ApprovalChoice.DENY)
    code = stripped.lower()
    if code in _CODE_TO_CHOICE:
        return ApprovalResponse(choice=_CODE_TO_CHOICE[code])
    return ApprovalResponse(choice=ApprovalChoice.DENY_WITH_FEEDBACK, feedback=stripped)


# ---------------------------------------------------------------------------
# Preview de la acción
# ---------------------------------------------------------------------------


def build_preview(tool_name: str, args: dict[str, Any]) -> Text:
    """Representación legible de lo que la tool va a hacer."""
    if tool_name == "bash":
        return Text(f"$ {args.get('command', '')}", style="bold white")
    if tool_name == "edit_file":
        return _preview_edit(args)
    if tool_name == "write_file":
        return _preview_write(args)
    lines = [f"{k} = {_truncate(str(v))}" for k, v in args.items()]
    return Text("\n".join(lines) or "(sin argumentos)")


def _preview_edit(args: dict[str, Any]) -> Text:
    path = resolve_path(str(args.get("path", "")))
    header = Text(f"editar {display_path(path)}\n", style="bold")
    try:
        old_text = path.read_text(encoding="utf-8")
        new_text = apply_edit(
            old_text,
            str(args.get("old_string", "")),
            str(args.get("new_string", "")),
            bool(args.get("replace_all", False)),
        )
    except (OSError, ValueError) as e:
        header.append(f"⚠ La edición no se puede aplicar: {e}", style="yellow")
        return header
    header.append_text(_diff(old_text, new_text, display_path(path)))
    return header


def _preview_write(args: dict[str, Any]) -> Text:
    path = resolve_path(str(args.get("path", "")))
    content = str(args.get("content", ""))
    if path.is_file():
        try:
            old_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            old_text = ""
        header = Text(f"sobrescribir {display_path(path)}\n", style="bold")
        header.append_text(_diff(old_text, content, display_path(path)))
        return header
    header = Text(f"crear {display_path(path)}\n", style="bold")
    header.append_text(_clip_lines([f"+{line}" for line in content.splitlines()]))
    return header


def _diff(old: str, new: str, name: str) -> Text:
    lines = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), fromfile=name, tofile=name, lineterm="", n=2
        )
    )
    return _clip_lines(lines[2:] if len(lines) > 2 else ["(sin cambios)"])


def _clip_lines(lines: list[str]) -> Text:
    out = Text()
    for line in lines[:_MAX_PREVIEW_LINES]:
        if line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = "dim"
        out.append(_truncate(line, 200) + "\n", style=style)
    if len(lines) > _MAX_PREVIEW_LINES:
        out.append(f"… ({len(lines) - _MAX_PREVIEW_LINES} líneas más)\n", style="dim")
    return out


# ---------------------------------------------------------------------------
# Panel + input
# ---------------------------------------------------------------------------


def render_approval_panel(
    tool: ToolDefinition,
    args: dict[str, Any],
    console: Console,
    session_hint: str = "esta acción",
) -> None:
    """Renderizar el panel de aprobación (sin leer input)."""
    options = Text()
    options.append("\n  y ", style="bold")
    options.append("una vez   ")
    options.append("s ", style="bold")
    options.append(f"sesión ({session_hint})   ")
    options.append("a ", style="bold")
    options.append("siempre   ")
    options.append("n ", style="bold")
    options.append("no   ")
    options.append("[texto] ", style="bold")
    options.append("no, y decirle qué hacer")

    console.print(
        Panel(
            Group(build_preview(tool.name, args), options),
            title=f"⚠  {tool.name}",
            title_align="left",
            border_style="yellow",
        )
    )


def ask_approval(
    tool: ToolDefinition,
    args: dict[str, Any],
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
    session_hint: str = "esta acción",
) -> ApprovalResponse:
    """Mostrar prompt de aprobación, leer respuesta del usuario, devolver decisión.

    `input_func` es inyectable para tests (default: `input`).
    """
    if console is None:
        console = Console()
    if input_func is None:
        input_func = input
    render_approval_panel(tool, args, console, session_hint)
    # La pregunta va pegada al cursor: con un diff largo el título del panel
    # queda fuera de pantalla y el "> " solo parece el prompt del chat.
    console.print(
        Text(f"¿{_QUESTIONS.get(tool.name, 'Permitir esta acción')}? ", style="bold yellow")
        + Text("y = sí · s = sí a todo en la sesión · n = no", style="yellow")
    )
    return parse_approval_input(input_func("> "))


_QUESTIONS = {
    "edit_file": "Aplico esta edición",
    "write_file": "Escribo este archivo",
    "bash": "Ejecuto este comando",
}


def _truncate(value: str, max_length: int = 80) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"
