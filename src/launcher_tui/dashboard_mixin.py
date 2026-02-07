"""
Dashboard Mixin — Service status, node counts, data path diagnostics, alerts.

Extracted from main.py to keep file size under 1,500 lines.
Provides the display methods used by the Dashboard submenu.
"""

import subprocess


class DashboardMixin:
    """TUI mixin for dashboard display methods."""

    def _service_status_display(self):
        """Show comprehensive service status."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Service Status ===\n")

        # Import here to avoid circular imports at module level
        try:
            from startup_checks import ServiceRunState
        except ImportError:
            ServiceRunState = None

        if self._env_state and ServiceRunState:
            for name, info in self._env_state.services.items():
                if info.state == ServiceRunState.RUNNING:
                    print(f"  \033[0;32m●\033[0m {name:<18} running")
                elif info.state == ServiceRunState.FAILED:
                    print(f"  \033[0;31m●\033[0m {name:<18} FAILED")
                else:
                    print(f"  \033[2m○\033[0m {name:<18} stopped")
        else:
            # Fallback to systemctl
            for svc in ['meshtasticd', 'rnsd', 'mosquitto']:
                try:
                    result = subprocess.run(
                        ['systemctl', 'is-active', svc],
                        capture_output=True, text=True, timeout=5
                    )
                    status = result.stdout.strip()
                    if status == 'active':
                        print(f"  \033[0;32m●\033[0m {svc:<18} running")
                    else:
                        print(f"  \033[2m○\033[0m {svc:<18} {status}")
                except Exception:
                    print(f"  ? {svc:<18} unknown")

        print()
        self._wait_for_enter()

    def _show_node_counts(self):
        """Show node counts from all sources."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Node Counts ===\n")

        # Meshtastic nodes
        try:
            cli = self._get_meshtastic_cli()
            result = subprocess.run(
                [cli, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=30
            )
            # Count nodes in output
            node_count = result.stdout.count('Node ')
            print(f"  Meshtastic nodes: {node_count}")
        except Exception as e:
            print(f"  Meshtastic: unavailable ({e})")

        # RNS destinations
        try:
            result = subprocess.run(
                ['rnstatus', '-a'],
                capture_output=True, text=True, timeout=10
            )
            # Count lines that look like destinations
            dest_count = len([line for line in result.stdout.splitlines()
                             if line.strip().startswith('<')])
            print(f"  RNS destinations: {dest_count}")
        except Exception:
            print("  RNS: unavailable")

        print()
        self._wait_for_enter()

    def _data_path_diagnostic(self):
        """Test all data collection paths to diagnose zero-data issues."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Data Path Diagnostic ===\n")
        print("Testing all data sources...\n")

        results = []

        # Test 1: meshtasticd TCP connection
        print("[1/6] Testing meshtasticd TCP (port 4403)...")
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            if result == 0:
                results.append(("meshtasticd TCP", "OK", "Port 4403 accepting connections"))
                print("      \033[0;32mOK\033[0m - Port 4403 reachable")
            else:
                results.append(("meshtasticd TCP", "FAIL", f"Connection refused (code {result})"))
                print("      \033[0;31mFAIL\033[0m - Connection refused")
        except Exception as e:
            results.append(("meshtasticd TCP", "FAIL", str(e)))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 2: meshtastic CLI node count
        print("[2/6] Testing meshtastic CLI...")
        try:
            result = subprocess.run(
                ['meshtastic', '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                node_lines = [line for line in result.stdout.split('\n')
                             if 'Node' in line or '!' in line]
                results.append(("meshtastic CLI", "OK", f"Responded, ~{len(node_lines)} node refs"))
                print("      \033[0;32mOK\033[0m - CLI responded")
            else:
                results.append(("meshtastic CLI", "WARN",
                               result.stderr[:50] if result.stderr else "No output"))
                print("      \033[0;33mWARN\033[0m - Non-zero exit")
        except FileNotFoundError:
            results.append(("meshtastic CLI", "SKIP", "CLI not installed"))
            print("      \033[0;33mSKIP\033[0m - CLI not found")
        except subprocess.TimeoutExpired:
            results.append(("meshtastic CLI", "FAIL", "Timeout after 15s"))
            print("      \033[0;31mFAIL\033[0m - Timeout")
        except Exception as e:
            results.append(("meshtastic CLI", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 3: meshtastic Python API
        print("[3/6] Testing meshtastic Python API...")
        try:
            import meshtastic.tcp_interface
            iface = meshtastic.tcp_interface.TCPInterface(hostname='localhost', connectNow=True)
            node_count = len(iface.nodes) if iface.nodes else 0
            iface.close()
            results.append(("meshtastic API", "OK", f"{node_count} nodes in nodeDB"))
            print(f"      \033[0;32mOK\033[0m - {node_count} nodes found")
        except ImportError:
            results.append(("meshtastic API", "SKIP", "meshtastic module not installed"))
            print("      \033[0;33mSKIP\033[0m - Module not installed")
        except Exception as e:
            err_msg = str(e)[:50]
            results.append(("meshtastic API", "FAIL", err_msg))
            print(f"      \033[0;31mFAIL\033[0m - {err_msg}")

        # Test 4: pubsub availability
        print("[4/6] Testing pubsub (for live capture)...")
        try:
            from pubsub import pub
            listeners = pub.getDefaultTopicMgr().getTopic('meshtastic.receive', okIfNone=True)
            if listeners:
                count = len(list(listeners.getListeners()))
                results.append(("pubsub", "OK", f"{count} listener(s) on meshtastic.receive"))
                print(f"      \033[0;32mOK\033[0m - {count} listener(s) registered")
            else:
                results.append(("pubsub", "WARN", "Topic exists but no listeners"))
                print("      \033[0;33mWARN\033[0m - No listeners registered")
        except ImportError:
            results.append(("pubsub", "SKIP", "pubsub module not installed"))
            print("      \033[0;33mSKIP\033[0m - Module not installed")
        except Exception as e:
            results.append(("pubsub", "WARN", str(e)[:50]))
            print(f"      \033[0;33mWARN\033[0m - {e}")

        # Test 5: MapDataCollector
        print("[5/6] Testing MapDataCollector...")
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector(enable_history=False)
            geojson = collector.collect(max_age_seconds=30)
            props = geojson.get('properties', {})
            total = props.get('total_nodes', 0)
            with_gps = props.get('nodes_with_position', 0)
            sources = props.get('sources', {})
            active_sources = [k for k, v in sources.items() if v > 0]
            if total > 0:
                results.append(("MapDataCollector", "OK", f"{total} nodes ({with_gps} with GPS)"))
                print(f"      \033[0;32mOK\033[0m - {total} nodes, sources: {active_sources}")
            else:
                results.append(("MapDataCollector", "WARN", "0 nodes returned"))
                print("      \033[0;33mWARN\033[0m - 0 nodes (check meshtasticd connection)")
        except ImportError:
            results.append(("MapDataCollector", "SKIP", "Module not available"))
            print("      \033[0;33mSKIP\033[0m - Module not available")
        except Exception as e:
            results.append(("MapDataCollector", "FAIL", str(e)[:50]))
            print(f"      \033[0;31mFAIL\033[0m - {e}")

        # Test 6: RNS path table
        print("[6/6] Testing RNS path table...")
        try:
            result = subprocess.run(
                ['rnpath', '-t'],
                capture_output=True, text=True, timeout=10
            )
            lines = [line for line in result.stdout.splitlines()
                     if line.strip() and not line.startswith('Path')]
            path_count = len(lines)
            if path_count > 0:
                results.append(("RNS paths", "OK", f"{path_count} known paths"))
                print(f"      \033[0;32mOK\033[0m - {path_count} paths in table")
            else:
                results.append(("RNS paths", "WARN", "Path table empty"))
                print("      \033[0;33mWARN\033[0m - No paths (normal if no RNS traffic yet)")
        except FileNotFoundError:
            results.append(("RNS paths", "SKIP", "rnpath not installed"))
            print("      \033[0;33mSKIP\033[0m - rnpath not found")
        except Exception as e:
            results.append(("RNS paths", "WARN", str(e)[:50]))
            print(f"      \033[0;33mWARN\033[0m - {e}")

        # Summary
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        ok_count = len([r for r in results if r[1] == "OK"])
        fail_count = len([r for r in results if r[1] == "FAIL"])
        warn_count = len([r for r in results if r[1] == "WARN"])

        for test, status, detail in results:
            if status == "OK":
                print(f"  \033[0;32m✓\033[0m {test:<20} {detail}")
            elif status == "FAIL":
                print(f"  \033[0;31m✗\033[0m {test:<20} {detail}")
            elif status == "WARN":
                print(f"  \033[0;33m!\033[0m {test:<20} {detail}")
            else:
                print(f"  \033[2m-\033[0m {test:<20} {detail}")

        print()
        if fail_count > 0:
            print(f"Result: {fail_count} FAILED - check service connections")
        elif warn_count > 0 and ok_count == 0:
            print("Result: No data sources working - check meshtasticd")
        elif ok_count > 0:
            print(f"Result: {ok_count} sources OK - data should be flowing")
        print()
        self._wait_for_enter()

    def _show_alerts(self):
        """Show current alerts from environment state."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Current Alerts ===\n")

        if self._env_state:
            alerts = self._env_state.get_alerts()
            if alerts:
                for alert in alerts:
                    print(f"  \033[0;33m!\033[0m {alert}")
            else:
                print("  No alerts - system healthy")
        else:
            print("  Environment state not available")

        print()
        self._wait_for_enter()
