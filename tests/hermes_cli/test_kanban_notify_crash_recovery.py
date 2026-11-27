"""Reserved notification cursors survive a notifier crash before delivery ACK."""
import json

from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_notify as kbn


def create(conn):
    tid = kb.create_task(conn, title="generic result")
    kbn.add_notify_sub(conn, task_id=tid, platform="telegram", chat_id="42")
    kb.complete_task(conn, tid, summary="A useful result")
    return {"task_id": tid, "platform": "telegram", "chat_id": "42", "kinds": ["completed"]}


def test_claim_recovered_after_dead_owner_but_not_live_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    with kbc.connect() as first:
        args = create(first)
        old, reserved, events = kbn.claim_unseen_events_for_sub(first, **args)
        assert len(events) == 1
    with kbc.connect() as restarted:
        assert kbn.claim_unseen_events_for_sub(restarted, **args)[2] == []
        monkeypatch.setattr(kbn, "_notify_owner_alive", lambda owner: False)
        old_again, reserved_again, replay = kbn.claim_unseen_events_for_sub(restarted, **args)
        assert (old_again, reserved_again) == (old, reserved)
        assert [event.id for event in replay] == [event.id for event in events]
        kbn.advance_notify_cursor(restarted, **{k: v for k, v in args.items() if k != "kinds"}, new_cursor=reserved)
    with kbc.connect() as after_ack:
        assert kbn.claim_unseen_events_for_sub(after_ack, **args)[2] == []


def test_cleanup_rewind_cannot_undo_ack_or_another_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    with kbc.connect() as conn:
        args = create(conn)
        old, reserved, _ = kbn.claim_unseen_events_for_sub(conn, **args)
        key = {k: v for k, v in args.items() if k != "kinds"}
        original_owner = kbn._notify_owner()
        monkeypatch.setattr(kbn, "_notify_owner", lambda: json.dumps(["other-host:1", "other-start"]))
        assert not kbn.rewind_notify_cursor(conn, **key, old_cursor=old, claimed_cursor=reserved)
        monkeypatch.setattr(kbn, "_notify_owner", lambda: original_owner)
        kbn.advance_notify_cursor(conn, **key, new_cursor=reserved)
        assert not kbn.rewind_notify_cursor(conn, **key, old_cursor=old, claimed_cursor=reserved)
        assert not kbn.unseen_events_for_sub(conn, **args)[1]


def test_recovery_keeps_ping_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "board.db"))
    with kbc.connect() as conn:
        args = create(conn)
        _, reserved, _ = kbn.claim_unseen_events_for_sub(conn, **args)
        key = {k: v for k, v in args.items() if k != "kinds"}
        kbn.record_notify_ping(conn, **key, event_id=reserved)
        monkeypatch.setattr(kbn, "_notify_owner_alive", lambda owner: False)
        assert kbn.claim_unseen_events_for_sub(conn, **args)[2]
        assert kbn.list_notify_subs(conn, args["task_id"])[0]["last_ping_event_id"] == reserved
