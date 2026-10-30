"""Builder del system prompt.

Corto a propósito: con tool calling nativo las tools no se describen acá
(el provider recibe su JSON Schema aparte), así que el prompt solo lleva
identidad, forma de trabajo y el contexto del proyecto. Cada token que no
gastamos acá es contexto disponible para código en un modelo local.

Estructura:
    1. Identidad y forma de trabajo
    2. Entorno (carpeta del proyecto, sistema, rama de git)
    3. Instrucciones del proyecto (WSO.md / AGENTS.md / CLAUDE.md), si hay
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

# Archivos de instrucciones de proyecto que se cargan si existen (el primero
# que aparezca). AGENTS.md y CLAUDE.md por compatibilidad con otros agentes.
PROJECT_INSTRUCTION_FILES = ("WSO.md", "AGENTS.md", "CLAUDE.md")
MAX_INSTRUCTIONS_CHARS = 6000

_SYSTEM_PROMPT_TEMPLATE = """\
Sos WSO, un agente de programación que trabaja en la terminal del usuario, \
sobre su proyecto. Tenés tools para leer, buscar y editar archivos y para \
correr comandos. Usalas: no le pidas al usuario que te pegue código ni que \
corra comandos que podés correr vos.

# Cómo trabajar
- Antes de cambiar código, entendé el contexto: buscá con grep/glob y leé \
solo lo necesario con read_file (por tramos si es largo).
- Leé un archivo antes de editarlo. Para cambios puntuales usá edit_file con \
un old_string copiado EXACTO del archivo; write_file solo para archivos nuevos \
o reescrituras completas.
- Seguí el estilo, las convenciones y las librerías que el proyecto ya usa.
- Después de cambiar código, verificá: corré los tests o el linter del \
proyecto si existen, y corregí lo que falle.
- Hacé lo que se pidió, ni más ni menos. Si el pedido es ambiguo o hay una \
decisión importante que no podés deducir, preguntá antes de actuar.
- Si una tool devuelve un error, leelo y ajustá; no repitas la misma llamada.
- Los paths pueden ser relativos a la carpeta del proyecto.

# Cómo responder
- Respondé en el idioma del usuario, breve y directo.
- Cuando terminaste (o necesitás algo del usuario), respondé en texto sin \
llamar tools: eso cierra tu turno. Contá qué hiciste y el resultado de la \
verificación; si algo falló o quedó pendiente, decilo.

# Entorno
- Carpeta del proyecto: {cwd}
- Sistema: {system}
{git_line}
{instructions_section}"""


def build_system_prompt(cwd: Path, instructions: str = "", git_branch: str | None = None) -> str:
    """Armar el system prompt.

    Args:
        cwd: Carpeta del proyecto (donde corre `wso`).
        instructions: Contenido del archivo de instrucciones del proyecto.
        git_branch: Rama actual, o None si no es un repo de git.
    """
    git_line = f"- Repo git, rama: {git_branch}" if git_branch else "- No es un repo de git"
    instructions_section = (
        f"\n# Instrucciones del proyecto\n{instructions.strip()}\n"
        if instructions.strip()
        else ""
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        cwd=cwd,
        system=f"{platform.system()} {platform.release()}",
        git_line=git_line,
        instructions_section=instructions_section,
    )


def load_project_instructions(cwd: Path) -> tuple[str, Path | None]:
    """Cargar el primer archivo de instrucciones del proyecto que exista.

    Returns:
        (contenido acotado, path del archivo) o ("", None) si no hay.
    """
    for name in PROJECT_INSTRUCTION_FILES:
        path = cwd / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if len(text) > MAX_INSTRUCTIONS_CHARS:
                text = text[:MAX_INSTRUCTIONS_CHARS] + "\n[… recortado]"
            return text, path
    return "", None


def detect_git_branch(cwd: Path) -> str | None:
    """Rama actual de git, o None si no es repo (o git no está instalado)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = proc.stdout.strip()
    return branch if proc.returncode == 0 and branch else None
