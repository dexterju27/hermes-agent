"""``kanban.wake_summary_chars``: a completed task's wake message can carry the whole run summary.

By default the woken agent gets only the summary's first line and has to call ``kanban_show`` for
the rest. With the setting, the wake text carries the summary (capped); the chat ping stays one line.
"""
from types import SimpleNamespace

from gateway import kanban_watchers_notifier as notifier

SUMMARY = "Go, but only on the way.\n- Opens 10:00\n- Ticket 2,400 yen"


def _completed(monkeypatch, chars):
    monkeypatch.setattr(notifier, "_wake_summary_chars", lambda: chars)
    ev = SimpleNamespace(payload={"summary": SUMMARY})
    n = SimpleNamespace(head="@worker t_1", title="Reel", task=None)
    return notifier._fmt_completed(ev, n)


def test_default_keeps_the_first_line(monkeypatch):
    ping, wake, _ = _completed(monkeypatch, 0)
    assert wake == "Go, but only on the way."
    assert "Opens 10:00" not in ping


def test_setting_carries_the_whole_summary_and_leaves_the_ping_alone(monkeypatch):
    ping, wake, _ = _completed(monkeypatch, 4000)
    assert wake == SUMMARY
    assert "Opens 10:00" not in ping and "Go, but only on the way." in ping


def test_setting_caps_the_summary(monkeypatch):
    _, wake, _ = _completed(monkeypatch, 30)
    assert wake == SUMMARY[:30]


def test_unreadable_config_means_first_line(monkeypatch):
    import hermes_cli.config as config
    monkeypatch.setattr(config, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert notifier._wake_summary_chars() == 0
