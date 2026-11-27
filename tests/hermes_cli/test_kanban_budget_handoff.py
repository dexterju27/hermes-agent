"""Configured timeout/crash budgets and run-scoped partial-result recovery."""
import json
import os
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_progress import checkpoint_handoff


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kbc.connect() as conn:
        yield conn


def running(conn, *, cap=1, attempts=None, age=30):
    tid = kb.create_task(conn, title="bounded work", assignee="worker",
                         max_runtime_seconds=cap, max_retries=attempts)
    claim(conn, tid, age=age)
    return tid


def claim(conn, tid, *, age=30):
    task = kb.claim_task(conn, tid)
    assert task is not None
    kbd._set_worker_pid(conn, tid, os.getpid())
    now = int(time.time())
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET started_at=?,last_heartbeat_at=? WHERE id=?", (now-3600, now, tid))
        conn.execute("UPDATE task_runs SET started_at=? WHERE id=?", (now-age, task.current_run_id))


def timeout_tick(conn, monkeypatch, *, failure_limit=2):
    # Simulate an identified running worker that SIGTERM/SIGKILL can stop; no OS
    # signal or wall-clock wait reaches the actual test runner process.
    monkeypatch.setattr(kbd, "_worker_alive", lambda *_: True)
    monkeypatch.setattr(kb, "_pid_alive", lambda *_: False)
    monkeypatch.setattr(kbd, "_kill_fn", lambda *_: lambda *_: None)
    monkeypatch.setattr(kbd, "_poll_worker_exit", lambda *_: None)
    monkeypatch.setattr(kbd, "_sigkill", lambda *_: True)
    return kbd.dispatch_once(conn, max_spawn=0, failure_limit=failure_limit, reconcile_orphans=False)


def test_dispatcher_timeout_limit_one_blocks_without_respawn(board, monkeypatch):
    tid = running(board)
    kbd.heartbeat_worker(board, tid, note="CHECKPOINT: Draft saved; tests remain; source artifact: draft-v1")
    result = timeout_tick(board, monkeypatch, failure_limit=1)
    assert tid in result.timed_out
    assert kb.get_task(board, tid).status == "blocked"
    timeout_tick(board, monkeypatch, failure_limit=1)
    assert len(kb.list_runs(board, tid)) == 1
    events = [e for e in kb.list_events(board, tid) if e.kind in {"timed_out", "gave_up"}]
    assert len(events) == 2
    for event in events:
        assert event.payload["completion_verified"] is False
        assert "Draft saved" in event.payload["partial_result"]
    closed = kb.list_runs(board, tid)[0]
    assert closed.outcome == "timed_out"
    assert "not verified completion" in closed.summary
    assert "Draft saved" in kb.build_worker_context(board, tid)
    assert kb.get_task(board, tid).result is None


def test_task_attempt_override_survives_reopen_and_beats_dispatcher_limit(board, monkeypatch):
    tid = running(board, attempts=2)
    kbd.heartbeat_worker(board, tid, note="CHECKPOINT: Two sources read, one remaining")
    timeout_tick(board, monkeypatch, failure_limit=1)
    assert kb.get_task(board, tid).status == "ready"
    # A new DB connection sees the persisted counter/checkpoint; no process-local budget.
    with kbc.connect() as other:
        assert kb.get_task(other, tid).consecutive_failures == 1
        assert "Two sources read" in kb.build_worker_context(other, tid)
        claim(other, tid)
        timeout_tick(other, monkeypatch, failure_limit=1)
        assert kb.get_task(other, tid).status == "blocked"
        assert kb.get_task(other, tid).consecutive_failures == 2
        assert kb.list_runs(other, tid)[-1].summary is None  # no stale prior-attempt checkpoint


def test_larger_dispatcher_budget_does_not_emit_premature_gave_up(board, monkeypatch):
    tid = running(board)
    timeout_tick(board, monkeypatch, failure_limit=4)
    claim(board, tid)
    timeout_tick(board, monkeypatch, failure_limit=4)
    assert kb.get_task(board, tid).status == "ready"
    assert not any(e.kind == "gave_up" for e in kb.list_events(board, tid))


def test_crash_uses_dispatcher_budget_and_preserves_checkpoint(board, monkeypatch):
    tid = running(board)
    kbd.heartbeat_worker(board, tid, note="CHECKPOINT: Located failing test; fix not verified")
    monkeypatch.setattr(kbd, "_worker_alive", lambda *_: False)
    monkeypatch.setattr(kb, "_pid_alive", lambda *_: False)
    monkeypatch.setattr(kbd, "_classify_worker_exit", lambda *_: ("nonzero_exit", 1))
    result = kbd.dispatch_once(board, max_spawn=0, failure_limit=1, reconcile_orphans=False)
    assert tid in result.crashed
    assert kb.get_task(board, tid).status == "blocked"
    assert "Located failing test" in kb.list_runs(board, tid)[0].summary
    assert kb.list_events(board, tid)[-1].payload["completion_verified"] is False


def test_uncapped_long_work_and_fresh_attempt_are_not_killed(board, monkeypatch):
    long_task = running(board, cap=None, age=86400)
    fresh = running(board, cap=180, age=1)
    result = timeout_tick(board, monkeypatch, failure_limit=1)
    assert result.timed_out == []
    assert kb.get_task(board, long_task).status == "running"
    assert kb.get_task(board, fresh).status == "running"


def test_checkpoint_is_explicit_bounded_current_task_and_current_run(board):
    tid = running(board)
    other = running(board)
    kbd.heartbeat_worker(board, other, note="CHECKPOINT: other private task")
    kbd.heartbeat_worker(board, tid, note="still working")
    assert checkpoint_handoff(board, tid) == {}
    kbd.heartbeat_worker(board, tid, note="CHECKPOINT: " + "a"*6000)
    kbd.heartbeat_worker(board, tid)  # automatic liveness does not erase progress
    with kb.write_txn(board):
        kb._append_event(board, tid, "heartbeat", {"note": 7})
        board.execute("INSERT INTO task_events(task_id,run_id,kind,payload,created_at) VALUES(?,?,'heartbeat','broken',?)",
                      (tid, kb.get_task(board, tid).current_run_id, int(time.time())))
    result = checkpoint_handoff(board, tid)
    assert len(result["partial_result"]) < 4100
    assert "truncated" in result["partial_result"]
    assert "other private" not in json.dumps(result)
    assert result["completion_verified"] is False
