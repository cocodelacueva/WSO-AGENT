"""Manejo del contexto: mantener el historial dentro de la ventana del modelo.

Con Ollama, si el prompt supera `num_ctx` el servidor recorta en silencio y
el modelo pierde el system prompt o el pedido del usuario. Por eso, antes
de cada llamada, el loop le pide al `ContextManager` que ajuste el
historial a la ventana, de menor a mayor agresividad:

    1. Resumir resultados de tools viejos: el contenido de un archivo leído
       hace 10 pasos se reemplaza por una línea ("[omitido: …]"). Los
       últimos `keep_recent` resultados quedan intactos.
    2. Descartar turnos completos viejos (desde el principio), nunca el
       turno actual.
    3. Si el turno actual solo ya no entra, resumir también sus resultados
       de tools salvo el último.

Solo actúa cuando el historial se acerca al límite. Con modelos cloud de
ventana grande prácticamente nunca dispara, así el historial queda
append-only (lo que el prompt caching y el thinking de Claude necesitan).

La cuenta de tokens es una estimación por caracteres. Arranca conservadora
(3 caracteres/token) y se calibra con los tokens reales que reporta el
provider en cada respuesta (`observe`), así no recorta de más.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from wso.agent.model.base import Message, ToolSpec

# Código y JSON tokenizan peor que prosa: ~3 caracteres por token es
# conservador para la mayoría de los tokenizers. Es el valor inicial; después
# se calibra con el uso real.
CHARS_PER_TOKEN = 3.0
_MIN_CHARS_PER_TOKEN = 2.5
_MAX_CHARS_PER_TOKEN = 5.0

# Por debajo de esta fracción de la ventana útil no tocamos nada.
_TRIGGER = 0.85

_ELIDE_MIN_CHARS = 300


@dataclass
class FitReport:
    """Qué hizo `fit` (para mostrarle al usuario y loguear)."""

    tokens_before: int
    tokens_after: int
    elided: int = 0
    dropped: int = 0

    @property
    def changed(self) -> bool:
        return self.elided > 0 or self.dropped > 0


@dataclass
class ContextManager:
    window: int
    """Tokens de contexto del modelo."""
    reserve_output: int
    """Tokens reservados para la respuesta del modelo."""
    keep_recent: int = 4
    """Resultados de tools recientes que nunca se resumen en el paso 1."""
    chars_per_token: float = CHARS_PER_TOKEN
    """Se ajusta con `observe` según los tokens reales del provider."""

    @classmethod
    def for_window(cls, window: int) -> ContextManager:
        # Ventanas chicas (locales): reservar 1/4, con tope de 4k.
        return cls(window=window, reserve_output=min(4096, window // 4))

    @property
    def budget(self) -> int:
        """Tokens disponibles para el prompt (system + tools + historial)."""
        return self.window - self.reserve_output

    def count_chars(self, system: str, tools: list[ToolSpec], history: list[Message]) -> int:
        chars = len(system) + sum(
            len(t.name) + len(t.description) + len(json.dumps(t.parameters)) for t in tools
        )
        return chars + sum(_message_chars(m) for m in history)

    def estimate(self, system: str, tools: list[ToolSpec], history: list[Message]) -> int:
        return int(self.count_chars(system, tools, history) / self.chars_per_token)

    def observe(self, chars_sent: int, actual_tokens: int) -> None:
        """Calibrar la estimación con los tokens de entrada que reportó el provider.

        Se ignoran mediciones implausibles (ej: Ollama a veces cuenta solo los
        tokens que no estaban en su cache) y el ratio se suaviza.
        """
        if actual_tokens <= 0 or chars_sent <= 0:
            return
        measured = chars_sent / actual_tokens
        if not _MIN_CHARS_PER_TOKEN <= measured <= _MAX_CHARS_PER_TOKEN * 1.5:
            return
        measured = min(measured, _MAX_CHARS_PER_TOKEN)
        self.chars_per_token = (self.chars_per_token + measured) / 2

    def fit(self, system: str, tools: list[ToolSpec], history: list[Message]) -> FitReport:
        """Ajustar `history` IN PLACE para que entre en la ventana."""
        before = self.estimate(system, tools, history)
        report = FitReport(tokens_before=before, tokens_after=before)
        if before <= self.budget * _TRIGGER:
            return report

        def over() -> bool:
            return self.estimate(system, tools, history) > self.budget * _TRIGGER

        # 1. Resumir resultados viejos.
        tool_idx = [i for i, m in enumerate(history) if m.role == "tool"]
        for i in tool_idx[: max(len(tool_idx) - self.keep_recent, 0)]:
            if not over():
                break
            report.elided += _elide(history, i)

        # 2. Descartar turnos completos viejos (nunca el actual).
        while over():
            turn_starts = [i for i, m in enumerate(history) if m.role == "user"]
            if len(turn_starts) < 2:
                break
            cut = turn_starts[1]
            del history[:cut]
            report.dropped += cut

        # 3. El turno actual solo no entra: resumir todo salvo el último resultado.
        if over():
            tool_idx = [i for i, m in enumerate(history) if m.role == "tool"]
            for i in tool_idx[:-1]:
                if not over():
                    break
                report.elided += _elide(history, i)

        report.tokens_after = self.estimate(system, tools, history)
        return report


def _message_chars(m: Message) -> int:
    chars = len(m.content) + 10  # overhead de rol/formato
    for c in m.tool_calls:
        chars += len(c.name) + len(json.dumps(c.args, ensure_ascii=False))
    return chars


def _elide(history: list[Message], i: int) -> int:
    """Reemplazar el contenido de un resultado de tool por un resumen de una línea."""
    m = history[i]
    if len(m.content) < _ELIDE_MIN_CHARS or m.content.startswith("[omitido"):
        return 0
    first = m.content.strip().splitlines()[0][:120] if m.content.strip() else ""
    summary = (
        f"[omitido para ahorrar contexto: resultado de {m.name} de "
        f"{len(m.content)} caracteres. Empezaba con: {first!r}. "
        "Si lo necesitás de nuevo, volvé a ejecutar la tool.]"
    )
    history[i] = replace(m, content=summary)
    return 1
