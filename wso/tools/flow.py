"""Tools de control de flujo del loop.

Estas dos tools son especiales: son las únicas que TERMINAN un turno.
Cualquier otra tool deja el loop activo esperando el siguiente paso.

Categoría FLOW: no piden permiso (son intrínsecamente seguras —
solo comunican con el usuario).
"""

from __future__ import annotations

# from wso.tools.base import PermissionCategory, tool


# TODO(v1): implementar como tools.
# Esquema previsto:
#
# @tool(
#     name="responder_al_usuario",
#     category=PermissionCategory.FLOW,
#     description="Entregar la respuesta final al usuario y cerrar el turno.",
#     args_schema={"mensaje": "texto de la respuesta final"},
# )
# def responder_al_usuario(mensaje: str) -> str:
#     # marca el turno como terminado, retorna el mensaje
#     ...
#
# @tool(
#     name="preguntar_al_usuario",
#     category=PermissionCategory.FLOW,
#     description="Pedir clarificación al usuario antes de continuar.",
#     args_schema={
#         "pregunta": "la pregunta a hacer",
#         "opciones": "lista de opciones sugeridas (separadas por |) o vacío",
#         "permite_respuesta_libre": "true o false",
#     },
# )
# def preguntar_al_usuario(...) -> str:
#     ...
