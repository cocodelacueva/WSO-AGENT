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


class PermissionCategory(str, Enum):  # noqa: UP042 — StrEnum es 3.11+; mantener este shape por ahora
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

    aliases: dict[str, str] = field(default_factory=dict)
    """Mapeo `alias -> arg canónico`. Tolera que el modelo use otro nombre.

    Ej: `{"slides": "slides_json"}` hace que una tool call con `slides`
    se interprete como `slides_json`. Solo se aplica si el arg canónico
    no vino ya provisto (lo explícito gana sobre el alias).
    """

    pydantic_model: type[BaseModel] = field(repr=False, default=BaseModel)
    """Modelo Pydantic auto-derivado de la signature, para validación runtime."""

    def _apply_aliases(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remapear claves alias a su arg canónico (sin pisar lo explícito)."""
        if not self.aliases:
            return args
        remapped = dict(args)
        for alias, canonical in self.aliases.items():
            if alias in remapped and canonical not in remapped:
                remapped[canonical] = remapped.pop(alias)
        return remapped

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
        args = self._apply_aliases(args)
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
    aliases: dict[str, str] | None = None,
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
        aliases: Mapeo opcional `alias -> arg canónico`. Tolera que el
            modelo emita un nombre de arg alternativo (ej: `slides` por
            `slides_json`). Cada valor (arg canónico) DEBE existir en la
            signature; cada clave (alias) NO debe colisionar con un arg real.

    Raises:
        TypeError: si la función tiene args sin type hint, o usa
            `*args`/`**kwargs` (las tools requieren args explícitos).
        ValueError: si `args_schema` o `aliases` no coinciden con la signature.
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

        # 2b. Validar aliases contra la signature
        resolved_aliases = dict(aliases) if aliases else {}
        if resolved_aliases:
            sig_keys = set(sig_args)
            bad_targets = {
                a: c for a, c in resolved_aliases.items() if c not in sig_keys
            }
            if bad_targets:
                raise ValueError(
                    f"Tool {name!r}: aliases apuntan a args inexistentes: "
                    f"{bad_targets}. Args válidos: {sorted(sig_keys)}."
                )
            clashing = sorted(set(resolved_aliases) & sig_keys)
            if clashing:
                raise ValueError(
                    f"Tool {name!r}: estos aliases colisionan con args reales: "
                    f"{clashing}. Un alias no puede llamarse igual que un arg."
                )

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
            aliases=resolved_aliases,
            pydantic_model=pydantic_model,
        )
        func._tool_def = definition  # type: ignore[attr-defined]
        return func

    return decorator


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def truncate_with_notice(
    text: str,
    max_chars: int | None,
    *,
    what: str = "contenido",
    more_hint: str = "",
) -> str:
    """Truncar `text` a `max_chars` agregando un aviso claro al final.

    Las tools de lectura (read_pdf, read_docx, read_pptx) pueden devolver
    documentos enormes. En modelos locales con `num_ctx` chico, un solo read
    gigante desaloja del contexto todo lo anterior (el system prompt, lo que
    el usuario pidió, lecturas previas), y el modelo "olvida" la tarea. Este
    cap acota cada lectura a un tamaño predecible y le avisa explícitamente
    al modelo que el contenido sigue, para que no lo trate como completo.

    Args:
        text: Texto completo a (posiblemente) truncar.
        max_chars: Tope de caracteres. Si es None o <= 0, no trunca.
        what: Qué se está truncando (para el mensaje, ej "el PDF").
        more_hint: Sugerencia extra sobre cómo obtener el resto.

    Returns:
        El texto original si entra; o el head truncado + un aviso visible.
    """
    if not max_chars or max_chars <= 0 or len(text) <= max_chars:
        return text
    head = text[:max_chars].rstrip()
    omitted = len(text) - len(head)
    notice = (
        f"\n\n[…TRUNCADO: se omitieron ~{omitted} caracteres de {what}. "
        f"Mostrados {len(head)} de {len(text)}."
    )
    if more_hint:
        notice += " " + more_hint
    notice += " Trabajá con lo mostrado o volvé a leer acotando el rango.]"
    return head + notice


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
