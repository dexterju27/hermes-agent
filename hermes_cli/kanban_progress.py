"""Recover an explicit current-attempt progress checkpoint without reading worker files."""
import json

CHECKPOINT_PREFIX = "CHECKPOINT:"
MAX_CHECKPOINT_CHARS = 4000


def checkpoint_handoff(conn, task_id):
    """A worker's opt-in heartbeat note is partial evidence, never a successful result.

    Keep this read run-scoped and bounded: no older attempt, unrelated comment or
    untrusted artifact path can become the current failure's handoff.
    """
    rows = conn.execute(
        "SELECT e.id, substr(e.payload, 1, 32768) AS payload FROM task_events e "
        "JOIN tasks t ON t.id=e.task_id AND t.current_run_id=e.run_id "
        "WHERE t.id=? AND e.kind='heartbeat' ORDER BY e.id DESC LIMIT 32",
        (task_id,),
    )
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "null")
        except (ValueError, TypeError):
            continue
        note = payload.get("note") if isinstance(payload, dict) else None
        if not isinstance(note, str) or not note.startswith(CHECKPOINT_PREFIX):
            continue
        partial = note[len(CHECKPOINT_PREFIX):].strip()
        if not partial:
            continue
        truncated = len(partial) > MAX_CHECKPOINT_CHARS
        partial = partial[:MAX_CHECKPOINT_CHARS]
        if truncated:
            partial += "… [checkpoint truncated]"
        return {
            "partial_result": partial,
            "partial_result_event_id": row["id"],
            "completion_verified": False,
            "summary": "Partial progress checkpoint (not verified completion):\n" + partial,
        }
    return {}
