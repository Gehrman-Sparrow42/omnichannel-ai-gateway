import logging
import re
from typing import Dict, Any, List, Optional, Tuple
import httpx

import config
from adapters.base import BaseChannelAdapter, UniversalMessage

logger = logging.getLogger("omni-adapter-whatsapp")


class WhatsAppAdapter(BaseChannelAdapter):
    """
    Adapter for WhatsApp supporting both:
    1. Official Meta WhatsApp Cloud API (Graph API)
    2. Node.js Session Bridge (Local Puppeteer/LocalAuth fallback)
    """

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    def verify_webhook(self, query_params: Dict[str, Any], headers: Dict[str, Any]) -> Tuple[bool, Any]:
        """Validates Meta Cloud API Webhook challenge verification."""
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
        """Parses both Meta Cloud API JSON and local Bridge JSON payloads."""
        messages: List[UniversalMessage] = []

        # 1. Local Bridge payload format
        if "phone" in payload and "message" in payload:
            phone = str(payload.get("phone", "")).strip()
            text = str(payload.get("message", "")).strip()
            name = str(payload.get("contact_name", "")).strip()
            if phone and text:
                messages.append(
                    UniversalMessage(
                        channel="whatsapp",
                        branch_id=branch_id,
                        customer_id=phone,
                        customer_name=name or phone,
                        text=text,
                        metadata={"source": "bridge", "raw": payload},
                    )
                )
            return messages

        # 2. Meta WhatsApp Cloud API format
        entry_list = payload.get("entry", [])
        for entry in entry_list:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = value.get("contacts", [])
                contact_map = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in contacts}

                raw_msgs = value.get("messages", [])
                for raw in raw_msgs:
                    msg_type = raw.get("type")
                    from_phone = raw.get("from")
                    msg_id = raw.get("id")

                    text_content = ""
                    if msg_type == "text":
                        text_content = raw.get("text", {}).get("body", "").strip()
                    elif msg_type == "button":
                        text_content = raw.get("button", {}).get("text", "").strip()
                    elif msg_type == "interactive":
                        interactive = raw.get("interactive", {})
                        if interactive.get("type") == "button_reply":
                            text_content = interactive.get("button_reply", {}).get("title", "")
                        elif interactive.get("type") == "list_reply":
                            text_content = interactive.get("list_reply", {}).get("title", "")

                    if from_phone and text_content:
                        customer_name = contact_map.get(from_phone, from_phone)
                        messages.append(
                            UniversalMessage(
                                channel="whatsapp",
                                branch_id=branch_id,
                                customer_id=from_phone,
                                customer_name=customer_name,
                                text=text_content,
                                metadata={"msg_id": msg_id, "source": "cloud_api", "raw": raw},
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
        """
        Dispatches outbound WhatsApp message via Meta Cloud API or local Bridge.
        """
        clean_phone = re.sub(r"[^\d]", "", customer_id)

        # 1. Try Meta Cloud API if token & phone ID configured
        if config.WHATSAPP_ACCESS_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID:
            url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_phone,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    if resp.status_code in [200, 201]:
                        logger.info("[WHATSAPP] Cloud API sent message to %s", clean_phone)
                        return True
                    else:
                        logger.warning("[WHATSAPP] Cloud API error HTTP %d: %s", resp.status_code, resp.text)
            except Exception as exc:
                logger.error("[WHATSAPP] Cloud API request failed: %s", exc)

        # 2. Fallback to Local Bridge
        bridge_url = f"{config.BRIDGE_API_URL.rstrip('/')}/send-message"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                chat_id = f"{clean_phone}@c.us" if "@" not in customer_id else customer_id
                resp = await client.post(
                    bridge_url,
                    json={"chatId": chat_id, "message": text},
                )
                if resp.status_code == 200:
                    logger.info("[WHATSAPP] Bridge sent message to %s", chat_id)
                    return True
                else:
                    logger.warning("[WHATSAPP] Bridge returned HTTP %d: %s", resp.status_code, resp.text)
        except Exception as bridge_exc:
            logger.debug("[WHATSAPP] Bridge not reachable: %s", bridge_exc)

        logger.info("[WHATSAPP MOCK] Outbound message logged for %s: '%s...'", clean_phone, text[:60])
        return True
