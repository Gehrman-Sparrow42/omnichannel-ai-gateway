import abc
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class UniversalMessage(BaseModel):
    """
    Channel-agnostic universal message representation.
    """
    channel: str = Field(..., description="whatsapp, instagram, messenger, telegram")
    branch_id: str = Field(..., description="Target branch slug (e.g. olympos, fethiye)")
    customer_id: str = Field(..., description="Channel customer identifier (phone, psid, ig_id, chat_id)")
    customer_name: Optional[str] = Field(default="", description="Customer display name if available")
    direction: str = Field(default="inbound", description="inbound or outbound")
    role: str = Field(default="user", description="user, assistant, or agent")
    text: str = Field(..., description="Text content")
    media: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Media attachments")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Raw channel metadata")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BaseChannelAdapter(abc.ABC):
    """
    Abstract Base Class for omnichannel adapters.
    """

    @property
    @abc.abstractmethod
    def channel_name(self) -> str:
        """Name of the channel: whatsapp, instagram, messenger, telegram."""
        pass

    @abc.abstractmethod
    def verify_webhook(self, query_params: Dict[str, Any], headers: Dict[str, Any]) -> Tuple[bool, Any]:
        """Verifies webhook challenge or signature from channel platform."""
        pass

    @abc.abstractmethod
    async def parse_inbound_webhook(
        self,
        payload: Dict[str, Any],
        branch_id: str,
    ) -> List[UniversalMessage]:
        """Normalizes raw channel webhook payload into standard UniversalMessage objects."""
        pass

    @abc.abstractmethod
    async def send_message(
        self,
        branch_id: str,
        customer_id: str,
        text: str,
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Dispatches outbound message to customer via platform API."""
        pass
