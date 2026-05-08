"""Factory que instancia el ModelClient correcto según la config.

Lee `settings.mode` y devuelve la implementación apropiada.
"""

from __future__ import annotations

from wso.agent.model.base import ModelClient
from wso.config import Settings


def build_model_client(settings: Settings) -> ModelClient:
    """Instanciar el cliente de modelo según la configuración.

    Raises:
        NotImplementedError: si el modo cloud está pedido pero el
            provider correspondiente todavía no está implementado.
        ValueError: si la config es inconsistente (ej: cloud sin
            provider, o sin API key).
    """
    if settings.mode == "local":
        from wso.agent.model.ollama import OllamaClient

        return OllamaClient(
            base_url=settings.local_url,
            model=settings.local_model,
            num_ctx=settings.local_num_ctx,
        )

    if settings.mode == "cloud":
        if settings.cloud_provider is None:
            raise ValueError("WSO_MODE=cloud requiere WSO_CLOUD_PROVIDER seteado.")
        if settings.cloud_model is None:
            raise ValueError("WSO_MODE=cloud requiere WSO_CLOUD_MODEL seteado.")

        match settings.cloud_provider:
            case "anthropic":
                from wso.agent.model.anthropic import AnthropicClient

                if settings.anthropic_api_key is None:
                    raise ValueError("Provider anthropic requiere ANTHROPIC_API_KEY.")
                return AnthropicClient(
                    api_key=settings.anthropic_api_key.get_secret_value(),
                    model=settings.cloud_model,
                )
            case "openai":
                from wso.agent.model.openai import OpenAIClient

                if settings.openai_api_key is None:
                    raise ValueError("Provider openai requiere OPENAI_API_KEY.")
                return OpenAIClient(
                    api_key=settings.openai_api_key.get_secret_value(),
                    model=settings.cloud_model,
                )
            case "google":
                from wso.agent.model.google import GoogleClient

                if settings.google_api_key is None:
                    raise ValueError("Provider google requiere GOOGLE_API_KEY.")
                return GoogleClient(
                    api_key=settings.google_api_key.get_secret_value(),
                    model=settings.cloud_model,
                )

    raise ValueError(f"Modo desconocido: {settings.mode!r}")
