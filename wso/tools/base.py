"""Contrato base de las tools y decorador @tool.

Cada tool es:
    - una función Python con argumentos tipados
    - registrada vía @tool con nombre, descripción y categoría de permiso
    - validada con Pydantic al recibir args desde el modelo
    - capaz de auto-generar su sección del system prompt

Una sola fuente de verdad: el código y el prompt salen de la misma
declaración.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PermissionCategory(str, Enum):
    """Categorías de permiso para clasificar tools."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    NETWORK = "network"
    EXECUTE = "execute"
    FLOW = "flow"
    """Tools de control de flujo (responder, preguntar). No piden permiso."""


@dataclass
class ToolDefinition:
    """Definición de una tool registrada.

    Una vez registrada, el agente puede:
        - encontrarla por nombre
        - validar args contra su schema
        - ejecutarla con args validados
        - generar su documentación para el system prompt
    """

    name: str
    description: str
    category: PermissionCategory
    handler: Callable[..., Any]
    """Función Python que ejecuta la tool. Recibe kwargs ya validados."""

    args_schema: dict[str, str]
    """Mapeo arg_name -> descripción humana. Para el prompt y validación.
    TODO(v1): reemplazar por un Pydantic model auto-derivado de la
    signature del handler.
    """


def tool(
    *,
    name: str,
    category: PermissionCategory,
    description: str,
    args_schema: dict[str, str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador para registrar una tool en el registry global.

    Uso:
        @tool(
            name="read_file",
            category=PermissionCategory.READ,
            description="Lee un archivo de texto.",
            args_schema={"path": "ruta absoluta del archivo"},
        )
        def read_file(path: str) -> str:
            ...

    TODO(v1):
        - inferir args_schema de la signature si no se provee
        - crear Pydantic model en runtime para validación
        - registrar automáticamente en el registry singleton
    """
    raise NotImplementedError("Decorador @tool pendiente de implementar.")
