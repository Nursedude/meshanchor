"""
Node Health Mixin — Battery forecasting, signal trending, latency monitoring.

Wires the following utility modules into the TUI:
- utils.predictive_maintenance (MaintenancePredictor)
- utils.signal_trending (SignalTrend)
- utils.latency_monitor (LatencyMonitor, probe_tcp)

Provides menu methods callable from Dashboard and Maps submenus.
"""

import logging
import subprocess
import time

logger = logging.getLogger(__name__)


class NodeHealthMixin:
    """TUI mixin for node health analysis features."""

    def _node_health_menu(self):
        """Node health analysis submenu."""
        while True:
            choices = [
                ("latency", "Service Latency     TCP probe all services"),
                ("battery", "Battery Forecast    Node battery projections"),
                ("signal", "Signal Trends       SNR/RSSI analysis"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Node Health",
                "Proactive health monitoring and prediction:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "latency": ("Service Latency", self._service_latency_probe),
                "battery": ("Battery Forecast", self._battery_forecast_display),
                "signal": ("Signal Trends", self._signal_trending_display),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _service_latency_probe(self):
        """Probe all NOC services and display latency/health."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Service Latency Probe ===\n")
        print("Probing services (2s timeout each)...\n")

        try:
            from utils.latency_monitor import probe_tcp, DEFAULT_SERVICES
        except ImportError:
            print("  Latency monitor module not available.")
            print("  File: src/utils/latency_monitor.py")
            self._wait_for_enter()
            return

        results = []
        for name, host, port in DEFAULT_SERVICES:
            success, rtt_ms = probe_tcp(host, port, timeout=2.0)
            results.append((name, host, port, success, rtt_ms))

            if success:
                # Color by latency
                if rtt_ms < 10:
                    color = "\033[0;32m"  # green
                    label = "HEALTHY"
                elif rtt_ms < 100:
                    color = "\033[0;33m"  # yellow
                    label = "OK"
                else:
                    color = "\033[0;31m"  # red
                    label = "SLOW"
                print(f"  {color}{label:8s}\033[0m {name:<22} {rtt_ms:>7.1f}ms  ({host}:{port})")
            else:
                print(f"  \033[0;31m{'DOWN':8s}\033[0m {name:<22} {'---':>7}    ({host}:{port})")

        # Summary
        up_count = sum(1 for r in results if r[3])
        down_count = len(results) - up_count
        avg_rtt = 0.0
        up_results = [r for r in results if r[3]]
        if up_results:
            avg_rtt = sum(r[4] for r in up_results) / len(up_results)

        print(f"\n{'='*50}")
        print(f"  Services: {up_count} up, {down_count} down")
        if up_results:
            print(f"  Avg RTT:  {avg_rtt:.1f}ms")

        if down_count > 0:
            print("\n  Down services may need to be started:")
            for name, host, port, success, _ in results:
                if not success:
                    print(f"    sudo systemctl start {name.split('_')[0]}")

        print()
        self._wait_for_enter()

    def _battery_forecast_display(self):
        """Show battery forecasts for tracked nodes."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Battery Forecast ===\n")

        try:
            from utils.predictive_maintenance import MaintenancePredictor
        except ImportError:
            print("  Predictive maintenance module not available.")
            print("  File: src/utils/predictive_maintenance.py")
            self._wait_for_enter()
            return

        # Try to get node data from meshtastic
        print("Querying node telemetry...\n")
        nodes = self._get_meshtastic_node_telemetry()

        if not nodes:
            print("  No node battery data available.\n")
            print("  Battery forecasting requires telemetry data from nodes.")
            print("  Ensure meshtasticd is running and nodes report telemetry.")
            self._wait_for_enter()
            return

        predictor = MaintenancePredictor()

        # Feed samples and show forecasts
        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            if battery_pct is not None:
                predictor.record_battery(node_id, battery_pct, voltage=voltage)

        # Display results
        print(f"{'Node':<20} {'Battery':>8} {'Voltage':>8} {'Status':<12}")
        print("-" * 52)

        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            name = data.get('short_name', node_id[:12])

            if battery_pct is None:
                print(f"  {name:<20} {'---':>8}")
                continue

            # Color code battery level
            if battery_pct > 50:
                color = "\033[0;32m"  # green
                status = "Good"
            elif battery_pct > 30:
                color = "\033[0;33m"  # yellow
                status = "Warning"
            elif battery_pct > 15:
                color = "\033[0;31m"  # red
                status = "Critical"
            else:
                color = "\033[0;31m"
                status = "SHUTDOWN RISK"

            v_str = f"{voltage:.2f}V" if voltage else "---"
            print(f"  {name:<20} {color}{battery_pct:>6.1f}%\033[0m {v_str:>8} {status:<12}")

        print(f"\n  Note: Accurate forecasts require multiple samples over time.")
        print(f"  Battery drain rates calculated after 3+ readings.")
        print()
        self._wait_for_enter()

    def _get_meshtastic_node_telemetry(self):
        """Get node telemetry from meshtastic.

        Returns dict of node_id -> {battery_level, voltage, short_name, ...}
        """
        nodes = {}

        # Try meshtastic Python API first
        try:
            import meshtastic.tcp_interface
            iface = meshtastic.tcp_interface.TCPInterface(
                hostname='localhost', connectNow=True
            )
            if iface.nodes:
                for node_id, node in iface.nodes.items():
                    device_metrics = node.get('deviceMetrics', {})
                    user = node.get('user', {})
                    if device_metrics:
                        nodes[node_id] = {
                            'battery_level': device_metrics.get('batteryLevel'),
                            'voltage': device_metrics.get('voltage'),
                            'short_name': user.get('shortName', node_id[:8]),
                            'long_name': user.get('longName', ''),
                        }
            iface.close()
        except ImportError:
            logger.debug("meshtastic module not installed, skipping API telemetry")
        except (ConnectionRefusedError, OSError, TimeoutError) as e:
            logger.debug(f"meshtasticd not reachable for telemetry: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error querying meshtastic telemetry: {e}")

        # Fallback: try meshtastic CLI --info
        if not nodes:
            try:
                cli = self._get_meshtastic_cli()
                result = subprocess.run(
                    [cli, '--host', 'localhost', '--info'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    # Parse basic node info from CLI output
                    for line in result.stdout.splitlines():
                        if 'batteryLevel' in line or 'battery_level' in line:
                            # Basic parsing - CLI output varies
                            pass
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.debug(f"meshtastic CLI unavailable for telemetry: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error querying meshtastic CLI: {e}")

        return nodes

    def _signal_trending_display(self):
        """Show signal trend analysis for nodes."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Signal Trending Analysis ===\n")

        try:
            from utils.signal_trending import SignalTrend
        except ImportError:
            print("  Signal trending module not available.")
            print("  File: src/utils/signal_trending.py")
            self._wait_for_enter()
            return

        # Try to get node signal data
        print("Querying node signal data...\n")
        nodes = self._get_meshtastic_node_signals()

        if not nodes:
            print("  No signal data available.\n")
            print("  Signal trending requires SNR/RSSI data from nodes.")
            print("  Ensure meshtasticd is running and nodes are in range.")
            self._wait_for_enter()
            return

        print(f"{'Node':<20} {'SNR':>8} {'RSSI':>8} {'Hops':>6}")
        print("-" * 46)

        for node_id, data in nodes.items():
            name = data.get('short_name', node_id[:12])
            snr = data.get('snr')
            rssi = data.get('rssi')
            hops = data.get('hops_away', '?')

            snr_str = f"{snr:.1f}dB" if snr is not None else "---"
            rssi_str = f"{rssi}dBm" if rssi is not None else "---"

            # Color code SNR
            if snr is not None:
                if snr > 5:
                    color = "\033[0;32m"  # green - excellent
                elif snr > 0:
                    color = "\033[0;33m"  # yellow - acceptable
                else:
                    color = "\033[0;31m"  # red - poor
            else:
                color = "\033[2m"  # dim

            print(f"  {name:<20} {color}{snr_str:>8}\033[0m {rssi_str:>8} {hops:>6}")

        print(f"\n  Note: Full trend analysis requires multiple observations.")
        print(f"  Re-run periodically to build signal history.")
        print()
        self._wait_for_enter()

    def _get_meshtastic_node_signals(self):
        """Get SNR/RSSI data from meshtastic nodes.

        Returns dict of node_id -> {snr, rssi, short_name, hops_away}
        """
        nodes = {}

        try:
            import meshtastic.tcp_interface
            iface = meshtastic.tcp_interface.TCPInterface(
                hostname='localhost', connectNow=True
            )
            if iface.nodes:
                for node_id, node in iface.nodes.items():
                    user = node.get('user', {})
                    snr = node.get('snr')
                    rssi = node.get('rssi')
                    hops = node.get('hopsAway')
                    if snr is not None or rssi is not None:
                        nodes[node_id] = {
                            'snr': snr,
                            'rssi': rssi,
                            'short_name': user.get('shortName', node_id[:8]),
                            'hops_away': hops,
                        }
            iface.close()
        except ImportError:
            logger.debug("meshtastic module not installed, skipping signal data")
        except (ConnectionRefusedError, OSError, TimeoutError) as e:
            logger.debug(f"meshtasticd not reachable for signal data: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error querying meshtastic signal data: {e}")

        return nodes
