"""Tests for the network latency monitor."""

import time
from unittest.mock import patch, MagicMock

from src.utils.latency_monitor import (
    LatencyMonitor, ServiceHealth, LatencySample, probe_tcp,
)


class TestServiceHealth:
    """Test ServiceHealth metrics calculations."""

    def test_empty_samples_unknown(self):
        """No samples should show UNKNOWN status."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        assert svc.status == "UNKNOWN"
        assert svc.packet_loss_pct == 100.0
        assert svc.avg_rtt_ms == 0.0

    def test_healthy_service(self):
        """All successful, low RTT should be HEALTHY."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        for _ in range(10):
            svc.samples.append(LatencySample(
                timestamp=time.time(), rtt_ms=5.0, success=True
            ))
        assert svc.status == "HEALTHY"
        assert svc.is_reachable
        assert svc.avg_rtt_ms == 5.0
        assert svc.jitter_ms == 0.0
        assert svc.packet_loss_pct == 0.0

    def test_down_service(self):
        """Last probe failed should be DOWN."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        svc.samples.append(LatencySample(
            timestamp=time.time(), rtt_ms=2000.0, success=False
        ))
        assert svc.status == "DOWN"
        assert not svc.is_reachable

    def test_degraded_high_jitter(self):
        """High jitter should be DEGRADED."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        # Alternating fast/slow creates high jitter
        for i in range(20):
            rtt = 10.0 if i % 2 == 0 else 200.0
            svc.samples.append(LatencySample(
                timestamp=time.time(), rtt_ms=rtt, success=True
            ))
        assert svc.status == "DEGRADED"
        assert svc.jitter_ms > 50

    def test_degraded_packet_loss(self):
        """High packet loss should be DEGRADED (last probe succeeds)."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        # 2 failures out of 10 = 20% loss, but last probe succeeds
        pattern = [True, True, False, True, True, False, True, True, True, True]
        for success in pattern:
            svc.samples.append(LatencySample(
                timestamp=time.time(), rtt_ms=5.0, success=success,
            ))
        assert svc.is_reachable  # Last probe succeeded
        assert svc.packet_loss_pct == 20.0
        assert svc.status == "DEGRADED"

    def test_summary_dict(self):
        """summary() should return proper dict."""
        svc = ServiceHealth(name='meshtasticd', host='localhost', port=4403)
        svc.samples.append(LatencySample(
            timestamp=time.time(), rtt_ms=3.5, success=True
        ))
        s = svc.summary()
        assert s['name'] == 'meshtasticd'
        assert s['port'] == 4403
        assert s['status'] == 'HEALTHY'
        assert s['avg_rtt_ms'] == 3.5


class TestLatencyMonitor:
    """Test LatencyMonitor probe and lifecycle."""

    def test_probe_once_populates_samples(self):
        """probe_once() should add samples to all services."""
        monitor = LatencyMonitor(
            services=[('test_svc', 'localhost', 65531)],
            interval_sec=60,
        )
        # Port 65531 won't be open, so probe will fail
        monitor.probe_once()
        health = monitor.get_health()
        assert 'test_svc' in health
        assert len(health['test_svc'].samples) == 1
        assert not health['test_svc'].is_reachable

    def test_get_summary_returns_list(self):
        """get_summary() should return list of dicts."""
        monitor = LatencyMonitor(
            services=[('svc1', 'localhost', 65532)],
        )
        monitor.probe_once()
        summary = monitor.get_summary()
        assert isinstance(summary, list)
        assert len(summary) == 1
        assert summary[0]['name'] == 'svc1'

    def test_get_degraded(self):
        """get_degraded() should return DOWN/DEGRADED services."""
        monitor = LatencyMonitor(
            services=[('down_svc', 'localhost', 65533)],
        )
        monitor.probe_once()
        degraded = monitor.get_degraded()
        assert 'down_svc' in degraded

    def test_start_stop(self):
        """start()/stop() should manage background thread."""
        monitor = LatencyMonitor(
            services=[('test', 'localhost', 65534)],
            interval_sec=0.1,
        )
        monitor.start()
        assert monitor._running
        assert monitor._thread is not None
        time.sleep(0.3)
        monitor.stop()
        assert not monitor._running

    def test_deque_max_length(self):
        """Samples deque should respect maxlen=120."""
        svc = ServiceHealth(name='test', host='localhost', port=1234)
        for i in range(200):
            svc.samples.append(LatencySample(
                timestamp=time.time(), rtt_ms=1.0, success=True
            ))
        assert len(svc.samples) == 120


class TestProbeTcp:
    """Test the probe_tcp function."""

    def test_unreachable_port(self):
        """Unreachable port should return (False, rtt)."""
        success, rtt = probe_tcp('localhost', 65535, timeout=0.5)
        assert not success
        assert rtt > 0

    @patch('socket.socket')
    def test_successful_connection(self, mock_socket_class):
        """Successful connect should return (True, rtt)."""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_sock.connect.return_value = None
        success, rtt = probe_tcp('localhost', 4403, timeout=1.0)
        assert success
        mock_sock.close.assert_called_once()
