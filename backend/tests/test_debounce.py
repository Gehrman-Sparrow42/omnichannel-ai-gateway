import pytest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debounce import SlidingDebounceBuffer


@pytest.mark.asyncio
async def test_debounce_aggregates_messages():
    buffer = SlidingDebounceBuffer(window_seconds=1)
    flushed_results = []

    async def mock_callback(branch_id, channel, customer_id, customer_name, text):
        flushed_results.append({
            "branch_id": branch_id,
            "channel": channel,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "text": text,
        })

    buffer.set_flush_callback(mock_callback)

    # Add 3 rapid successive messages from same user
    await buffer.add_message("olympos", "whatsapp", "905551112233", "Selamlar", "Can")
    await buffer.add_message("olympos", "whatsapp", "905551112233", "Hafta sonu boş yeriniz var mı?", "Can")
    await buffer.add_message("olympos", "whatsapp", "905551112233", "2 kişiyiz", "Can")

    assert buffer.get_pending_count() == 1
    assert len(flushed_results) == 0

    # Wait for the 1-second debounce timer to expire
    await asyncio.sleep(1.3)

    assert len(flushed_results) == 1
    flushed = flushed_results[0]
    assert flushed["branch_id"] == "olympos"
    assert flushed["channel"] == "whatsapp"
    assert flushed["customer_id"] == "905551112233"
    assert "Selamlar\nHafta sonu boş yeriniz var mı?\n2 kişiyiz" == flushed["text"]
    assert buffer.get_pending_count() == 0


@pytest.mark.asyncio
async def test_debounce_immediate_reset():
    buffer = SlidingDebounceBuffer(window_seconds=10)
    flushed_results = []

    async def mock_callback(branch_id, channel, customer_id, customer_name, text):
        flushed_results.append(text)

    buffer.set_flush_callback(mock_callback)

    # /reset command should bypass debounce delay immediately
    await buffer.add_message("fethiye", "telegram", "12345", "/reset")
    assert len(flushed_results) == 1
    assert flushed_results[0] == "/reset"
