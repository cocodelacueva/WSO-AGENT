"""Configuración global de WSO.

Lee las variables del .env y expone un objeto `Settings` validado.
Este módulo es la única fuente de verdad para configuración —
ningún otro módulo debe leer `os.environ` directamente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto (donde vive .env, config/, workspace/, logs/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuración de WSO leída desde .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="WSO_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Modo de operación ---
    mode: Literal["local", "cloud"] = "local"

    # --- Configuración local (Ollama) ---
    local_url: str = "http://localhost:11434"
    local_model: str = "qwen2.5-coder:32b"
    local_num_ctx: int = 8192
    """Tamaño del contexto en tokens. Reducirlo libera VRAM (KV cache).
    8192 funciona bien para WSO; bajalo a 4096 si tu GPU tiene poca memoria."""

    # --- Configuración cloud ---
    cloud_provider: Literal["anthropic", "openai", "google"] | None = None
    cloud_model: str | None = None

    # --- API Keys (sin prefijo WSO_, son convenciones globales) ---
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")

    # --- Paths derivados (no van en .env) ---
    @property
    def workspace_dir(self) -> Path:
        return PROJECT_ROOT / "workspace"

    @property
    def context_dir(self) -> Path:
        return self.workspace_dir / "context"

    @property
    def automations_dir(self) -> Path:
        return self.workspace_dir / "automations"

    @property
    def output_dir(self) -> Path:
        return self.workspace_dir / "output"

    @property
    def config_dir(self) -> Path:
        return PROJECT_ROOT / "config"

    @property
    def permissions_file(self) -> Path:
        return self.config_dir / "permissions.toml"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    # --- Constantes del loop ---
    step_budget: int = 10
    """Cantidad máxima de tool calls antes de pedir continuación."""


# Instancia única reutilizable. Importar como `from wso.config import settings`.
settings = Settings()
