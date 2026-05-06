"""Tools de filesystem: read_file, write_file, delete_file, list_directory.

Estas son las tools más usadas y las que tienen la lógica de permisos
más compleja (whitelist de carpetas para read, confirmación obligatoria
para write/delete).

Todas reciben paths como strings y los validan contra el sistema antes
de actuar. La lógica de permisos NO vive acá — vive en
permissions/manager.py. Estas funciones asumen que ya fueron aprobadas.
"""

from __future__ import annotations

# from wso.tools.base import PermissionCategory, tool


# TODO(v1): implementar las cuatro tools como funciones con @tool.
# Esquema previsto:
#
# @tool(
#     name="read_file",
#     category=PermissionCategory.READ,
#     description="Lee el contenido de un archivo de texto.",
#     args_schema={"path": "ruta absoluta del archivo a leer"},
# )
# def read_file(path: str) -> str:
#     ...
#
# @tool(name="write_file", category=PermissionCategory.WRITE, ...)
# def write_file(path: str, content: str) -> str:
#     ...
#
# @tool(name="delete_file", category=PermissionCategory.DELETE, ...)
# def delete_file(path: str) -> str:
#     ...
#
# @tool(name="list_directory", category=PermissionCategory.READ, ...)
# def list_directory(path: str) -> str:
#     ...
