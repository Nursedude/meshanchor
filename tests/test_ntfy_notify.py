"""Tests for MeshAnchor watchdog ntfy paging (utils/ntfy_notify.py) and its
wiring into fleet_watchdog blackout transitions.

Run: python3 -m pytest tests/test_ntfy_notify.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils import ntfy_notify as nn
from monitoring import fleet_watchdog as wd


@pytest.fixture(autouse=True)
def _no_config_file(monkeypatch):
    """Isolate from any real ~/.config/meshanchor/ntfy.json on the test host."""
    monkeypatch.setattr(nn, "_config_file_values", lambda: {})
    for var in ("MESHANCHOR_NTFY_TOPIC", "MESHANCHOR_NTFY_BASE_URL",
                "MESHANCHOR_NTFY_TOKEN_ENV"):
        monkeypatch.delenv(var, raising=False)


class TestResolveConfig:
    def test_no_topic_is_none(self):
        assert nn.resolve_ntfy_config()[0] is None

    def test_env_topic_and_defaults(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_NTFY_TOPIC", "fleet-xyz")
        topic, base_url, token = nn.resolve_ntfy_config()
        assert topic == "fleet-xyz"
        assert base_url == "https://ntfy.sh"
        assert token is None

    def test_token_resolved_from_named_env(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_NTFY_TOPIC", "t")
        monkeypatch.setenv("MESHANCHOR_NTFY_TOKEN_ENV", "MY_SECRET")
        monkeypatch.setenv("MY_SECRET", "tk_abc")
        assert nn.resolve_ntfy_config()[2] == "tk_abc"

    def test_config_file_fallback(self, monkeypatch):
        monkeypatch.setattr(nn, "_config_file_values",
                            lambda: {"topic": "file-topic", "base_url": "https://n.example"})
        topic, base_url, _ = nn.resolve_ntfy_config()
        assert topic == "file-topic" and base_url == "https://n.example"


class TestPublish:
    def test_noop_when_no_topic(self):
        with patch("urllib.request.urlopen") as m:
            assert nn.publish("t", "m") is False
            m.assert_not_called()

    def test_posts_when_configured(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_NTFY_TOPIC", "fleet-xyz")
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value = MagicMock()
            assert nn.publish("Title", "Body", priority="urgent", tags=["gear"]) is True
            req = m.call_args[0][0]
            assert req.full_url == "https://ntfy.sh/fleet-xyz"
            assert req.get_header("Priority") == "urgent"
            assert req.get_header("Tags") == "gear"
            assert req.data == b"Body"

    def test_swallows_errors(self, monkeypatch):
        monkeypatch.setenv("MESHANCHOR_NTFY_TOPIC", "fleet-xyz")
        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            assert nn.publish("t", "m") is False  # never raises


class TestWatchdogPaging:
    def test_pages_on_opened(self):
        decisions = {k: None for k in wd.ALL_KINDS}
        decisions[wd.KIND_ROLE_DRIFT] = "drift reason"
        summary = {k: "no_change" for k in wd.ALL_KINDS}
        summary[wd.KIND_ROLE_DRIFT] = "opened"
        with patch("utils.ntfy_notify.publish") as pub:
            wd._notify_blackout_transitions(decisions, summary)
        assert pub.call_count == 1
        args, kwargs = pub.call_args
        assert "role_drift" in args[0] and "drift reason" in args[1]
        # role_drift is degraded/latent debt, paged quietly (MF deliberately
        # does not page it at all; MA has no escalation-feed tier so pages min).
        assert kwargs["priority"] == "min"

    def test_pages_cleared_on_closed(self):
        decisions = {k: None for k in wd.ALL_KINDS}
        summary = {k: "no_change" for k in wd.ALL_KINDS}
        summary[wd.KIND_DAEMON_DEAD] = "closed"
        with patch("utils.ntfy_notify.publish") as pub:
            wd._notify_blackout_transitions(decisions, summary)
        assert pub.call_count == 1
        args, kwargs = pub.call_args
        assert "cleared" in args[0] and kwargs["priority"] == "min"

    def test_no_page_on_no_change(self):
        decisions = {k: None for k in wd.ALL_KINDS}
        summary = {k: "no_change" for k in wd.ALL_KINDS}
        with patch("utils.ntfy_notify.publish") as pub:
            wd._notify_blackout_transitions(decisions, summary)
        pub.assert_not_called()

    def test_daemon_dead_pages_urgent(self):
        decisions = {k: None for k in wd.ALL_KINDS}
        decisions[wd.KIND_DAEMON_DEAD] = "daemon down"
        summary = {k: "no_change" for k in wd.ALL_KINDS}
        summary[wd.KIND_DAEMON_DEAD] = "opened"
        with patch("utils.ntfy_notify.publish") as pub:
            wd._notify_blackout_transitions(decisions, summary)
        assert pub.call_args.kwargs["priority"] == "urgent"

    def test_never_raises_when_publish_import_fails(self):
        decisions = {k: None for k in wd.ALL_KINDS}
        summary = {k: "opened" for k in wd.ALL_KINDS}
        with patch.dict("sys.modules", {"utils.ntfy_notify": None}):
            wd._notify_blackout_transitions(decisions, summary)  # must not raise
