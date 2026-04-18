"""Model Router providers.

Each provider adapts a specific model API to the router's streaming
`ModelChunk` interface. Add new providers here and register them in
`tename.router.service.ModelRouter`.
"""

from tename.router.providers.anthropic import AnthropicProvider
from tename.router.providers.base import ProviderInterface

__all__ = ["AnthropicProvider", "ProviderInterface"]
