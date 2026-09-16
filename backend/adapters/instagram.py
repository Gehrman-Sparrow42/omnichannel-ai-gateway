import logging
from typing import Dict, Any, List, Optional, Tuple
import httpx

import config
from adapters.base import BaseChannelAdapter, UniversalMessage

logger = logging.getLogger("omni-adapter-instagram")


class InstagramAdapter(BaseChannelAdapter):
    """
    Adapter for Instagram Direct Messages via Meta Graph API.
    """

    @property
    def channel_name(self) -> str:
        return "instagram"

    def verify_webhook(self, query_params: Dict[str, Any], headers: Dict[str, Any]) -> Tuple[bool, Any]:
        """Validates Meta Instagram Webhook challenge verification."""
        mode = query_params.get("hub.mode")
        token = query_params.get("hub.verify_token")
        challenge = query_params.get("hub.challenge")

        if mode == "subscribe" and token == config.META_WEBHOOK_VERIFY_TOKEN:
            return True, challenge
        return False, "Verification failed"

    async def parse_inbound_webhook(
        self,
        payload: Dict[str, Any],
        branch_id: str,
    ) -> List[UniversalMessage]:
        """Parses Meta Instagram Messaging webhook payload."""
        messages: List[UniversalMessage] = []

        entry_list = payload.get("entry", [])
        for entry in entry_list:
            messaging_events = entry.get("messaging", [])
            for event in messaging_events:
                sender_id = event.get("sender", {}).get("id")
                recipient_id = event.get("recipient", {}).get("id")
                msg = event.get("message", {})

                # Ignore echo messages or delivery receipts
                if msg.get("is_echo", False):
                    continue

                text_content = msg.get("text", "").strip()
                msg_id = msg.get("mid")

                if sender_id and text_content:
                    messages.append(
                        UniversalMessage(
                            channel="instagram",
                            branch_id=branch_id,
                            customer_id=str(sender_id),
                            customer_name=f"IG User {str(sender_id)[-4:]}",
                            text=text_content,
                            metadata={"msg_id": msg_id, "recipient_id": recipient_id, "raw": event},
                        )
                    )

        return messages

    async def send_message(
        self,
        branch_id: str,
        customer_id: str,
        text: str,
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Dispatches outbound message via Meta Graph API for Instagram."""
        account_id = config.INSTAGRAM_ACCOUNT_ID or "me"
        token = config.INSTAGRAM_ACCESS_TOKEN

        if token:
            url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{account_id}/messages"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            body = {
                "recipient": {"id": customer_id},
                "message": {"text": text},
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    if resp.status_code in [200, 201]:
                        logger.info("[INSTAGRAM] Message sent to %s", customer_id)
                        return True
                    else:
                        logger.warning("[INSTAGRAM] Graph API returned HTTP %d: %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("[INSTAGRAM] Send error: %s", exc)

        logger.info("[INSTAGRAM MOCK] Outbound message logged for %s: '%s...'", customer_id, text[:60])
        return True
