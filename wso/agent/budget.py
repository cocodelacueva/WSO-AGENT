"""Tracking del budget de pasos por turno y prompt de continuación.

El budget es un mecanismo de seguridad, no de control normal. El loop
termina por elicitación explícita (responder_al_usuario o
preguntar_al_usuario); el budget solo dispara si el modelo entra en
loop o no logra terminar la tarea en N pasos.

Cuando se agota:
    - mostramos un panel con objetivo, history (✓/✗) y razonamiento
    - el usuario puede:
        * 'y'        → continuar 10 intentos más
        * 'n' o ''   → abortar
        * cualquier  → abortar con feedback al modelo
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass
class StepRecord:
    """Una ejecución de tool dentro de un turno."""

    tool_name: str
    args_summary: str
    """Versión truncada de los args, lista para mostrar al usuario."""
    success: bool
    error: str | None = None


ContinuationDecision = Literal["continue", "abort"]


@dataclass(frozen=True)
class ContinuationResponse:
    decision: ContinuationDecision
    feedback: str | None = None
    """Texto libre si el usuario abortó con instrucción nueva."""


# ---------------------------------------------------------------------------
# BudgetTracker
# ---------------------------------------------------------------------------


@dataclass
class BudgetTracker:
    """Lleva la cuenta de pasos consumidos en el turno actual.

    El loop crea uno al iniciar cada turno (vía `start_turn`) y lo
    consulta tras cada tool execution.
    """

    limit: int = 10
    used: int = 0
    history: list[StepRecord] = field(default_factory=list)
    original_goal: str = ""

    def start_turn(self, goal: str, limit: int = 10) -> None:
        """Resetear y arrancar un nuevo turno con el objetivo dado."""
        self.limit = limit
        self.used = 0
        self.history.clear()
        self.original_goal = goal

    def record(self, step: StepRecord) -> None:
        """Registrar una ejecución de tool (incrementa el contador)."""
        self.history.append(step)
        self.used += 1

    def is_exhausted(self) -> bool:
        return self.used >= self.limit

    def reset(self) -> None:
        """Limpiar todo el estado del turno actual."""
        self.used = 0
        self.history.clear()
        self.original_goal = ""

    def extend(self, additional: int = 10) -> None:
        """Extender el budget cuando el usuario aprueba continuar."""
        self.limit += additional


# ---------------------------------------------------------------------------
# Prompt de continuación
# ---------------------------------------------------------------------------


def parse_continuation_input(raw: str) -> ContinuationResponse:
    """Parsear respuesta del usuario en el prompt de continuación.

    - 'y' / 'Y' (con o sin whitespace)  → continue
    - empty / 'n' / 'N'                  → abort sin feedback
    - cualquier otra cosa               → abort con feedback

    Esta función está separada para ser testeable sin I/O.
    """
    stripped = raw.strip()
    if not stripped:
        return ContinuationResponse(decision="abort")

    code = stripped.lower()
    if code == "y":
        return ContinuationResponse(decision="continue")
    if code == "n":
        return ContinuationResponse(decision="abort")

    return ContinuationResponse(decision="abort", feedback=stripped)


def render_continuation_panel(
    tracker: BudgetTracker,
    console: Console,
    current_thinking: str = "",
    additional: int = 10,
) -> None:
    """Renderizar el panel de continuación (sin leer input)."""
    body = Text()
    body.append(
        f"Budget agotado: {tracker.used}/{tracker.limit} acciones\n\n",
        style="yellow bold",
    )

    body.append("Objetivo original: ", style="dim")
    goal_display = tracker.original_goal if tracker.original_goal else "(sin objetivo)"
    body.append(f"{goal_display}\n\n", style="white")

    body.append("Lo que se intentó:\n", style="bold")
    if not tracker.history:
        body.append("  (sin acciones registradas)\n", style="dim italic")
    else:
        for step in tracker.history:
            marker = "✓" if step.success else "✗"
            color = "green" if step.success else "red"
            body.append(f"  {marker} ", style=color)
            body.append(f"{step.tool_name}({step.args_summary})\n", style="white")
            if step.error:
                body.append(f"      → {step.error}\n", style="red dim")

    if current_thinking:
        body.append("\nRazonamiento actual del agente:\n", style="bold")
        body.append(f"  {current_thinking}\n", style="dim italic")

    body.append(f"\n¿Continuar {additional} intentos más?\n\n", style="bold")
    body.append("  y       — continuar\n")
    body.append("  n / Enter — abortar\n")
    body.append("  [texto] — abortar y mandar feedback al agente\n")

    console.print(Panel(body, title="⚠  Budget agotado", border_style="yellow"))


def ask_continuation(
    tracker: BudgetTracker,
    console: Console | None = None,
    input_func: Callable[[str], str] | None = None,
    current_thinking: str = "",
    additional: int = 10,
) -> ContinuationResponse:
    """Mostrar el prompt de continuación, leer respuesta, devolver decisión.

    Args:
        tracker: Tracker con el history del turno.
        console: Console de Rich. Default: nuevo Console().
        input_func: Función para leer input. Default: input() builtin.
        current_thinking: Razonamiento actual del modelo (lo último
            que emitió antes de agotarse el budget). Opcional.
        additional: Cantidad de pasos extra que se ofrecen.
    """
    if console is None:
        console = Console()
    if input_func is None:
        input_func = input

    render_continuation_panel(tracker, console, current_thinking, additional)
    raw = input_func("> ")
    return parse_continuation_input(raw)
