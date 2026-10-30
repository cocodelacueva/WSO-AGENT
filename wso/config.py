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

# Raíz de la instalación de WSO (donde vive .env, config/, logs/). No es el
# proyecto sobre el que trabaja el agente: ese es el cwd donde corrés `wso`.
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
    local_model: str = "qwen2.5-coder:14b"
    local_num_ctx: int = 16384
    """Tamaño del contexto en tokens. Escala el KV cache (y la VRAM) linealmente.

    Con un 14B en Q4 (~9GB de pesos) en una GPU de 16GB:
      - 16384 con KV cache f16 → ~12.5GB total, entra.
      - 32768 con KV cache q8_0 → ~12.5GB total, entra. Requiere en el
        servidor de Ollama: OLLAMA_FLASH_ATTENTION=1 y OLLAMA_KV_CACHE_TYPE=q8_0.
    Mirá `ollama ps`: querés ver `100% GPU`. El loop mantiene el historial
    por debajo de este número (si se excede, Ollama recorta en silencio)."""

    # --- Configuración cloud ---
    cloud_provider: Literal["anthropic", "openai"] | None = None
    cloud_model: str | None = None

    openai_base_url: str | None = None
    """Endpoint OpenAI-compatible (OpenRouter, Groq, Gemini, vLLM, ...).
    Solo aplica si cloud_provider="openai". None = API de OpenAI."""

    openai_context_window: int = 128_000
    """Contexto del modelo OpenAI/compatible (no hay forma estándar de leerlo)."""

    anthropic_thinking: bool = True
    """Thinking adaptive (visible resumido). Desactivalo para modelos viejos
    que no lo soportan."""

    anthropic_effort: str | None = "high"
    """low | medium | high | xhigh | max. Vacío = default del modelo."""

    # --- API Keys (sin prefijo WSO_, son convenciones globales) ---
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")

    # --- Paths derivados (no van en .env) ---
    @property
    def config_dir(self) -> Path:
        return PROJECT_ROOT / "config"

    @property
    def permissions_file(self) -> Path:
        return self.config_dir / "permissions.toml"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def history_file(self) -> Path:
        """Archivo donde se persiste el historial entre sesiones."""
        return PROJECT_ROOT / ".wso_history.json"

    # --- Loop ---
    step_budget: int = 25
    """Tool calls por turno antes de preguntar si seguir. Programar lleva
    más pasos que una tarea de oficina (leer, editar, correr tests, corregir)."""

    # --- bash ---
    bash_timeout: int = 120
    """Timeout default (segundos) de un comando."""

    bash_max_timeout: int = 600
    """Tope de timeout que el modelo puede pedir."""

    bash_max_output_chars: int = 10000
    """Tope de salida devuelta al modelo (se conserva principio y final)."""

    # --- Persistencia de historial ---
    history_persist: bool = False
    """Si True, el historial se guarda tras cada turno y se restaura al
    arrancar `wso`. `/reset` lo borra."""

    history_max_messages: int = 200
    """Tope de mensajes guardados/restaurados (se conservan los últimos N)."""

    # --- Logging estructurado ---
    log_enabled: bool = False
    """Si True, cada sesión escribe un .jsonl a `logs_dir` con todos los
    eventos del agente."""


# Instancia única reutilizable. Importar como `from wso.config import settings`.
settings = Settings()
