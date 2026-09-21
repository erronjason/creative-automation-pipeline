from __future__ import annotations

from .base import ImageProvider, ProviderError, ProviderStatus
from .firefly import FireflyProvider
from .mock import MockProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider

REGISTRY: dict[str, type[ImageProvider]] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "firefly": FireflyProvider,
}


def get_provider(name: str) -> ImageProvider:
    try:
        return REGISTRY[name]()
    except KeyError:
        raise ValueError(f"unknown provider '{name}'; choose from {sorted(REGISTRY)}") from None


__all__ = ["REGISTRY", "ImageProvider", "ProviderError", "ProviderStatus", "get_provider"]
