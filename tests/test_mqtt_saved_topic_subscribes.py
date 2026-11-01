"""MQTT-CFG S5 (MA port) — the subscriber must subscribe what the TUI SAVED.

The TUI's `save_mqtt_config` writes `topic` ("msh/<region>/2/e/<channel>/#");
the subscriber's `_load_config` returns the RAW file and `_subscribe_topics`
reads `root_topic`/`channel` — absent from a TUI-shaped file — so it silently
subscribes the DEFAULTS (the two-schema bug, fixed in MeshForge by
`monitoring/mqtt_settings.py`, S1–S4). Pinned at the END of the path — the
topics handed to `client.subscribe()` — so it survives the port replacing
`_load_config`.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from monitoring import mqtt_subscriber as sub_mod  # noqa: E402


def _subscribed_topics(home):
    with patch.object(sub_mod, "get_real_user_home", return_value=home):
        sub = sub_mod.MQTTNodelessSubscriber()
    sub._client = MagicMock()
    sub._subscribe_topics()
    return [c.args[0] for c in sub._client.subscribe.call_args_list]


def test_tui_shaped_file_subscribes_the_saved_topic_not_defaults(tmp_path):
    cfg_dir = tmp_path / ".config" / "meshanchor"
    cfg_dir.mkdir(parents=True)
    # exactly the shape handlers/mqtt.py:save_mqtt_config writes
    (cfg_dir / "mqtt_nodeless.json").write_text(json.dumps({
        "broker": "mqtt.example.org", "port": 8883,
        "topic": "msh/EU_868/2/e/MediumFast/#",
        "username": None, "password": None,
    }))

    topics = _subscribed_topics(tmp_path)

    assert "msh/EU_868/2/e/MediumFast/#" in topics, topics
    assert not any("LongFast" in t or "/US/" in t for t in topics), topics


def test_absent_file_still_subscribes_defaults(tmp_path):
    # control: the default path must keep working (and must PASS today)
    topics = _subscribed_topics(tmp_path)
    assert f"{sub_mod.DEFAULT_ROOT_TOPIC}/{sub_mod.DEFAULT_CHANNEL}/#" in topics, topics


def test_unreadable_file_refuses_start_never_defaults(tmp_path):
    # MQTT-CFG F6: an operator setting we failed to honour must not run as
    # the public defaults — start() refuses and config_error names the file
    cfg_dir = tmp_path / ".config" / "meshanchor"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "mqtt_nodeless.json").write_text("{not json")
    with patch.object(sub_mod, "get_real_user_home", return_value=tmp_path):
        sub = sub_mod.MQTTNodelessSubscriber()
    assert sub.config_error and "mqtt_nodeless.json" in sub.config_error
    with patch.object(sub, "_connect") as conn:
        assert sub.start() is False
    conn.assert_not_called()


def test_daemon_service_reports_failure_when_settings_unreadable(tmp_path):
    # the daemon must not report "started" while the subscriber refused
    from daemon import MQTTSubscriberService
    cfg_dir = tmp_path / ".config" / "meshanchor"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "mqtt_nodeless.json").write_text("{not json")
    svc = MQTTSubscriberService(broker="localhost", port=1883)
    with patch.object(sub_mod, "get_real_user_home", return_value=tmp_path), \
            patch.object(sub_mod.MQTTNodelessSubscriber, "_connect") as conn:
        assert svc.start() is False
    conn.assert_not_called()
