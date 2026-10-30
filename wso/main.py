"""Entry point CLI de WSO.

Uso:
    cd mi-proyecto
    wso                  # arranca el REPL sobre la carpeta actual
    python -m wso.main   # equivalente

Este módulo solo orquesta la inicialización de las dependencias y
delega al `AgentLoop`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console

from wso import __version__
from wso.agent.loop import AgentLoop
from wso.agent.model.factory import build_model_client
from wso.agent.prompts import build_system_prompt, detect_git_branch, load_project_instructions
from wso.config import settings
from wso.history_store import HistoryStore
from wso.permissions.manager import PermissionManager
from wso.session_log import SessionLogger
from wso.tools.registry import load_builtin_tools
from wso.ui.console import ConsoleRenderer


def main() -> int:
    """Punto de entrada sincrónico llamado por el script `wso`."""
    console = Console()
    console.print(f"[bold cyan]WSO[/bold cyan] v{__version__} · [dim]{Path.cwd()}[/dim]")
    try:
        return asyncio.run(_run(console))
    except KeyboardInterrupt:
        console.print("\n[dim]Sesión interrumpida.[/]")
        return 0


async def _run(console: Console) -> int:
    renderer = ConsoleRenderer(console=console)
    cwd = Path.cwd()

    try:
        model = build_model_client(settings)
    except ValueError as e:
        renderer.render_error(f"Error de configuración: {e}")
        return 1
    except ImportError as e:
        renderer.render_error(f"Falta dependencia: {e}")
        return 1

    instructions, instructions_path = load_project_instructions(cwd)
    if instructions_path is not None:
        renderer.render_info(f"Instrucciones del proyecto: {instructions_path.name}")
    system_prompt = build_system_prompt(cwd, instructions, detect_git_branch(cwd))

    session_log = SessionLogger(log_dir=settings.logs_dir if settings.log_enabled else None)
    if session_log.enabled:
        renderer.render_info(f"Logging habilitado: {session_log.path}")

    history_store = HistoryStore(
        path=settings.history_file if settings.history_persist else None,
        max_messages=settings.history_max_messages,
    )
    restored = history_store.load()
    if restored:
        renderer.render_info(
            f"Restauré {len(restored)} mensajes de la sesión anterior "
            "(/reset para empezar de cero)."
        )

    loop = AgentLoop(
        model=model,
        tools=load_builtin_tools(),
        permissions=PermissionManager(settings=settings, project_root=cwd),
        renderer=renderer,
        system_prompt=system_prompt,
        session_log=session_log,
        history=restored,
        history_store=history_store,
        step_budget=settings.step_budget,
    )
    await loop.run_repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())
