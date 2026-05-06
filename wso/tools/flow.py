"""Tools de control de flujo del loop.

Estas dos tools son especiales: son las únicas que TERMINAN un turno.
Cualquier otra tool deja el loop activo esperando el siguiente paso.

Categoría FLOW: no piden permiso (son intrínsecamente seguras — solo
comunican con el usuario).

Notas de implementación:
    - Los handlers no hacen I/O directo. Solo estructuran la data.
      El loop detecta el nombre de la tool y orquesta el rendering
      y la captura de respuesta.
    - `responder_al_usuario` devuelve el mensaje sin modificar.
    - `preguntar_al_usuario` devuelve un JSON con la pregunta parseada
      (opciones split por '|', bool ya convertido por Pydantic).
"""

from __future__ import annotations

import json

from wso.tools.base import PermissionCategory, tool


@tool(
    name="responder_al_usuario",
    category=PermissionCategory.FLOW,
    description=(
        "Entrega la respuesta final al usuario y termina el turno. "
        "Usá esto cuando hayas completado la tarea solicitada o tengas "
        "la información que el usuario pidió."
    ),
    args_schema={
        "mensaje": "texto de la respuesta final para el usuario",
    },
)
def responder_al_usuario(mensaje: str) -> str:
    """Marcar el turno como terminado con la respuesta dada.

    El loop detecta esta tool y renderiza el mensaje como respuesta final,
    cerrando el turno y devolviendo el control al usuario.
    """
    return mensaje


@tool(
    name="preguntar_al_usuario",
    category=PermissionCategory.FLOW,
    description=(
        "Pide clarificación al usuario antes de continuar. Usalo cuando "
        "haya ambigüedad real que no podés resolver con suposiciones razonables. "
        "El turno se pausa esperando la respuesta del usuario, que llegará "
        "como el siguiente input."
    ),
    args_schema={
        "pregunta": "la pregunta concreta a hacerle al usuario",
        "opciones": (
            "lista de opciones sugeridas separadas por '|'. "
            "Vacío si no hay opciones predefinidas. "
            "Ej: 'Formal corporativo|Casual visual|Técnico detallado'"
        ),
        "permite_respuesta_libre": (
            "true si el usuario puede responder con texto libre además de las opciones, "
            "false si debe elegir una de las opciones"
        ),
    },
)
def preguntar_al_usuario(
    pregunta: str,
    opciones: str = "",
    permite_respuesta_libre: bool = True,
) -> str:
    """Estructurar una pregunta para el usuario.

    Devuelve JSON con la pregunta, lista de opciones parseada, y el flag
    de respuesta libre. El loop captura este JSON, renderiza el prompt
    y espera input del usuario.
    """
    options_list = [opt.strip() for opt in opciones.split("|") if opt.strip()]
    return json.dumps(
        {
            "pregunta": pregunta,
            "opciones": options_list,
            "permite_respuesta_libre": permite_respuesta_libre,
        },
        ensure_ascii=False,
    )
