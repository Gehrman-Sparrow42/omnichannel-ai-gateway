import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters import get_adapter
from adapters.base import UniversalMessage


@pytest.mark.asyncio
async def test_whatsapp_adapter_parsing():
    adapter = get_adapter("whatsapp")
    assert adapter is not None
    assert adapter.channel_name == "whatsapp"

    # 1. Local Bridge payload
    bridge_payload = {
        "phone": "905321112233",
        "message": "Giriş saati kaça kadar?",
        "contact_name": "Ahmet Yılmaz",
    }
    messages = await adapter.parse_inbound_webhook(bridge_payload, branch_id="olympos")
    assert len(messages) == 1
    assert messages[0].channel == "whatsapp"
    assert messages[0].branch_id == "olympos"
    assert messages[0].customer_id == "905321112233"
    assert messages[0].customer_name == "Ahmet Yılmaz"
    assert messages[0].text == "Giriş saati kaça kadar?"

    # 2. Meta WhatsApp Cloud API payload
    cloud_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"profile": {"name": "Ayşe Kaya"}, "wa_id": "905334445566"}],
                            "messages": [{"from": "905334445566", "id": "wamid.123", "type": "text", "text": {"body": "Glamping fiyatı nedir?"}}],
                        }
                    }
                ]
            }
        ]
    }
    cloud_msgs = await adapter.parse_inbound_webhook(cloud_payload, branch_id="fethiye")
    assert len(cloud_msgs) == 1
    assert cloud_msgs[0].channel == "whatsapp"
    assert cloud_msgs[0].branch_id == "fethiye"
    assert cloud_msgs[0].customer_id == "905334445566"
    assert cloud_msgs[0].text == "Glamping fiyatı nedir?"


@pytest.mark.asyncio
async def test_telegram_adapter_parsing():
    adapter = get_adapter("telegram")
    assert adapter is not None
    assert adapter.channel_name == "telegram"

    payload = {
        "update_id": 99999,
        "message": {
            "message_id": 101,
            "from": {"id": 1234567, "first_name": "Mehmet", "last_name": "Öz", "username": "mehmetoz"},
            "chat": {"id": 1234567, "type": "private"},
            "text": "Karavan yeri müsait mi?",
        },
    }
    messages = await adapter.parse_inbound_webhook(payload, branch_id="kas")
    assert len(messages) == 1
    assert messages[0].channel == "telegram"
    assert messages[0].branch_id == "kas"
    assert messages[0].customer_id == "1234567"
    assert messages[0].customer_name is not None and "Mehmet Öz" in messages[0].customer_name
    assert messages[0].text == "Karavan yeri müsait mi?"


@pytest.mark.asyncio
async def test_instagram_adapter_parsing():
    adapter = get_adapter("instagram")
    assert adapter is not None
    assert adapter.channel_name == "instagram"

    payload = {
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "ig_cust_888"},
                        "recipient": {"id": "ig_page_111"},
                        "message": {"mid": "mid.456", "text": "Hafta sonu etkinlik var mı?"},
                    }
                ]
            }
        ]
    }
    messages = await adapter.parse_inbound_webhook(payload, branch_id="bodrum")
    assert len(messages) == 1
    assert messages[0].channel == "instagram"
    assert messages[0].branch_id == "bodrum"
    assert messages[0].customer_id == "ig_cust_888"
    assert messages[0].text == "Hafta sonu etkinlik var mı?"


@pytest.mark.asyncio
async def test_messenger_adapter_parsing():
    adapter = get_adapter("messenger")
    assert adapter is not None
    assert adapter.channel_name == "messenger"

    payload = {
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "fb_user_555"},
                        "recipient": {"id": "fb_page_777"},
                        "message": {"mid": "mid.789", "text": "Köpeğimle gelebilir miyim?"},
                    }
                ]
            }
        ]
    }
    messages = await adapter.parse_inbound_webhook(payload, branch_id="akyaka")
    assert len(messages) == 1
    assert messages[0].channel == "messenger"
    assert messages[0].branch_id == "akyaka"
    assert messages[0].customer_id == "fb_user_555"
    assert messages[0].text == "Köpeğimle gelebilir miyim?"
