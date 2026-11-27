"""A profile with kanban.board + kanban.lock_board works on its own board and cannot reach another."""
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
    from hermes_cli import kanban_db_boards as boards
    kb.init_db()
    boards.create_board("second")
    kb.init_db(board="second")
    return path


def configure(monkeypatch, **kanban):
    from tools import kanban_tools
    monkeypatch.setattr(kanban_tools, "load_config", lambda: {"kanban": kanban})


def call(tool, **args):
    from tools import kanban_tools  # noqa: F401  (registers the tools)
    from tools.registry import registry
    return json.loads(registry.dispatch(tool, args))


def create(**args):
    return call("kanban_create", title="lookup", assignee="default", workspace_kind="scratch", **args)


def titles_on(board):
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    with connect_closing(board=board) as conn:
        return [task.title for task in kb.list_tasks(conn)]


def test_without_the_setting_nothing_changes(home, monkeypatch):
    configure(monkeypatch)
    assert create().get("ok")
    assert create(board="second").get("ok")
    assert titles_on("default") == ["lookup"]
    assert titles_on("second") == ["lookup"]


def test_profile_board_is_used_when_the_call_names_none(home, monkeypatch):
    configure(monkeypatch, board="second", lock_board=True)
    result = create()
    assert result.get("ok"), result
    assert titles_on("second") == ["lookup"]
    assert titles_on("default") == []
    assert call("kanban_show", task_id=result["task_id"]).get("task", {}).get("id") == result["task_id"]


def test_locked_profile_cannot_create_on_another_board(home, monkeypatch):
    configure(monkeypatch, board="second", lock_board=True)
    result = create(board="default")
    assert not result.get("ok"), result
    assert "board 'second' only" in json.dumps(result)
    assert titles_on("default") == []


@pytest.mark.parametrize("tool", ["kanban_show", "kanban_list", "kanban_comment", "kanban_attachments"])
def test_locked_profile_cannot_read_or_touch_another_board(home, monkeypatch, tool):
    configure(monkeypatch)
    other = create()["task_id"]  # a task on the default board, as another person's profile would create it
    configure(monkeypatch, board="second", lock_board=True)
    args = {"board": "default"}
    if tool != "kanban_list":
        args["task_id"] = other
    if tool == "kanban_comment":
        args["body"] = "hello"
    result = call(tool, **args)
    assert not result.get("ok"), result
    assert "board 'second' only" in json.dumps(result)


def test_locked_profile_does_not_see_another_boards_task_by_id(home, monkeypatch):
    configure(monkeypatch)
    other = create()["task_id"]
    configure(monkeypatch, board="second", lock_board=True)
    result = call("kanban_show", task_id=other)
    assert not result.get("ok"), result
    assert "lookup" not in json.dumps(result)
    assert call("kanban_list").get("tasks", []) == []


def test_unlocked_profile_board_is_only_a_default(home, monkeypatch):
    configure(monkeypatch, board="second")
    assert create(board="default").get("ok")
    assert titles_on("default") == ["lookup"]


def test_worker_keeps_its_dispatcher_pin(home, monkeypatch):
    configure(monkeypatch, board="second", lock_board=True)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    from tools.kanban_tools import _profile_board
    assert _profile_board(None) is None
    assert _profile_board("default") == "default"
