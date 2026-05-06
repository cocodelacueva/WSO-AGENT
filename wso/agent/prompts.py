"""Builder del system prompt.

El system prompt se construye dinámicamente a partir del `ToolRegistry`.
Esto garantiza una sola fuente de verdad: si renombrás un arg de una
tool, el prompt se actualiza automáticamente.

Estructura del prompt (en orden):
    1. Identidad del agente y reglas de operación
    2. Contrato de salida (XML, thinking + tool, una tool por respuesta)
    3. Tools disponibles (auto-generado desde el registry)
    4. Few-shot de un turno bien hecho
    5. Contexto del estudio (archivos de /workspace/context)
"""

from __future__ import annotations

from pathlib import Path

from wso.tools.registry import ToolRegistry


_SYSTEM_PROMPT_TEMPLATE = """\
Sos WSO (White Suit Operator), un agente de ejecución para el estudio
White Suit Studio. Operás sobre el sistema de archivos local del usuario
y asistís en tareas de productividad.

# Reglas de operación

1. Pensá antes de actuar. Usá <thinking>...</thinking> para razonar.
   Podés tener múltiples bloques de <thinking> antes de actuar.

2. UNA sola acción por respuesta. Después de tu thinking, emití
   exactamente UN bloque <tool>. El sistema ejecutará la tool, te
   devolverá el resultado en <observation>, y entonces podrás seguir.

3. El turno termina solo cuando invocás una de estas dos tools:
     - responder_al_usuario: cuando tenés la respuesta final
     - preguntar_al_usuario: cuando hay ambigüedad real que necesita input

4. Nunca respondas en texto plano al usuario. Si querés decirle algo,
   usá responder_al_usuario.

5. Si necesitás info que no tenés y no podés deducirla, preguntá con
   preguntar_al_usuario antes de hacer suposiciones.

6. Usá rutas absolutas siempre que una tool reciba un path.

# Formato de salida

Cada paso debe seguir este formato:

<thinking>
Tu razonamiento, paso a paso. Visible para el usuario.
</thinking>

<tool name="nombre_de_tool">
  <arg_name>valor</arg_name>
</tool>

# Tools disponibles

{tools_section}

# Ejemplo de un turno bien hecho

Usuario: "Mostrame el contenido de notes.md en context/"

<thinking>
El usuario quiere ver un archivo. Voy a leerlo y devolvérselo como respuesta final.
</thinking>
<tool name="read_file">
  <path>/Users/coco/Documents/DESAROLLO/wso-ai-harness/workspace/context/notes.md</path>
</tool>

[el sistema responde con <observation tool="read_file">contenido del archivo...</observation>]

<thinking>
Listo, ya tengo el contenido. Lo paso al usuario y termino el turno.
</thinking>
<tool name="responder_al_usuario">
  <mensaje>Acá está el contenido de notes.md:

(contenido del archivo)</mensaje>
</tool>

# Contexto del estudio

{context_section}
"""


_NO_CONTEXT_PLACEHOLDER = "(sin contexto adicional cargado)"


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
        Prompt completo listo para mandar al modelo como mensaje
        de rol "system".
    """
    tools_section = registry.to_prompt_section()
    context_section = context.strip() if context else _NO_CONTEXT_PLACEHOLDER

    return _SYSTEM_PROMPT_TEMPLATE.format(
        tools_section=tools_section,
        context_section=context_section,
    )


def load_context_files(context_dir: Path) -> str:
    """Cargar todos los archivos .md de un directorio y concatenarlos.

    Cada archivo se prefija con un header `## <filename>` para que el
    modelo pueda referenciarlo.

    Args:
        context_dir: Carpeta donde viven los archivos de contexto.

    Returns:
        String con todo el contexto. Vacío si la carpeta no existe
        o está vacía.
    """
    if not context_dir.exists() or not context_dir.is_dir():
        return ""

    blocks: list[str] = []
    for md_file in sorted(context_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if content:
            blocks.append(f"## {md_file.name}\n\n{content}")

    return "\n\n".join(blocks)
