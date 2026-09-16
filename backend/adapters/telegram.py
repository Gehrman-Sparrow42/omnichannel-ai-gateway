import logging
from typing import Dict, Any, List, Optional, Tuple
import httpx

import config
from adapters.base import BaseChannelAdapter, UniversalMessage

logger = logging.getLogger("omni-adapter-telegram")


class TelegramAdapter(BaseChannelAdapter):
    """
    Adapter for Telegram Bot API.
    """

    @property
    def channel_name(self) -> str:
        return "telegram"

    def verify_webhook(self, query_params: Dict[str, Any], headers: Dict[str, Any]) -> Tuple[bool, Any]:
        """Validates Telegram secret token header if configured."""
        if not config.TELEGRAM_WEBHOOK_SECRET:
            return True, "OK"

        token_header = headers.get("x-telegram-bot-api-secret-token", "")
        if token_header == config.TELEGRAM_WEBHOOK_SECRET:
            return True, "OK"
        return False, "Invalid secret token"

    async def parse_inbound_webhook(
        self,
        payload: Dict[str, Any],
        branch_id: str,
    ) -> List[UniversalMessage]:
        """Parses Telegram Update object."""
        messages: List[UniversalMessage] = []

        # Check standard message or edited message
        msg_obj = payload.get("message") or payload.get("channel_post")
        if not msg_obj:
            return messages

        chat_obj = msg_obj.get("chat", {})
        chat_id = str(chat_obj.get("id", ""))
        text_content = msg_obj.get("text", "").strip()

        from_user = msg_obj.get("from", {})
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        username = from_user.get("username", "")

        full_name = f"{first_name} {last_name}".strip()
        if username and not full_name:
            full_name = f"@{username}"

        if chat_id and text_content:
            messages.append(
                UniversalMessage(
                    channel="telegram",
                    branch_id=branch_id,
                    customer_id=chat_id,
                    customer_name=full_name or f"Telegram User {chat_id}",
                    text=text_content,
                    metadata={
                        "message_id": msg_obj.get("message_id"),
                        "username": username,
                        "raw": payload,
                    },
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
        """Sends message via Telegram Bot API."""
        bot_token = config.TELEGRAM_BOT_TOKEN
        if bot_token:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            body = {
                "chat_id": customer_id,
                "text": text,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=body)
                    if resp.status_code == 200:
                        logger.info("[TELEGRAM] Message sent to chat_id %s", customer_id)
                        return True
                    else:
                        logger.warning("[TELEGRAM] API returned HTTP %d: %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("[TELEGRAM] Send error: %s", exc)

        logger.info("[TELEGRAM MOCK] Outbound message logged for %s: '%s...'", customer_id, text[:60])
        return True
