"""Contrato base de las tools y decorador @tool.

Cada tool es:
    - una función Python con argumentos tipados
    - registrada vía @tool con nombre, descripción y categoría de permiso
    - validada con Pydantic al recibir args desde el modelo
    - capaz de auto-generar su sección del system prompt

Una sola fuente de verdad: el código y el prompt salen de la misma
declaración. Si renombrás un arg de la función, el prompt se actualiza
solo y la validación sigue funcionando.

El decorador NO mantiene estado global. Solo adjunta la `ToolDefinition`
a la función como atributo `_tool_def`. El registry construye su mapa
escaneando módulos en busca de funciones con ese atributo
(ver `wso/tools/registry.py`).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError, create_model


# ---------------------------------------------------------------------------
# Categorías de permiso
# ---------------------------------------------------------------------------


class PermissionCategory(str, Enum):
    """Categorías de permiso para clasificar tools.

    El `PermissionManager` consulta esta categoría para decidir si una
    tool call necesita aprobación del usuario.
    """

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    NETWORK = "network"
    EXECUTE = "execute"
    FLOW = "flow"
    """Tools de control de flujo (responder, preguntar). No piden permiso."""


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------


class ToolValidationError(ValueError):
    """Args inválidos para una tool.

    Se lanza cuando el modelo emite una tool call con args que no
    matchean el schema (faltan, sobran, tipo incorrecto, etc).
    El loop puede capturarla y devolver una observación de error
    al modelo para que reintente.
    """

    def __init__(self, tool_name: str, validation_error: ValidationError) -> None:
        self.tool_name = tool_name
        self.validation_error = validation_error
        details = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in validation_error.errors()
        )
        super().__init__(f"Tool {tool_name!r}: args inválidos — {details}")


# ---------------------------------------------------------------------------
# Definición de tool
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """Definición de una tool registrada.

    Una vez registrada, el agente puede:
        - encontrarla por nombre (vía registry)
        - validar args con `validate_and_call`
        - generar su documentación con `to_prompt_section`
    """

    name: str
    description: str
    category: PermissionCategory
    handler: Callable[..., Any]
    """Función Python que ejecuta la tool. Recibe kwargs ya validados."""

    args_schema: dict[str, str] = field(default_factory=dict)
    """Descripciones humanas de cada arg (para el system prompt)."""

    pydantic_model: type[BaseModel] = field(repr=False, default=BaseModel)
    """Modelo Pydantic auto-derivado de la signature, para validación runtime."""

    def validate_and_call(self, args: dict[str, Any]) -> Any:
        """Validar los args contra el schema y ejecutar el handler.

        Args:
            args: Diccionario crudo de args (típicamente parseado del XML
                emitido por el modelo).

        Returns:
            Lo que devuelva el handler (string en la mayoría de las tools).

        Raises:
            ToolValidationError: si los args no matchean el schema.
        """
        try:
            validated = self.pydantic_model(**args)
        except ValidationError as e:
            raise ToolValidationError(self.name, e) from e
        return self.handler(**validated.model_dump())

    def to_prompt_section(self) -> str:
        """Generar la sección de esta tool para incluir en el system prompt.

        Formato:

            ## nombre_tool
            Descripción de la tool.
            Categoría: read
            Args:
              - arg_name (tipo, requerido): descripción del arg
            Ejemplo:
            <tool name="nombre_tool">
              <arg_name>...</arg_name>
            </tool>
        """
        sig = inspect.signature(self.handler)

        # Líneas de args
        args_lines: list[str] = []
        for arg_name, param in sig.parameters.items():
            type_hint = _format_type(param.annotation)
            if param.default is inspect.Parameter.empty:
                required = "requerido"
            else:
                required = f"opcional, default={param.default!r}"
            desc = self.args_schema.get(arg_name, "").strip()
            line = f"  - {arg_name} ({type_hint}, {required})"
            if desc:
                line += f": {desc}"
            args_lines.append(line)

        # Bloque de ejemplo
        if sig.parameters:
            example_args = "\n".join(
                f"  <{arg_name}>...</{arg_name}>" for arg_name in sig.parameters
            )
            example = f'<tool name="{self.name}">\n{example_args}\n</tool>'
        else:
            example = f'<tool name="{self.name}" />'

        args_block = "\n".join(args_lines) if args_lines else "  (ninguno)"

        return (
            f"## {self.name}\n"
            f"{self.description}\n"
            f"Categoría: {self.category.value}\n"
            f"Args:\n"
            f"{args_block}\n"
            f"Ejemplo:\n"
            f"{example}\n"
        )


# ---------------------------------------------------------------------------
# Decorador @tool
# ---------------------------------------------------------------------------


def tool(
    *,
    name: str,
    category: PermissionCategory,
    description: str,
    args_schema: dict[str, str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador para declarar una tool del agente.

    Adjunta una `ToolDefinition` a la función como atributo `_tool_def`.
    El registry la descubre escaneando módulos. La función sigue siendo
    invocable normalmente (útil para tests).

    Uso:
        @tool(
            name="read_file",
            category=PermissionCategory.READ,
            description="Lee el contenido de un archivo de texto.",
            args_schema={"path": "ruta absoluta del archivo a leer"},
        )
        def read_file(path: str) -> str:
            return Path(path).read_text()

    Args:
        name: Identificador único de la tool (lo que el modelo emite en
            `<tool name="...">`).
        category: Categoría de permiso para gating.
        description: Descripción humana que ve el modelo en el prompt.
        args_schema: Mapeo opcional `arg_name -> descripción`. Si se
            provee, sus claves DEBEN coincidir exactamente con los args
            de la función. Si no se provee, se genera con descripciones
            vacías (la tool funciona, pero el prompt es menos descriptivo).

    Raises:
        TypeError: si la función tiene args sin type hint, o usa
            `*args`/`**kwargs` (las tools requieren args explícitos).
        ValueError: si `args_schema` no coincide con la signature.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(func)

        # 1. Validar la signature: sin *args/**kwargs, todos con type hint
        for arg_name, param in sig.parameters.items():
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                raise TypeError(
                    f"Tool {name!r}: no se permiten *args ni **kwargs "
                    f"(arg {arg_name!r} es {param.kind.description})."
                )
            if param.annotation is inspect.Parameter.empty:
                raise TypeError(
                    f"Tool {name!r}: el arg {arg_name!r} necesita type hint."
                )

        # 2. Reconciliar args_schema con la signature
        sig_args = list(sig.parameters)
        if args_schema is None:
            resolved_schema = {arg: "" for arg in sig_args}
        else:
            schema_keys = set(args_schema)
            sig_keys = set(sig_args)
            if schema_keys != sig_keys:
                missing = sig_keys - schema_keys
                extra = schema_keys - sig_keys
                msg_parts = [f"Tool {name!r}: args_schema no coincide con la signature."]
                if missing:
                    msg_parts.append(f"Faltan: {sorted(missing)}.")
                if extra:
                    msg_parts.append(f"Sobran: {sorted(extra)}.")
                raise ValueError(" ".join(msg_parts))
            resolved_schema = dict(args_schema)

        # 3. Construir el Pydantic model dinámicamente
        fields: dict[str, tuple[Any, Any]] = {}
        for arg_name, param in sig.parameters.items():
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[arg_name] = (param.annotation, default)
        pydantic_model = create_model(f"{_to_camel(name)}Args", **fields)

        # 4. Crear y adjuntar la definición
        definition = ToolDefinition(
            name=name,
            description=description,
            category=category,
            handler=func,
            args_schema=resolved_schema,
            pydantic_model=pydantic_model,
        )
        func._tool_def = definition  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _format_type(annotation: Any) -> str:
    """Render legible de un type annotation para el prompt."""
    if annotation is inspect.Parameter.empty:
        return "any"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _to_camel(name: str) -> str:
    """Convertir snake_case a CamelCase para nombres de Pydantic models."""
    return "".join(part.capitalize() for part in name.split("_"))


def get_tool_definition(func: Callable[..., Any]) -> ToolDefinition | None:
    """Obtener la `ToolDefinition` adjunta a una función decorada.

    Devuelve None si la función no fue decorada con @tool.
    Útil para introspección desde el registry.
    """
    return getattr(func, "_tool_def", None)
