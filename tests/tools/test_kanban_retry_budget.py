"""The existing board retry budget must be configurable through the model tool."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(key, raising=False)
    from hermes_cli import kanban_db as kb
    kb.init_db()
    return path


def create(args):
    from tools import kanban_tools
    from tools.registry import registry
    return json.loads(registry.dispatch("kanban_create", {
        "title": "bounded lookup", "assignee": "default", "workspace_kind": "scratch", **args}))


def test_model_can_request_no_retry_short_task(home):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    result = create({"max_runtime_seconds": 180, "max_retries": 1})
    assert result.get("ok"), result
    with connect_closing() as conn:
        task = kb.get_task(conn, result["task_id"])
        assert task.max_runtime_seconds == 180
        assert task.max_retries == 1
    assert result["max_retries"] == 1


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_attempt_budget_is_refused_before_creating_task(home, value):
    from hermes_cli.kanban_db_connect import connect_closing
    result = create({"max_retries": value})
    assert "max_retries" in result.get("error", ""), result
    with connect_closing() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_omitted_attempt_budget_keeps_dispatcher_default(home):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    result = create({})
    assert result.get("ok"), result
    with connect_closing() as conn:
        assert kb.get_task(conn, result["task_id"]).max_retries is None
