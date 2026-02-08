"""
Tests for PSKReporter MQTT subscriber.

Run: python3 -m pytest tests/test_pskreporter.py -v
"""

import json
import time
import pytest
from unittest.mock import patch, MagicMock

from src.monitoring.pskreporter_subscriber import (
    PSKReporterSubscriber,
    PSKSpot,
    PSKR_BROKER,
    PSKR_PORT,
    PSKR_PORT_TLS,
    PSKR_BASE_TOPIC,
    MAX_SPOTS,
    create_pskreporter_subscriber,
)


# =========================================================================
# PSKSpot dataclass tests
# =========================================================================


class TestPSKSpot:
    """Tests for PSKSpot dataclass."""

    def test_spot_creation(self):
        """Test creating a PSKSpot with basic data."""
        spot = PSKSpot(
            sequence=12345,
            frequency=14074000,
            mode="FT8",
            snr=-10,
            timestamp=1700000000,
            sender_call="WH6GXZ",
            sender_locator="BL11",
            receiver_call="KH6RS",
            receiver_locator="BL01",
            band="20m",
        )
        assert spot.sender_call == "WH6GXZ"
        assert spot.frequency == 14074000
        assert spot.mode == "FT8"
        assert spot.band == "20m"
        assert spot.snr == -10

    def test_frequency_mhz(self):
        """Test frequency conversion to MHz."""
        spot = PSKSpot(frequency=14074000)
        assert abs(spot.frequency_mhz - 14.074) < 0.001

    def test_frequency_mhz_zero(self):
        """Test frequency_mhz with zero frequency."""
        spot = PSKSpot(frequency=0)
        assert spot.frequency_mhz == 0.0

    def test_age_seconds(self):
        """Test age calculation."""
        spot = PSKSpot(received_at=time.time() - 10)
        assert 9 < spot.age_seconds < 12


# =========================================================================
# PSKReporterSubscriber tests
# =========================================================================


class TestPSKReporterSubscriber:
    """Tests for PSKReporterSubscriber class."""

    def test_default_config(self):
        """Test default configuration values."""
        with patch.object(PSKReporterSubscriber, '_load_config') as mock_load:
            mock_load.return_value = {
                "broker": PSKR_BROKER,
                "port": PSKR_PORT,
                "use_tls": False,
                "callsign": "",
                "bands": [],
                "modes": [],
                "max_spots": MAX_SPOTS,
                "enabled": False,
                "auto_reconnect": True,
                "reconnect_delay": 5,
                "max_reconnect_delay": 60,
            }
            sub = PSKReporterSubscriber()
            assert sub._config["broker"] == PSKR_BROKER
            assert sub._config["port"] == PSKR_PORT
            assert sub._config["use_tls"] is False
            assert sub._config["callsign"] == ""

    def test_explicit_config(self):
        """Test subscriber with explicit configuration."""
        config = {
            "broker": "custom.broker.com",
            "port": 1884,
            "use_tls": True,
            "callsign": "WH6GXZ",
            "bands": ["20m", "40m"],
            "modes": ["FT8"],
            "max_spots": 1000,
            "enabled": True,
        }
        sub = PSKReporterSubscriber(config=config)
        assert sub._config["broker"] == "custom.broker.com"
        assert sub._config["callsign"] == "WH6GXZ"
        assert sub._config["bands"] == ["20m", "40m"]

    def test_is_connected_default(self):
        """Test initial connection state."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        assert sub.is_connected() is False

    def test_parse_spot_valid(self):
        """Test parsing a valid PSKReporter spot."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        data = {
            "sq": 30142870791,
            "f": 21074653,
            "md": "FT8",
            "rp": -5,
            "t": 1662407712,
            "sc": "SP2EWQ",
            "sl": "JO93fn42",
            "rc": "CU3AT",
            "rl": "HM68jp36",
            "sa": 269,
            "ra": 149,
            "b": "15m",
        }
        spot = sub._parse_spot(data)
        assert spot is not None
        assert spot.sender_call == "SP2EWQ"
        assert spot.receiver_call == "CU3AT"
        assert spot.frequency == 21074653
        assert spot.mode == "FT8"
        assert spot.snr == -5
        assert spot.band == "15m"
        assert spot.sender_locator == "JO93fn42"
        assert spot.receiver_locator == "HM68jp36"
        assert spot.sender_country == 269
        assert spot.receiver_country == 149

    def test_parse_spot_missing_sender(self):
        """Test parsing spot with missing sender callsign."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        data = {"f": 14074000, "md": "FT8", "rc": "KH6RS"}
        spot = sub._parse_spot(data)
        assert spot is None

    def test_parse_spot_missing_receiver(self):
        """Test parsing spot with missing receiver callsign."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        data = {"f": 14074000, "md": "FT8", "sc": "WH6GXZ"}
        spot = sub._parse_spot(data)
        assert spot is None

    def test_parse_spot_invalid_frequency(self):
        """Test parsing spot with invalid frequency."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        data = {"f": 0, "md": "FT8", "sc": "WH6GXZ", "rc": "KH6RS"}
        spot = sub._parse_spot(data)
        assert spot is None

    def test_parse_spot_not_dict(self):
        """Test parsing non-dict data returns None."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        assert sub._parse_spot("not a dict") is None
        assert sub._parse_spot([1, 2, 3]) is None
        assert sub._parse_spot(None) is None

    def test_get_spots_empty(self):
        """Test getting spots when none collected."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        spots = sub.get_spots()
        assert spots == []

    def test_get_spots_with_data(self):
        """Test getting spots after adding some."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        spot1 = PSKSpot(
            frequency=14074000, mode="FT8", band="20m",
            sender_call="WH6GXZ", receiver_call="KH6RS",
        )
        spot2 = PSKSpot(
            frequency=7074000, mode="FT8", band="40m",
            sender_call="KH6RS", receiver_call="WH6GXZ",
        )
        sub._spots.append(spot1)
        sub._spots.append(spot2)

        spots = sub.get_spots()
        assert len(spots) == 2
        # Newest first
        assert spots[0].band == "40m"
        assert spots[1].band == "20m"

    def test_get_spots_band_filter(self):
        """Test filtering spots by band."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        sub._spots.append(PSKSpot(
            frequency=14074000, mode="FT8", band="20m",
            sender_call="A", receiver_call="B",
        ))
        sub._spots.append(PSKSpot(
            frequency=7074000, mode="FT8", band="40m",
            sender_call="C", receiver_call="D",
        ))

        spots = sub.get_spots(band="20m")
        assert len(spots) == 1
        assert spots[0].band == "20m"

    def test_get_spots_callsign_filter(self):
        """Test filtering spots by callsign."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        sub._spots.append(PSKSpot(
            frequency=14074000, mode="FT8", band="20m",
            sender_call="WH6GXZ", receiver_call="KH6RS",
        ))
        sub._spots.append(PSKSpot(
            frequency=7074000, mode="FT8", band="40m",
            sender_call="OTHER", receiver_call="ANOTHER",
        ))

        spots = sub.get_spots(callsign="WH6GXZ")
        assert len(spots) == 1
        assert spots[0].sender_call == "WH6GXZ"

    def test_get_spots_limit(self):
        """Test spot limit."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        for i in range(10):
            sub._spots.append(PSKSpot(
                frequency=14074000 + i, mode="FT8", band="20m",
                sender_call=f"CALL{i}", receiver_call="RX",
            ))

        spots = sub.get_spots(limit=3)
        assert len(spots) == 3

    def test_band_activity_tracking(self):
        """Test band activity updates from spots."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        spot = PSKSpot(
            frequency=14074000, mode="FT8", band="20m",
            sender_call="WH6GXZ", receiver_call="KH6RS", snr=-10,
        )
        sub._update_band_activity(spot)

        activity = sub.get_band_activity()
        assert "20m" in activity
        assert activity["20m"]["spot_count"] == 1
        assert activity["20m"]["unique_senders"] == 1
        assert activity["20m"]["avg_snr"] == -10.0

    def test_band_activity_multiple_spots(self):
        """Test band activity with multiple spots."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        for i in range(5):
            spot = PSKSpot(
                frequency=14074000, mode="FT8", band="20m",
                sender_call=f"CALL{i}", receiver_call="RX", snr=-10 + i,
            )
            sub._update_band_activity(spot)

        activity = sub.get_band_activity()
        assert activity["20m"]["spot_count"] == 5
        assert activity["20m"]["unique_senders"] == 5
        assert activity["20m"]["unique_receivers"] == 1

    def test_get_stats(self):
        """Test statistics retrieval."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        stats = sub.get_stats()
        assert stats["spots_received"] == 0
        assert stats["spots_rejected"] == 0
        assert stats["connected"] is False
        assert stats["spots_buffered"] == 0

    def test_get_propagation_data(self):
        """Test propagation data format for integration."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        # Add some spots
        spot = PSKSpot(
            frequency=14074000, mode="FT8", band="20m",
            sender_call="WH6GXZ", receiver_call="KH6RS",
        )
        sub._spots.append(spot)
        sub._update_band_activity(spot)

        data = sub.get_propagation_data()
        assert "pskreporter" in data
        pskr = data["pskreporter"]
        assert pskr["source"] == "PSKReporter (mqtt.pskreporter.info)"
        assert pskr["bands_active"] >= 1

    def test_register_spot_callback(self):
        """Test registering a spot callback."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        received = []

        def on_spot(spot):
            received.append(spot)

        sub.register_spot_callback(on_spot)
        assert len(sub._spot_callbacks) == 1


# =========================================================================
# Factory function tests
# =========================================================================


class TestFactory:
    """Tests for factory functions."""

    def test_create_pskreporter_subscriber_default(self):
        """Test creating subscriber with defaults."""
        sub = create_pskreporter_subscriber()
        assert sub._config["broker"] == PSKR_BROKER
        assert sub._config["port"] == PSKR_PORT
        assert sub._config["callsign"] == ""
        assert sub._config["bands"] == []
        assert sub._config["enabled"] is True

    def test_create_pskreporter_subscriber_callsign(self):
        """Test creating subscriber with callsign filter."""
        sub = create_pskreporter_subscriber(callsign="WH6GXZ")
        assert sub._config["callsign"] == "WH6GXZ"

    def test_create_pskreporter_subscriber_bands(self):
        """Test creating subscriber with band filter."""
        sub = create_pskreporter_subscriber(bands=["20m", "40m"])
        assert sub._config["bands"] == ["20m", "40m"]

    def test_create_pskreporter_subscriber_tls(self):
        """Test creating subscriber with TLS."""
        sub = create_pskreporter_subscriber(use_tls=True)
        assert sub._config["use_tls"] is True
        assert sub._config["port"] == PSKR_PORT_TLS


# =========================================================================
# Propagation integration tests
# =========================================================================


class TestPropagationIntegration:
    """Tests for PSKReporter integration with propagation module."""

    def test_datasource_pskreporter_exists(self):
        """Test that PSKReporter DataSource enum exists."""
        from commands.propagation import DataSource
        assert DataSource.PSKREPORTER.value == "pskreporter"

    def test_pskreporter_in_sources(self):
        """Test PSKReporter is in default source configuration."""
        from commands.propagation import _sources, DataSource
        assert DataSource.PSKREPORTER in _sources

    def test_configure_pskreporter(self):
        """Test configuring PSKReporter source."""
        from commands.propagation import configure_source, DataSource
        result = configure_source(
            DataSource.PSKREPORTER,
            enabled=True,
            callsign="WH6GXZ",
            bands=["20m"],
        )
        assert result.success
        assert result.data['source'] == 'pskreporter'
        assert result.data['callsign'] == 'WH6GXZ'
        assert result.data['bands'] == ['20m']

        # Clean up
        configure_source(DataSource.PSKREPORTER, enabled=False)

    def test_check_source_pskreporter_not_configured(self):
        """Test checking unconfigured PSKReporter returns failure."""
        from commands.propagation import check_source, DataSource, configure_source
        # Ensure disabled
        configure_source(DataSource.PSKREPORTER, enabled=False)
        result = check_source(DataSource.PSKREPORTER)
        assert not result.success
        assert 'not configured' in result.message.lower()


# =========================================================================
# MQTT message handler tests (without real broker)
# =========================================================================


class TestMessageHandling:
    """Tests for MQTT message processing without a real broker."""

    def test_on_message_valid_spot(self):
        """Test processing a valid MQTT message."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        payload = json.dumps({
            "sq": 1234,
            "f": 14074000,
            "md": "FT8",
            "rp": -12,
            "t": 1700000000,
            "sc": "WH6GXZ",
            "sl": "BL11",
            "rc": "KH6RS",
            "rl": "BL01",
            "sa": 110,
            "ra": 110,
            "b": "20m",
        }).encode("utf-8")

        msg = MagicMock()
        msg.payload = payload

        sub._on_message(None, None, msg)

        assert len(sub._spots) == 1
        assert sub._spots[0].sender_call == "WH6GXZ"
        assert sub._stats["spots_received"] == 1

    def test_on_message_invalid_json(self):
        """Test processing invalid JSON message."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        msg = MagicMock()
        msg.payload = b"not json"

        sub._on_message(None, None, msg)

        assert len(sub._spots) == 0
        assert sub._stats["spots_rejected"] == 1

    def test_on_message_oversized_payload(self):
        """Test rejection of oversized payloads."""
        sub = PSKReporterSubscriber(config={"broker": "test"})

        msg = MagicMock()
        msg.payload = b"x" * 20000  # Over MAX_PAYLOAD_BYTES

        sub._on_message(None, None, msg)

        assert len(sub._spots) == 0
        assert sub._stats["spots_rejected"] == 1

    def test_on_message_callback_invoked(self):
        """Test that spot callbacks are invoked."""
        sub = PSKReporterSubscriber(config={"broker": "test"})
        received = []

        sub.register_spot_callback(lambda spot: received.append(spot))

        payload = json.dumps({
            "sq": 1, "f": 14074000, "md": "FT8", "rp": 0,
            "sc": "TEST1", "rc": "TEST2", "b": "20m",
        }).encode("utf-8")

        msg = MagicMock()
        msg.payload = payload

        sub._on_message(None, None, msg)

        assert len(received) == 1
        assert received[0].sender_call == "TEST1"
