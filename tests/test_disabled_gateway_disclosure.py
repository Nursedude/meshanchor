"""MeshAnchor #151 items 2/3 (KN4YDL, 2026-10-10): top-level ``enabled``
defaults False and ``should_bridge()`` then refuses EVERY message; the only
trace was a DEBUG line the handlers word "blocked by routing rules". A
refusing gateway must say so at startup and name the knob, and the first
refusal must be visible at INFO. Port of MeshForge's disclosure fix."""
from __future__ import annotations

import datetime
import logging
import threading
import types

from gateway import message_routing as mr
from gateway.config import GatewayConfig


def _msg():
    return types.SimpleNamespace(
        source_network="meshcore", source_id="", source_address="",
        destination_id=None, destination_address=None, content="hello",
        is_broadcast=True, timestamp=datetime.datetime.now(), metadata={})


def _router(enabled):
    cfg = GatewayConfig()
    cfg.enabled = enabled
    return mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())


def test_disabled_router_warns_closed_and_names_the_knob(caplog):
    with caplog.at_level(logging.INFO, logger=mr.logger.name):
        _router(False)
    closed = [r for r in caplog.records if "CLOSED" in r.getMessage()]
    assert len(closed) == 1
    assert closed[0].levelno == logging.WARNING
    assert '"enabled": true' in closed[0].getMessage()


def test_first_refusal_is_info_then_quiet(caplog):
    r = _router(False)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=mr.logger.name):
        assert r.should_bridge(_msg()) is False
        assert r.should_bridge(_msg()) is False
    loud = [x for x in caplog.records
            if x.levelno >= logging.INFO and "enabled" in x.getMessage()]
    assert len(loud) == 1, [x.getMessage() for x in caplog.records]


def test_enabled_router_says_nothing_closed(caplog):
    with caplog.at_level(logging.INFO, logger=mr.logger.name):
        r = _router(True)
    assert not [x for x in caplog.records if "CLOSED" in x.getMessage()]
    assert r.should_bridge(_msg()) is True
