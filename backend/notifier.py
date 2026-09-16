import logging
import re
import httpx
from typing import Optional, List, Dict, Any

import config

logger = logging.getLogger("omni-notifier")


def sanitize_phone_for_actions(phone: str) -> tuple[str, str]:
    """
    Cleans raw phone numbers or WhatsApp IDs.
    Returns: (tel_uri: '+90552...', wa_uri: '90552...')
    """
    digits = re.sub(r"[^\d]", "", phone)
    if digits.startswith("0") and len(digits) == 11:
        digits = "9" + digits  # Turkish format: 0532... -> 90532...
    tel_number = f"+{digits}" if not digits.startswith("+") else digits
    wa_number = digits.lstrip("+")
    return tel_number, wa_number


async def send_ntfy_push(
    topic: str,
    title: str,
    message: str,
    priority: int = 4,
    tags: Optional[List[str]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    server_url: str = config.NTFY_SERVER_URL,
) -> bool:
    """
    Sends async push notification to ntfy.sh with interactive mobile actions.
    Priority 5 = Urgent loud alarm sound (bypasses Android DND)
    Priority 4 = High priority alert sound & vibration
    """
    clean_topic = topic.strip().lower()
    if not clean_topic:
        logger.warning("ntfy push aborted: topic is empty.")
        return False

    payload: Dict[str, Any] = {
        "topic": clean_topic,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": tags or ["camp"],
    }
    if actions:
        payload["actions"] = actions

    target_url = server_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                target_url,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            if resp.status_code == 200:
                logger.info("ntfy push sent to topic '%s': %s", clean_topic, title)
                return True
            else:
                logger.warning("ntfy push returned HTTP %d for topic '%s': %s", resp.status_code, clean_topic, resp.text)
                return False
    except Exception as exc:
        logger.warning("Failed to dispatch ntfy push to topic '%s': %s", clean_topic, exc)
        return False


async def send_intervention_alert(
    branch_id: str,
    channel: str,
    customer_id: str,
    customer_name: str,
    user_message: str,
    branch_name: Optional[str] = None,
    custom_topic: Optional[str] = None,
) -> bool:
    """
    Dispatches urgent loud alarm when customer asks for a human agent or complains.
    Routes to the branch's specific ntfy topic.
    """
    target_topic = custom_topic or f"omni-camp-{branch_id.strip().lower()}"
    b_name = branch_name or branch_id.title()
    channel_display = channel.upper()

    title = f"🚨 [{b_name.upper()}] YETKİLİ DEVRALMA TALEBİ"
    body = (
        f"Müşteri bir yetkiliyle görüşmek istiyor!\n\n"
        f"🏢 Şube: {b_name}\n"
        f"📡 Kanal: {channel_display}\n"
        f"👤 Müşteri: {customer_name or customer_id}\n"
        f"💬 Mesaj: \"{user_message}\"\n\n"
        f"⏸️ AI bu sohbet için 2 saatliğine SUSTURULDU. Lütfen kontrol panelinden veya aşağıdaki butonlardan iletişime geçiniz!"
    )

    actions: List[Dict[str, Any]] = []

    if channel == "whatsapp":
        tel_num, wa_num = sanitize_phone_for_actions(customer_id)
        actions.append({"action": "view", "label": "📞 Müşteriyi Ara", "url": f"tel:{tel_num}"})
        actions.append({"action": "view", "label": "💬 WhatsApp'ta Aç", "url": f"https://wa.me/{wa_num}"})
    elif channel == "telegram":
        clean_user = customer_id.replace("@", "")
        actions.append({"action": "view", "label": "💬 Telegram'da Aç", "url": f"https://t.me/{clean_user}"})

    # Dashboard Direct URL action
    actions.append({
        "action": "view",
        "label": "🖥️ Kontrol Merkezinde Aç",
        "url": f"http://127.0.0.1:{config.PORT}/#chat-{channel}-{customer_id}"
    })

    # 1. Send to branch topic
    await send_ntfy_push(
        topic=target_topic,
        title=title,
        message=body,
        priority=5,  # Urgent continuous ringing
        tags=["rotating_light", "sos", "warning"],
        actions=actions,
    )

    # 2. Also send mirror to global alerts topic if different
    if config.NTFY_GLOBAL_TOPIC and config.NTFY_GLOBAL_TOPIC != target_topic:
        await send_ntfy_push(
            topic=config.NTFY_GLOBAL_TOPIC,
            title=title,
            message=body,
            priority=5,
            tags=["rotating_light", "sos"],
            actions=actions,
        )

    return True


async def send_reservation_lead_alert(
    branch_id: str,
    channel: str,
    customer_id: str,
    customer_name: str,
    user_message: str,
    branch_name: Optional[str] = None,
    custom_topic: Optional[str] = None,
) -> bool:
    """
    Dispatches high-priority notification when a booking/reservation intent is detected.
    """
    target_topic = custom_topic or f"omni-camp-{branch_id.strip().lower()}"
    b_name = branch_name or branch_id.title()
    channel_display = channel.upper()

    title = f"🏕️ [{b_name.upper()}] YENİ REZERVASYON TALEBİ"
    body = (
        f"Yeni bir konaklama/rezervasyon adayı tespit edildi!\n\n"
        f"🏢 Şube: {b_name}\n"
        f"📡 Kanal: {channel_display}\n"
        f"👤 Müşteri: {customer_name or customer_id}\n"
        f"💬 Talep Özeti: \"{user_message}\"\n\n"
        f"✅ Müşteri sistemde 'Rezervasyon Adayı (Lead)' olarak etiketlendi."
    )

    actions: List[Dict[str, Any]] = []
    if channel == "whatsapp":
        tel_num, wa_num = sanitize_phone_for_actions(customer_id)
        actions.append({"action": "view", "label": "📞 Müşteriyi Ara", "url": f"tel:{tel_num}"})
        actions.append({"action": "view", "label": "💬 WhatsApp'ta Aç", "url": f"https://wa.me/{wa_num}"})

    actions.append({
        "action": "view",
        "label": "🖥️ Kontrol Merkezinde Aç",
        "url": f"http://127.0.0.1:{config.PORT}/#leads"
    })

    await send_ntfy_push(
        topic=target_topic,
        title=title,
        message=body,
        priority=4,  # High priority sound
        tags=["tent", "bell", "moneybag"],
        actions=actions,
    )

    if config.NTFY_GLOBAL_TOPIC and config.NTFY_GLOBAL_TOPIC != target_topic:
        await send_ntfy_push(
            topic=config.NTFY_GLOBAL_TOPIC,
            title=title,
            message=body,
            priority=4,
            tags=["tent", "bell"],
            actions=actions,
        )

    return True
