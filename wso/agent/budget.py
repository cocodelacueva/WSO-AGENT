"""Tracking del budget de pasos por turno y prompt de continuación.

El budget es un mecanismo de seguridad, no de control normal. El loop
termina por elicitación explícita (responder_al_usuario o
preguntar_al_usuario); el budget solo dispara si el modelo entra en
loop o no logra terminar la tarea.

Cuando se agota, le mostramos al usuario:
  - el objetivo original del turno
  - lista de tools intentadas con resultado (✓/✗)
  - razonamiento del modelo de por qué seguir
  - opciones: y / N / texto-de-feedback
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class StepRecord:
    """Una ejecución de tool dentro de un turno."""

    tool_name: str
    args_summary: str
    success: bool
    error: str | None = None


@dataclass
class BudgetTracker:
    """Lleva la cuenta de pasos consumidos y registra qué se intentó.

    TODO(v1): implementar lógica completa.
    """

    limit: int = 10
    used: int = 0
    history: list[StepRecord] = field(default_factory=list)
    original_goal: str = ""

    def record(self, step: StepRecord) -> None:
        """Registrar una ejecución de tool."""
        self.history.append(step)
        self.used += 1

    def is_exhausted(self) -> bool:
        return self.used >= self.limit

    def reset(self) -> None:
        """Limpiar para empezar un turno nuevo."""
        self.used = 0
        self.history.clear()
        self.original_goal = ""

    def extend(self, additional: int = 10) -> None:
        """Extender el budget cuando el usuario aprueba continuar."""
        self.limit += additional


ContinuationDecision = Literal["continue", "abort"]


@dataclass
class ContinuationResponse:
    decision: ContinuationDecision
    feedback: str | None = None
    """Texto libre si el usuario eligió abortar con nueva instrucción."""
