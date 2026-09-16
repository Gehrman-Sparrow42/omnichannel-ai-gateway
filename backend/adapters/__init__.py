from typing import Dict, Optional
from adapters.base import BaseChannelAdapter, UniversalMessage
from adapters.whatsapp import WhatsAppAdapter
from adapters.instagram import InstagramAdapter
from adapters.messenger import MessengerAdapter
from adapters.telegram import TelegramAdapter

ADAPTERS_REGISTRY: Dict[str, BaseChannelAdapter] = {
    "whatsapp": WhatsAppAdapter(),
    "instagram": InstagramAdapter(),
    "messenger": MessengerAdapter(),
    "telegram": TelegramAdapter(),
}

def get_adapter(channel: str) -> Optional[BaseChannelAdapter]:
    """Retrieves adapter instance for channel name."""
    return ADAPTERS_REGISTRY.get(channel.strip().lower())

__all__ = [
    "BaseChannelAdapter",
    "UniversalMessage",
    "WhatsAppAdapter",
    "InstagramAdapter",
    "MessengerAdapter",
    "TelegramAdapter",
    "ADAPTERS_REGISTRY",
    "get_adapter",
]
