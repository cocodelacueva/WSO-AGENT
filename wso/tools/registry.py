"""Registry central de tools.

Permite descubrir tools por nombre, listarlas todas para el prompt,
y ejecutarlas con args validados.

El registry se construye importando los módulos de tools (que tienen
las declaraciones `@tool`) y escaneándolos en busca de funciones con
el atributo `_tool_def` adjunto por el decorador. No hay estado global
mutable: cada registry es independiente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType

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

    # ---- Generación de prompt ----

    def to_prompt_section(self) -> str:
        """Generar la sección consolidada de tools para el system prompt.

        Concatena las secciones individuales separadas por línea en blanco.
        El orden coincide con el orden de registro.
        """
        if not self._tools:
            return "(ninguna tool disponible)"
        return "\n".join(t.to_prompt_section() for t in self._tools.values())


def load_builtin_tools() -> ToolRegistry:
    """Cargar el set built-in de tools.

    Importa los módulos de tools y registra todas sus funciones decoradas
    con `@tool`. Devuelve un registry listo para usar.

    Las tools de `pptx`, `xlsx`, `pdf` y `docx` quedan registradas sin
    importar si las deps respectivas están instaladas. Cada paquete se
    importa lazy: solo falla si el modelo realmente invoca la tool sin
    tener la extra instalada (con un mensaje guía pidiendo
    `pip install -e ".[office]"`).

    Returns:
        Registry poblado con las 24 tools built-in:
            - filesystem (4): read_file, write_file, delete_file, list_directory
            - flow (2):       responder_al_usuario, preguntar_al_usuario
            - pptx (4):       generate_pptx, read_pptx, edit_pptx_slide,
                              generate_pptx_from_template
            - xlsx (4):       generate_xlsx, read_xlsx, edit_xlsx_cell,
                              append_xlsx_rows
            - pdf (1):        read_pdf
            - docx (1):       read_docx
            - browser (8):    browser_open_tab, browser_navigate,
                              browser_close_tab, browser_read_page,
                              browser_screenshot, browser_click,
                              browser_type, browser_wait_for

    Las tools de `browser` se registran igual que las de Office: sin importar
    Playwright. Solo fallan al invocarse si falta la extra `[browser]` o si
    Chrome no está corriendo con CDP (mensaje guía en ambos casos).
    """
    from wso.tools import browser, docx, filesystem, flow, pdf, pptx, xlsx

    registry = ToolRegistry()
    registry.register_module(filesystem)
    registry.register_module(flow)
    registry.register_module(pptx)
    registry.register_module(xlsx)
    registry.register_module(pdf)
    registry.register_module(docx)
    registry.register_module(browser)
    return registry
