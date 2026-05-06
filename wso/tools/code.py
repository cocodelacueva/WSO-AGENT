"""Tool de ejecución de código Python (run_python).

Stub para v2. Implementación requiere:
    - sandbox real (subprocess con cwd restringido y resource limits)
    - timeout configurable
    - captura de stdout/stderr
    - validación previa del código (AST inspection para bloquear imports
      peligrosos, llamadas a os.system, etc.)
    - allowlist de módulos importables

Diseño previsto:

    @tool(
        name="run_python",
        category=PermissionCategory.EXECUTE,
        description="Ejecuta código Python en un sandbox restringido.",
        args_schema={"code": "código Python a ejecutar"},
    )
    def run_python(code: str) -> str:
        ...
"""

from __future__ import annotations

# Placeholder explícito hasta v2.
