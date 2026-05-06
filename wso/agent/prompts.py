"""Builder del system prompt.

El system prompt se construye dinámicamente a partir del registry de
tools. Esto garantiza una sola fuente de verdad: si renombrás un arg
de una tool, el prompt se actualiza automáticamente.

Estructura del prompt:
    1. Identidad del agente y reglas de operación
    2. Contrato de salida (XML, thinking + tool, una tool por respuesta)
    3. Catálogo de tools (auto-generado desde el registry)
    4. Few-shot examples del formato esperado
    5. Información de contexto del estudio (cargada desde /context)
"""

from __future__ import annotations

from wso.tools.registry import ToolRegistry


SYSTEM_PROMPT_TEMPLATE = """\
Sos WSO (White Suit Operator), un agente de ejecución para el estudio
White Suit. Operás sobre el sistema de archivos local del usuario y
asistís en tareas de productividad.

## Reglas de operación

1. Pensá antes de actuar. Usá <thinking>...</thinking> para razonar.
   Podés tener múltiples bloques de thinking antes de actuar.

2. Una sola acción por respuesta. Después de tu thinking, emití
   exactamente UN bloque <tool>. El sistema ejecutará la tool, te
   devolverá el resultado en <observation>, y entonces podrás seguir.

3. El turno termina solo cuando invocás una de estas dos tools
   especiales:
     - responder_al_usuario: cuando tenés la respuesta final
     - preguntar_al_usuario: cuando hay ambigüedad real que necesita input

4. Nunca respondas en texto plano al usuario. Si querés decirle algo,
   usá responder_al_usuario.

5. Si necesitás info que no tenés y no podés deducirla, preguntá con
   preguntar_al_usuario antes de hacer suposiciones.

## Formato de salida

<thinking>
Tu razonamiento, paso a paso. Visible para el usuario.
</thinking>

<tool name="nombre_de_tool">
  <arg_name>valor</arg_name>
</tool>

## Tools disponibles

{tools_section}

## Contexto del estudio

{context_section}
"""


def build_system_prompt(
    registry: ToolRegistry,
    context: str = "",
) -> str:
    """Armar el system prompt con tools auto-documentadas.

    Args:
        registry: Registry de tools disponibles. Se itera para
            generar la sección de tools.
        context: Texto opcional con info cargada desde /context.

    Returns:
        Prompt completo listo para mandar al modelo.

    TODO(v1): implementar la generación de tools_section.
    """
    raise NotImplementedError("Builder de prompt pendiente de implementar.")
