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
    local_num_ctx: int = 16384
    """Tamaño del contexto en tokens. Escala el KV cache (y la VRAM) linealmente.

    Default 16384: da headroom para tareas con lecturas grandes (resumir un
    PDF, armar un deck desde un docx). Con num_ctx chico (4096-8192) un solo
    read grande desaloja del contexto el system prompt y lo que el usuario
    pidió, y el modelo "olvida" la tarea a mitad de camino.

    Ajustá según tu hardware:
      - 14B en GPU de 16GB: 16384 entra cómodo (~13GB total).
      - 32B o GPU chica: bajá a 8192/4096 para no spillear a CPU
        (mirá `ollama ps`: querés ver `100% GPU`)."""

    # --- Configuración cloud ---
    cloud_provider: Literal["anthropic", "openai", "google"] | None = None
    cloud_model: str | None = None

    # Base URL custom para endpoints OpenAI-compatible (OpenRouter,
    # Together.ai, Groq, Azure, vLLM local, etc). Solo aplica si
    # cloud_provider="openai". Si es None usa la API default de OpenAI.
    openai_base_url: str | None = None

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

    # --- Logging estructurado ---
    log_enabled: bool = False
    """Si True, cada sesión escribe un .jsonl a `logs_dir` con todos los
    eventos del agente (turn_start, tool_call, observation, permission, etc).
    Útil para debug post-mortem, auditoría, y replay de sesiones.
    Default off para no llenar disco si no lo necesitás."""


# Instancia única reutilizable. Importar como `from wso.config import settings`.
settings = Settings()
