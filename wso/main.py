"""Entry point CLI de WSO.

Uso:
    wso                  # arranca el REPL conversacional
    python -m wso.main   # equivalente

Este módulo es deliberadamente delgado: solo orquesta la inicialización
y delega al `agent.loop`. Cualquier lógica que no sea wiring va en otro
módulo.
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console

from wso.config import settings


def main() -> int:
    """Punto de entrada sincrónico llamado por el script `wso`."""
    console = Console()
    console.print("[bold cyan]White Suit Operator[/bold cyan] v0.1.0")
    console.print(f"Modo: [yellow]{settings.mode}[/yellow]")

    if settings.mode == "local":
        console.print(f"Modelo local: {settings.local_model} @ {settings.local_url}")
    else:
        console.print(f"Provider cloud: {settings.cloud_provider} ({settings.cloud_model})")

    console.print()

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Sesión interrumpida.[/dim]")
        return 0


async def _run() -> int:
    """Loop principal asincrónico.

    TODO(v1): instanciar AgentLoop con sus dependencias y arrancar el REPL.
    """
    raise NotImplementedError("AgentLoop todavía no está implementado — siguiente milestone.")


if __name__ == "__main__":
    sys.exit(main())
