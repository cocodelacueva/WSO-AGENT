"""Registry central de tools.

Permite descubrir tools por nombre, listarlas todas para el prompt,
y ejecutarlas con args validados.

El registry se construye cargando los módulos de tools (que tienen
las declaraciones @tool). Para forzar el descubrimiento, importar
los módulos en `load_builtin_tools()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wso.tools.base import ToolDefinition


@dataclass
class ToolRegistry:
    """Mapa de nombre -> ToolDefinition."""

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)

    def register(self, tool: ToolDefinition) -> None:
        """Registrar una tool. Llamado por el decorador @tool."""
        if tool.name in self._tools:
            raise ValueError(f"Tool ya registrada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def load_builtin_tools() -> ToolRegistry:
    """Cargar el set built-in de tools de v1.

    Importa los módulos para forzar la ejecución de los decoradores
    @tool y devuelve el registry poblado.

    TODO(v1): importar wso.tools.filesystem y wso.tools.flow para que
    sus decoradores se ejecuten.
    """
    raise NotImplementedError("Loader de tools built-in pendiente de implementar.")
