import asyncio
import time
import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field

import config

logger = logging.getLogger("omni-debounce")


@dataclass
class DebounceEntry:
    branch_id: str
    channel: str
    customer_id: str
    customer_name: Optional[str]
    messages: List[str] = field(default_factory=list)
    first_received_at: float = field(default_factory=time.time)
    last_received_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None


class SlidingDebounceBuffer:
    """
    Sliding-window debounce buffer for multi-channel messaging.
    Aggregates rapid successive messages sent within `window_seconds`
    into a single cohesive customer prompt before invoking AI inference.
    """

    def __init__(self, window_seconds: int = config.DEBOUNCE_SECONDS):
        self.window_seconds = max(1, window_seconds)
        self.buffers: Dict[str, DebounceEntry] = {}
        self._flush_callback: Optional[Callable[[str, str, str, Optional[str], str], Awaitable[None]]] = None

    def set_flush_callback(
        self,
        callback: Callable[[str, str, str, Optional[str], str], Awaitable[None]],
    ):
        """Sets the async callback to invoke when buffer expires and flushes."""
        self._flush_callback = callback

    def _make_key(self, branch_id: str, channel: str, customer_id: str) -> str:
        return f"{branch_id.strip().lower()}:{channel.strip().lower()}:{customer_id.strip()}"

    async def add_message(
        self,
        branch_id: str,
        channel: str,
        customer_id: str,
        message: str,
        customer_name: Optional[str] = None,
    ) -> bool:
        """
        Adds incoming message into the buffer.
        If message is special command like '/reset', flushes immediately.
        Returns True if queued, False otherwise.
        """
        msg_clean = message.strip()
        if not msg_clean:
            return False

        key = self._make_key(branch_id, channel, customer_id)

        # Immediate bypass for reset command
        if msg_clean.lower() == "/reset":
            await self._execute_flush(branch_id, channel, customer_id, customer_name, [msg_clean])
            return True

        if key in self.buffers:
            entry = self.buffers[key]
            # Cancel existing pending timer
            if entry.task and not entry.task.done():
                entry.task.cancel()

            entry.messages.append(msg_clean)
            entry.last_received_at = time.time()
            if customer_name and not entry.customer_name:
                entry.customer_name = customer_name

            logger.info(
                "[DEBOUNCE] Appended message to buffer for %s (%d messages queued). Resetting %ds timer.",
                key,
                len(entry.messages),
                self.window_seconds,
            )

            # Start fresh timer
            entry.task = asyncio.create_task(self._wait_and_flush(key))
        else:
            logger.info(
                "[DEBOUNCE] Initializing new buffer for %s (%ds debounce window started).",
                key,
                self.window_seconds,
            )
            entry = DebounceEntry(
                branch_id=branch_id,
                channel=channel,
                customer_id=customer_id,
                customer_name=customer_name,
                messages=[msg_clean],
            )
            self.buffers[key] = entry
            entry.task = asyncio.create_task(self._wait_and_flush(key))

        return True

    async def _wait_and_flush(self, key: str):
        """Waits for window_seconds, then triggers flush."""
        try:
            await asyncio.sleep(self.window_seconds)
            if key in self.buffers:
                entry = self.buffers.pop(key, None)
                if entry and entry.messages:
                    await self._execute_flush(
                        entry.branch_id,
                        entry.channel,
                        entry.customer_id,
                        entry.customer_name,
                        entry.messages,
                    )
        except asyncio.CancelledError:
            # Expected when subsequent message resets timer
            pass
        except Exception as exc:
            logger.exception("Error in debounce timer execution for %s: %s", key, exc)

    async def _execute_flush(
        self,
        branch_id: str,
        channel: str,
        customer_id: str,
        customer_name: Optional[str],
        messages: List[str],
    ):
        """Flushes joined prompt to the registered processor callback."""
        aggregated_text = "\n".join(messages).strip()
        logger.info(
            "[DEBOUNCE] Flushing %d messages for %s:%s:%s -> \"%s...\"",
            len(messages),
            branch_id,
            channel,
            customer_id,
            aggregated_text[:80],
        )

        if self._flush_callback:
            try:
                await self._flush_callback(
                    branch_id,
                    channel,
                    customer_id,
                    customer_name,
                    aggregated_text,
                )
            except Exception as e:
                logger.exception("Exception in debounce flush callback: %s", e)

    def get_pending_count(self) -> int:
        """Returns number of active pending debounce buffers."""
        return len(self.buffers)

    def clear_all(self):
        """Cancels all active debounce timers."""
        for key, entry in list(self.buffers.items()):
            if entry.task and not entry.task.done():
                entry.task.cancel()
        self.buffers.clear()


# Global sliding debounce buffer instance
debounce_buffer = SlidingDebounceBuffer()
