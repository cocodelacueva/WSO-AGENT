"""Registry central de tools.

Permite descubrir tools por nombre y listarlas como `ToolSpec` para el
tool calling nativo del provider.

El registry se construye importando los módulos de tools (que tienen
las declaraciones `@tool`) y escaneándolos en busca de funciones con
el atributo `_tool_def` adjunto por el decorador. No hay estado global
mutable: cada registry es independiente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType

from wso.agent.model.base import ToolSpec
from wso.tools.base import ToolDefinition, get_tool_definition


@dataclass
class ToolRegistry:
    """Mapa de nombre -> ToolDefinition.

    Construir típicamente vía `load_builtin_tools()`, o manualmente
    con `register_module()` para casos custom (tests, plugins, etc).
    """

    _tools: dict[str, ToolDefinition] = field(default_factory=dict)

    # ---- Acceso ----

    def get(self, name: str) -> ToolDefinition | None:
        """Obtener una tool por nombre, o None si no existe."""
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        """Listar todas las tools registradas (orden de inserción)."""
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ---- Registro ----

    def register(self, tool: ToolDefinition) -> None:
        """Registrar una tool individual.

        Raises:
            ValueError: si ya hay una tool registrada con ese nombre.
        """
        if tool.name in self._tools:
            raise ValueError(
                f"Tool ya registrada: {tool.name!r}. "
                f"Cada tool debe tener un nombre único en el registry."
            )
        self._tools[tool.name] = tool

    def register_module(self, module: ModuleType) -> int:
        """Escanear un módulo y registrar todas sus tools decoradas.

        Busca atributos del módulo que sean callables con `_tool_def`
        adjunto (es decir, funciones decoradas con `@tool`).

        Args:
            module: módulo Python ya importado.

        Returns:
            Cantidad de tools registradas desde el módulo.
        """
        count = 0
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            attr = getattr(module, attr_name)
            if not callable(attr):
                continue
            defn = get_tool_definition(attr)
            if defn is None:
                continue
            self.register(defn)
            count += 1
        return count

    # ---- Specs para el provider ----

    def specs(self) -> list[ToolSpec]:
        """JSON Schema de todas las tools, en orden de registro (estable: cacheable)."""
        return [t.to_spec() for t in self._tools.values()]


def load_builtin_tools() -> ToolRegistry:
    """Cargar el set built-in de tools de programación.

    Returns:
        Registry con 6 tools:
            - filesystem: read_file, write_file, edit_file
            - search:     glob, grep
            - shell:      bash
    """
    from wso.tools import filesystem, search, shell

    registry = ToolRegistry()
    registry.register_module(filesystem)
    registry.register_module(search)
    registry.register_module(shell)
    return registry
