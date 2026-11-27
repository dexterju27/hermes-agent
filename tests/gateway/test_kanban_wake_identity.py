"""A producer retry after FIFO admission must not execute its wake twice."""
import asyncio

import pytest

from gateway.platforms.event import MessageEvent
from gateway.wake import deliver_wake
from tests.gateway.test_wake_admitted_before_drain_authority import Hold, admissions, authority_route
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_same_delivery_id_deduplicates_before_and_after_execution(monkeypatch):
    executed, hold = [], Hold()
    runner, adapter, authority, source = await authority_route(monkeypatch, "owner", executed, hold)
    await adapter.handle_message(MessageEvent(text="A", source=source, message_id="A"))
    await asyncio.wait_for(hold.running.wait(), 10)
    await deliver_wake(adapter, text="result", source=source, delivery_id="task-1:event-9")
    await deliver_wake(adapter, text="result", source=source, delivery_id="task-1:event-9")
    hold.finish.set()
    await async_wait_until(
        lambda: admissions(runner, authority, source)[-1] == ("automation", "terminal", "completed"),
        what="one durable wake to finish",
    )
    await deliver_wake(adapter, text="result", source=source, delivery_id="task-1:event-9")
    assert [text for _, text in executed] == ["A", "result"]
    assert len(admissions(runner, authority, source)) == 2
