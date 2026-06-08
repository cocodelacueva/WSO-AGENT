"""Entry point CLI de WSO.

Uso:
    wso                  # arranca el REPL conversacional
    python -m wso.main   # equivalente

Este módulo solo orquesta la inicialización de las dependencias y
delega al `AgentLoop`. Cualquier lógica de agente, parsing o tools
vive en otros módulos.
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console

from wso.agent.loop import AgentLoop
from wso.agent.model.factory import build_model_client
from wso.agent.prompts import build_system_prompt, load_context_files
from wso.config import settings
from wso.permissions.manager import PermissionManager
from wso.session_log import SessionLogger
from wso.tools.registry import load_builtin_tools
from wso.ui.console import ConsoleRenderer


def main() -> int:
    """Punto de entrada sincrónico llamado por el script `wso`."""
    console = Console()
    console.print("[bold cyan]White Suit Operator[/bold cyan] v0.1.0")
    console.print(f"Modo: [yellow]{settings.mode}[/yellow]")

    if settings.mode == "local":
        console.print(f"Modelo local: {settings.local_model} @ {settings.local_url}")
    else:
        console.print(
            f"Provider cloud: {settings.cloud_provider} "
            f"({settings.cloud_model})"
        )

    console.print()

    try:
        return asyncio.run(_run(console))
    except KeyboardInterrupt:
        console.print("\n[dim]Sesión interrumpida.[/]")
        return 0


async def _run(console: Console) -> int:
    """Loop principal asincrónico: arma todas las dependencias y arranca el REPL."""
    renderer = ConsoleRenderer(console=console)

    # Asegurar que las carpetas de workspace existan (primer run)
    _ensure_workspace_dirs()

    # Construir cliente de modelo
    try:
        model = build_model_client(settings)
    except (ValueError, NotImplementedError) as e:
        renderer.render_error(f"Error de configuración: {e}")
        return 1
    except ImportError as e:
        renderer.render_error(f"Falta dependencia: {e}")
        return 1

    # Cargar tools, contexto, permisos
    tools = load_builtin_tools()
    permissions = PermissionManager(settings=settings)
    context = load_context_files(settings.context_dir)
    system_prompt = build_system_prompt(tools, context=context)

    if context:
        renderer.render_info(
            f"Contexto cargado desde {settings.context_dir} "
            f"({len(context)} caracteres)."
        )

    # Logger estructurado (opt-in via WSO_LOG_ENABLED). Si está deshabilitado,
    # el logger es no-op y no toca el disco.
    session_log = SessionLogger(
        log_dir=settings.logs_dir if settings.log_enabled else None
    )
    if session_log.enabled:
        renderer.render_info(f"Logging habilitado: {session_log.path}")

    # Construir y arrancar el loop
    loop = AgentLoop(
        model=model,
        tools=tools,
        permissions=permissions,
        renderer=renderer,
        system_prompt=system_prompt,
        session_log=session_log,
    )

    await loop.run_repl()
    return 0


def _ensure_workspace_dirs() -> None:
    """Crear las carpetas de workspace si no existen (primer run)."""
    for directory in [
        settings.context_dir,
        settings.automations_dir,
        settings.output_dir,
        settings.config_dir,
        settings.logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    sys.exit(main())
