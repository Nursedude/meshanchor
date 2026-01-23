"""
Quick Actions Mixin — single-key shortcuts for common NOC operations.

Provides a fast-access menu where each entry maps to a common
operation, avoiding deep menu navigation for frequent tasks.

Quick Actions:
    s - Service status (all services at a glance)
    n - Node list (meshtastic --nodes)
    l - Follow logs (journalctl meshtasticd)
    r - Restart meshtasticd
    R - Restart rnsd
    p - Port/network check
    g - Generate status report
    d - Run diagnostics
"""

import sys
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Quick action definitions: (tag, description, method_name)
QUICK_ACTIONS = [
    ('s', 'Service status overview', '_qa_service_status'),
    ('n', 'Node list (meshtastic --nodes)', '_qa_node_list'),
    ('l', 'Follow logs (meshtasticd)', '_qa_follow_logs'),
    ('r', 'Restart meshtasticd', '_qa_restart_meshtasticd'),
    ('R', 'Restart rnsd', '_qa_restart_rnsd'),
    ('p', 'Port / network check', '_qa_port_check'),
    ('g', 'Generate status report', '_qa_generate_report'),
    ('d', 'Run diagnostics', '_qa_run_diagnostics'),
]


class QuickActionsMixin:
    """Single-key shortcuts for frequent NOC operations."""

    def _quick_actions_menu(self):
        """Display quick actions menu with single-key shortcuts."""
        while True:
            choices = [(tag, desc) for tag, desc, _ in QUICK_ACTIONS]
            choices.append(('b', 'Back to main menu'))

            choice = self.dialog.menu(
                "Quick Actions",
                "Single-key shortcuts (press letter to select):",
                choices
            )

            if choice is None or choice == 'b':
                break

            # Find and execute the matching action
            for tag, _, method_name in QUICK_ACTIONS:
                if choice == tag:
                    method = getattr(self, method_name, None)
                    if method:
                        method()
                    break

    def _qa_service_status(self):
        """Quick: show all service statuses."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Quick Service Status ===\n")

        services = ['meshtasticd', 'rnsd', 'mosquitto', 'meshforge']
        for svc in services:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', svc],
                    capture_output=True, text=True, timeout=5
                )
                status = result.stdout.strip()
                if status == 'active':
                    print(f"  * {svc:<18} running")
                elif status == 'failed':
                    print(f"  ! {svc:<18} FAILED")
                else:
                    print(f"  - {svc:<18} {status}")
            except Exception:
                print(f"  ? {svc:<18} unknown")

        # Bridge process
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'rns_bridge'],
                capture_output=True, timeout=3
            )
            bridge_status = "running" if result.returncode == 0 else "not running"
            sym = "*" if result.returncode == 0 else "-"
            print(f"  {sym} {'rns_bridge':<18} {bridge_status}")
        except Exception:
            print(f"  ? {'rns_bridge':<18} unknown")

        print()
        input("Press Enter to continue...")

    def _qa_node_list(self):
        """Quick: show meshtastic node list."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Node List ===\n")
        try:
            subprocess.run(
                ['meshtastic', '--nodes'],
                timeout=30
            )
        except FileNotFoundError:
            print("Error: 'meshtastic' CLI not installed.")
            print("Install with: pip install meshtastic")
        except subprocess.TimeoutExpired:
            print("Error: Command timed out. Is meshtasticd running?")
        except Exception as e:
            print(f"Error: {e}")
        print()
        input("Press Enter to continue...")

    def _qa_follow_logs(self):
        """Quick: follow meshtasticd journal logs."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== meshtasticd Logs (Ctrl+C to stop, auto-exits after 2 min) ===\n")
        try:
            subprocess.run(
                ['journalctl', '-fu', 'meshtasticd', '--no-pager', '-n', '50'],
                timeout=120
            )
        except subprocess.TimeoutExpired:
            pass  # Normal exit after timeout
        except KeyboardInterrupt:
            pass  # User pressed Ctrl+C
        except Exception as e:
            print(f"Error: {e}")
            input("Press Enter to continue...")

    def _qa_restart_meshtasticd(self):
        """Quick: restart meshtasticd service."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("Restarting meshtasticd...\n")
        try:
            subprocess.run(
                ['systemctl', 'restart', 'meshtasticd'],
                timeout=30
            )
            subprocess.run(
                ['systemctl', 'status', 'meshtasticd', '--no-pager', '-l'],
                timeout=10
            )
        except Exception as e:
            print(f"Error: {e}")

        # Invalidate status bar cache
        if hasattr(self, '_status_bar') and self._status_bar:
            self._status_bar.invalidate()

        print()
        input("Press Enter to continue...")

    def _qa_restart_rnsd(self):
        """Quick: restart rnsd service."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("Restarting rnsd...\n")
        try:
            subprocess.run(
                ['systemctl', 'restart', 'rnsd'],
                timeout=30
            )
            subprocess.run(
                ['systemctl', 'status', 'rnsd', '--no-pager', '-l'],
                timeout=10
            )
        except Exception as e:
            print(f"Error: {e}")

        # Invalidate status bar cache
        if hasattr(self, '_status_bar') and self._status_bar:
            self._status_bar.invalidate()

        print()
        input("Press Enter to continue...")

    def _qa_port_check(self):
        """Quick: check network ports."""
        import socket as sock
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Port Check ===\n")

        ports = [
            (4403, 'meshtasticd TCP API'),
            (9443, 'meshtasticd Web Client'),
            (37428, 'rnsd (RNS shared instance)'),
            (1883, 'MQTT broker'),
        ]

        for port, desc in ports:
            try:
                with sock.socket(sock.AF_INET, sock.SOCK_STREAM) as s:
                    s.settimeout(1)
                    result = s.connect_ex(('127.0.0.1', port))
                    if result == 0:
                        print(f"  * {port:<6} {desc}")
                    else:
                        print(f"  - {port:<6} {desc} (not listening)")
            except Exception:
                print(f"  ? {port:<6} {desc} (check failed)")

        print()
        input("Press Enter to continue...")

    def _qa_generate_report(self):
        """Quick: generate and display a status report."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("Generating status report...\n")

        try:
            from utils.report_generator import generate_report
            report = generate_report()
            # Print report, paginated
            lines = report.split('\n')
            for i, line in enumerate(lines):
                print(line)
                # Pause every 40 lines
                if (i + 1) % 40 == 0 and i + 1 < len(lines):
                    resp = input("\n--- More (Enter=continue, q=quit) ---\n")
                    if resp.strip().lower() == 'q':
                        break
        except ImportError:
            print("Error: Report generator module not available.")
        except Exception as e:
            print(f"Error generating report: {e}")

        print()
        input("Press Enter to continue...")

    def _qa_run_diagnostics(self):
        """Quick: run diagnostic engine health check."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== Diagnostic Health Check ===\n")

        try:
            from utils.diagnostic_engine import get_diagnostic_engine
            engine = get_diagnostic_engine()
            summary = engine.get_health_summary()

            print(f"  Overall Health:    {summary['overall_health']}")
            print(f"  Symptoms (1h):     {summary['symptoms_last_hour']}")
            print(f"  Total Diagnoses:   {summary['stats'].get('diagnoses_made', 0)}")
            print(f"  Auto-Recoveries:   {summary['stats'].get('auto_recoveries', 0)}")
            print(f"  Rules Loaded:      {summary['stats'].get('rules_loaded', 0)}")

            # Recent issues
            recent = engine.get_recent_diagnoses(limit=5)
            if recent:
                print(f"\n  Recent Issues ({len(recent)}):")
                for d in recent[-5:]:
                    cat = d.symptom.category.value
                    print(f"    [{cat}] {d.likely_cause[:60]}")
            else:
                print("\n  No recent issues detected.")

        except ImportError:
            print("Error: Diagnostic engine not available.")
        except Exception as e:
            print(f"Error: {e}")

        print()
        input("Press Enter to continue...")
