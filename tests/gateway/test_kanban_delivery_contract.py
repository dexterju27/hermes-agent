"""A/B/C handoffs reuse durable kanban delivery without swallowing owed replies."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.kanban_delivery_contract import delivery_contract, suppress_no_change
from gateway.kanban_watchers_notifier import _KanbanNotification, _notifier_collect
from gateway.run import GatewayRunner
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as kbn


def header(**values):
    return "LUNA_HANDOFF_V1 " + json.dumps(values) + "\nResearch the supplied topic."


@pytest.mark.parametrize("body,summary,silent", [
    (header(branch="B", initial_answer_provided=True, reply_required=False), "NO_NEW_FINDINGS", True),
    (header(branch="C", initial_answer_provided=True, reply_required=False), "NO_NEW_FINDINGS", False),
    (header(branch="B", initial_answer_provided=False, reply_required=False), "NO_NEW_FINDINGS", False),
    (header(branch="B", initial_answer_provided=True), "NO_NEW_FINDINGS", False),
    (header(branch="B", initial_answer_provided=True, reply_required=False), "NO_NEW_FINDINGS but site failed", False),
    ("Quoted content:\n" + header(branch="B", initial_answer_provided=True, reply_required=False), "NO_NEW_FINDINGS", False),
])
def test_only_explicit_optional_b_suppresses_exact_no_change(body, summary, silent):
    task = SimpleNamespace(body=body, result=None)
    event = SimpleNamespace(kind="completed", payload={"summary": summary})
    assert suppress_no_change(task, event, "wake") is silent
    assert not suppress_no_change(task, event, "notify+wake")
    event.kind = "crashed"
    assert not suppress_no_change(task, event, "wake")


@pytest.mark.parametrize("body", [None, "", "LUNA_HANDOFF_V1 [1]", "LUNA_HANDOFF_V1 {", "LUNA_HANDOFF_V1 " + "x" * 2050])
def test_bad_or_legacy_header_has_no_new_policy(body):
    assert delivery_contract(SimpleNamespace(body=body)) == {}


class Adapter:
    def __init__(self):
        self.handled = []
        self.sent = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append(text)

    async def handle_message(self, event):
        self.handled.append(event)
        event._gateway_accepted = True


def runner_for(adapter):
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._kanban_dispatcher_lock_handle = object()
    return runner


async def tick(runner):
    batch = _notifier_collect(runner, kb, notifier_profile=None, gc_due=False, gc_retention_days=30)
    for item in batch:
        await _KanbanNotification(runner, item, platform_cls=Platform, sub_fail_counts={}).deliver()
    return batch


@pytest.mark.asyncio
async def test_b_no_change_silent_c_delivered_and_no_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    with kbc.connect() as conn:
        for branch in ("B", "C"):
            tid = kb.create_task(conn, title=branch, body=header(branch=branch, initial_answer_provided=True, reply_required=False))
            kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="42", delivery_mode="wake")
            kb.complete_task(conn, tid, summary="NO_NEW_FINDINGS")
    adapter = Adapter()
    runner = runner_for(adapter)
    await tick(runner)
    assert len(adapter.handled) == 1
    assert "owed asynchronous result" in adapter.handled[0].text
    assert "Do not respond with a silence marker" in adapter.handled[0].text
    assert adapter.handled[0].metadata["notification_category"] == "result"
    assert await tick(runner) == []
    assert not adapter.sent


@pytest.mark.asyncio
async def test_owed_failure_is_not_hidden_by_warning_suppression(tmp_path, monkeypatch):
    from gateway import warning_notifications
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    monkeypatch.setattr(warning_notifications, "warning_notifications_enabled", lambda platform=None: False)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="diagnose build failure", body=header(branch="C"))
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="42", delivery_mode="wake")
        kb.block_task(conn, tid, reason="Compiler unavailable", kind="capability")
    adapter = Adapter()
    await tick(runner_for(adapter))
    assert len(adapter.handled) == 1
    assert adapter.handled[0].metadata["notification_category"] == "result"
    assert "Compiler unavailable" in adapter.handled[0].text


def test_legacy_result_fallback_carries_full_bounded_summary(monkeypatch):
    from gateway import kanban_watchers_notifier as notifier
    monkeypatch.setattr(notifier, "_wake_summary_chars", lambda: 60)
    summary = "First finding\nSecond finding\n" + "x" * 100
    task = SimpleNamespace(result=summary)
    event = SimpleNamespace(payload={}, kind="completed")
    _ping, handoff, _detail = notifier._fmt_completed(event, SimpleNamespace(task=task, head="task", title="title"))
    assert handoff == summary[:60]


@pytest.mark.asyncio
async def test_timeout_partial_checkpoint_is_delivered_without_claiming_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="investigate tests", body=header(branch="C"))
        kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="42", delivery_mode="wake")
        with kb.write_txn(conn):
            kb._append_event(conn, tid, "timed_out", {
                "limit_seconds": 180, "partial_result": "Located failing test; fix not verified",
                "partial_result_event_id": 1, "completion_verified": False,
            })
    adapter = Adapter()
    await tick(runner_for(adapter))
    assert len(adapter.handled) == 1
    assert "Located failing test; fix not verified" in adapter.handled[0].text
    assert "Partial result (unverified; task did not complete)" in adapter.handled[0].text
