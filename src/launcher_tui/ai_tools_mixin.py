"""
AI Tools Mixin for MeshForge Launcher TUI.

Provides AI-powered diagnostics, knowledge base queries, and coverage map
generation for the TUI launcher.

Features:
- Intelligent Diagnostics (rule-based symptom analysis)
- Knowledge Base (mesh networking concepts and troubleshooting)
- Claude Assistant (PRO mode with API key)
- Coverage Map Generation (opens in browser)
"""

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional


class AIToolsMixin:
    """Mixin providing AI tools for the TUI launcher."""

    def _ai_tools_menu(self):
        """Maps and coverage tools menu."""
        choices = [
            ("livemap", "Live Network Map"),
            ("coverage", "Generate Coverage Map (All Sources)"),
            ("diagnose", "Intelligent Diagnostics"),
            ("knowledge", "Knowledge Base Query"),
            ("assistant", "Claude Assistant"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Maps & Coverage",
                "Network mapping and analysis tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "livemap":
                self._open_live_map()
            elif choice == "diagnose":
                self._intelligent_diagnostics()
            elif choice == "knowledge":
                self._knowledge_base_query()
            elif choice == "assistant":
                self._claude_assistant()
            elif choice == "coverage":
                self._generate_coverage_map()

    def _maybe_auto_start_map(self):
        """Start map server on TUI launch if user has enabled auto-open."""
        import json
        import socket

        settings_file = self._get_map_settings_file()
        if not settings_file.exists():
            return

        try:
            with open(settings_file) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        if not settings.get("auto_open_map", False):
            return

        # Check if server already running
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', 5000))
            sock.close()
            if result == 0:
                return  # Already running
        except OSError:
            pass

        # Start map server in background (no dialog, quiet)
        try:
            from utils.map_data_service import MapServer
            server = MapServer(port=5000)
            server.start_background()
            # Store reference to prevent garbage collection
            self._map_server = server
        except Exception:
            pass  # Non-fatal, don't interrupt startup

    def _get_map_settings_file(self) -> Path:
        """Get the map settings file path."""
        from utils.paths import get_real_user_home
        config_dir = get_real_user_home() / ".config" / "meshforge"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "map_settings.json"

    def _open_live_map(self):
        """Open the live network map with real node data."""
        import json
        import socket

        # Check current auto-open setting
        auto_enabled = False
        settings_file = self._get_map_settings_file()
        if settings_file.exists():
            try:
                with open(settings_file) as f:
                    auto_enabled = json.load(f).get("auto_open_map", False)
            except (json.JSONDecodeError, OSError):
                pass

        auto_label = "ON" if auto_enabled else "OFF"
        choices = [
            ("browser", "Open map in browser (snapshot)"),
            ("server", "Start map server (live updates)"),
            ("autostart", f"Auto-open on launch [{auto_label}]"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Live Network Map",
            "Select map mode:",
            choices
        )

        if choice is None or choice == "back":
            return

        if choice == "server":
            self._start_map_server()
            return

        if choice == "autostart":
            self._toggle_auto_map()
            return

        # Browser mode: collect data, inject into HTML, open
        self.dialog.infobox("Loading", "Collecting node data from all sources...")

        try:
            from utils.map_data_service import MapDataCollector

            collector = MapDataCollector()
            geojson = collector.collect()
            node_count = len(geojson.get("features", []))
            sources = geojson.get("properties", {}).get("sources", {})

            # Find the map template
            src_dir = Path(__file__).parent.parent.parent
            map_template = src_dir / "web" / "node_map.html"

            if not map_template.exists():
                self.dialog.msgbox(
                    "Map Not Found",
                    f"Map template not found at:\n{map_template}"
                )
                return

            # Read template and inject data
            with open(map_template, 'r') as f:
                html_content = f.read()

            if node_count > 0:
                geojson_str = json.dumps(geojson)
                inject_script = (
                    f'\n<script>\n'
                    f'// MeshForge: {node_count} nodes from '
                    f'meshtasticd({sources.get("meshtasticd", 0)}) '
                    f'mqtt({sources.get("mqtt", 0)}) '
                    f'tracker({sources.get("node_tracker", 0)})\n'
                    f'window.meshforgeData = {geojson_str};\n'
                    f'</script>\n</body>'
                )
                html_content = html_content.replace('</body>', inject_script)

            # Write to user-accessible location
            from utils.paths import get_real_user_home
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "live_map.html"

            with open(output_file, 'w') as f:
                f.write(html_content)

            # Build detailed source breakdown
            source_info = []
            source_info.append(f"meshtasticd: {sources.get('meshtasticd', 0)}")
            source_info.append(f"MQTT: {sources.get('mqtt', 0)}")
            source_info.append(f"node_tracker: {sources.get('node_tracker', 0)}")

            msg = (
                f"Map saved: {output_file}\n\n"
                f"Total nodes: {node_count}\n"
                f"Sources:\n  " + "\n  ".join(source_info) + "\n\n"
                "Opening in browser..."
            )
            self.dialog.msgbox("Live Map", msg)
            self._open_in_browser(f"file://{output_file}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate live map: {e}")

    def _start_map_server(self):
        """Start the map HTTP server for live-updating browser access."""
        import socket

        port = 5000

        # Check if port is already in use
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            if result == 0:
                self.dialog.msgbox(
                    "Map Server",
                    f"Map server already running!\n\n"
                    f"Local:  http://localhost:{port}\n"
                    f"Remote: http://<this-ip>:{port}\n\n"
                    "The map auto-refreshes every 30 seconds."
                )
                self._open_in_browser(f"http://localhost:{port}")
                return
        except OSError:
            pass

        try:
            from utils.map_data_service import MapServer

            server = MapServer(port=port)
            server.start_background()

            self.dialog.msgbox(
                "Map Server Started",
                f"Live map server running!\n\n"
                f"Local:  http://localhost:{port}\n"
                f"Remote: http://<this-ip>:{port}\n\n"
                "The map pulls fresh data every 30 seconds.\n"
                "Server runs until MeshForge exits."
            )
            self._open_in_browser(f"http://localhost:{port}")

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to start map server: {e}")

    def _toggle_auto_map(self):
        """Toggle the auto-open map on launch setting."""
        import json

        settings_file = self._get_map_settings_file()
        settings = {}

        if settings_file.exists():
            try:
                with open(settings_file) as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        current = settings.get("auto_open_map", False)
        settings["auto_open_map"] = not current

        try:
            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)

            state = "ENABLED" if settings["auto_open_map"] else "DISABLED"
            msg = (
                f"Auto-open map: {state}\n\n"
            )
            if settings["auto_open_map"]:
                msg += (
                    "The map server will start automatically\n"
                    "when MeshForge launches, and the map will\n"
                    "be accessible locally and remotely on port 5000."
                )
            else:
                msg += "Map server will not start automatically."

            self.dialog.msgbox("Map Settings", msg)
        except OSError as e:
            self.dialog.msgbox("Error", f"Failed to save setting: {e}")

        # Re-show the live map menu
        self._open_live_map()

    def _intelligent_diagnostics(self):
        """Run intelligent diagnostics with symptom analysis."""
        # Common symptoms to diagnose
        symptom_choices = [
            ("connection", "Connection refused to meshtasticd"),
            ("no_nodes", "No nodes visible in mesh"),
            ("weak_signal", "Weak signal / low SNR"),
            ("timeout", "Message timeouts"),
            ("service", "Service not starting"),
            ("custom", "Describe custom symptom"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Intelligent Diagnostics",
                "Select a symptom to diagnose:",
                symptom_choices
            )

            if choice is None or choice == "back":
                break

            symptom_text = None
            if choice == "custom":
                symptom_text = self.dialog.inputbox(
                    "Custom Symptom",
                    "Describe the issue you're experiencing:"
                )
                if not symptom_text:
                    continue
            else:
                # Map choice to symptom text
                symptom_map = {
                    "connection": "Connection refused to meshtasticd on port 4403",
                    "no_nodes": "No nodes visible in mesh network",
                    "weak_signal": "Weak signal with low SNR values",
                    "timeout": "Message timeouts when sending",
                    "service": "Service meshtasticd failed to start",
                }
                symptom_text = symptom_map.get(choice, choice)

            self._run_diagnosis(symptom_text)

    def _run_diagnosis(self, symptom: str):
        """Run diagnosis on a symptom."""
        self.dialog.infobox("Analyzing", f"Analyzing: {symptom[:40]}...")

        try:
            from utils.diagnostic_engine import diagnose, Category, Severity

            # Run diagnosis
            diagnosis = diagnose(
                symptom,
                category=Category.CONNECTIVITY,
                severity=Severity.ERROR
            )

            if diagnosis:
                # Format diagnosis for display
                result_lines = [
                    f"SYMPTOM: {symptom}",
                    "",
                    f"LIKELY CAUSE:",
                    f"  {diagnosis.likely_cause}",
                    "",
                    f"CONFIDENCE: {diagnosis.confidence:.0%}",
                    "",
                ]

                if diagnosis.evidence:
                    result_lines.append("EVIDENCE:")
                    for ev in diagnosis.evidence[:3]:
                        result_lines.append(f"  - {ev}")
                    result_lines.append("")

                if diagnosis.suggestions:
                    result_lines.append("SUGGESTIONS:")
                    for i, sug in enumerate(diagnosis.suggestions[:5], 1):
                        result_lines.append(f"  {i}. {sug}")
                    result_lines.append("")

                if diagnosis.auto_recoverable:
                    result_lines.append(f"AUTO-RECOVERY: {diagnosis.recovery_action}")

                self.dialog.msgbox(
                    "Diagnosis Result",
                    "\n".join(result_lines)
                )
            else:
                self.dialog.msgbox(
                    "Diagnosis",
                    f"No specific diagnosis found for:\n{symptom}\n\n"
                    "Try the Knowledge Base for general information,\n"
                    "or use Claude Assistant for detailed help."
                )
        except ImportError:
            self.dialog.msgbox(
                "Error",
                "Diagnostic engine not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Diagnosis failed: {e}")

    def _knowledge_base_query(self):
        """Query the knowledge base for mesh networking concepts."""
        # Common topics
        topic_choices = [
            ("snr", "What is SNR?"),
            ("rssi", "What is RSSI?"),
            ("lora", "How does LoRa work?"),
            ("meshtastic", "Meshtastic basics"),
            ("reticulum", "Reticulum basics"),
            ("antenna", "Antenna selection"),
            ("range", "Improving range"),
            ("custom", "Custom query"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Knowledge Base",
                "Select a topic or enter custom query:",
                topic_choices
            )

            if choice is None or choice == "back":
                break

            query = None
            if choice == "custom":
                query = self.dialog.inputbox(
                    "Knowledge Query",
                    "Enter your question about mesh networking:"
                )
                if not query:
                    continue
            else:
                query_map = {
                    "snr": "What is SNR?",
                    "rssi": "What is RSSI?",
                    "lora": "How does LoRa modulation work?",
                    "meshtastic": "What is Meshtastic and how does it work?",
                    "reticulum": "What is Reticulum Network Stack?",
                    "antenna": "How do I choose the right antenna?",
                    "range": "How can I improve my mesh range?",
                }
                query = query_map.get(choice, choice)

            self._query_knowledge(query)

    def _query_knowledge(self, query: str):
        """Query the knowledge base."""
        self.dialog.infobox("Searching", f"Searching: {query[:40]}...")

        try:
            from utils.knowledge_base import get_knowledge_base

            kb = get_knowledge_base()
            results = kb.query(query)

            if results:
                # Format results for display
                result_lines = [f"QUERY: {query}", ""]

                for i, result in enumerate(results[:3], 1):
                    result_lines.append(f"--- Result {i}: {result.title} ---")
                    # Truncate content for dialog display
                    content = result.content.strip()
                    if len(content) > 800:
                        content = content[:800] + "..."
                    result_lines.append(content)
                    result_lines.append("")

                self.dialog.msgbox(
                    "Knowledge Base Results",
                    "\n".join(result_lines)
                )
            else:
                self.dialog.msgbox(
                    "No Results",
                    f"No knowledge base entries found for:\n{query}\n\n"
                    "Try different keywords or use Claude Assistant."
                )
        except ImportError:
            self.dialog.msgbox(
                "Error",
                "Knowledge base not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Query failed: {e}")

    def _claude_assistant(self):
        """Interactive Claude Assistant for mesh help."""
        # Check for API key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        mode = "PRO" if api_key else "Standalone"

        self.dialog.msgbox(
            "Claude Assistant",
            f"Mode: {mode}\n\n"
            f"{'PRO mode: Full Claude AI capabilities' if api_key else 'Standalone: Rule-based + knowledge base'}\n\n"
            f"{'Set ANTHROPIC_API_KEY for PRO features.' if not api_key else 'API key detected.'}"
        )

        while True:
            question = self.dialog.inputbox(
                f"Claude Assistant ({mode})",
                "Ask a question about mesh networking:\n(Enter blank to exit)"
            )

            if not question:
                break

            self._ask_assistant(question)

    def _ask_assistant(self, question: str):
        """Ask the Claude assistant."""
        self.dialog.infobox("Thinking", f"Processing: {question[:40]}...")

        try:
            from utils.claude_assistant import ClaudeAssistant

            assistant = ClaudeAssistant()
            response = assistant.ask(question)

            # Format response
            result_lines = [
                f"Q: {question}",
                "",
                "ANSWER:",
                response.answer,
                "",
            ]

            if response.suggested_actions:
                result_lines.append("SUGGESTED ACTIONS:")
                for action in response.suggested_actions[:3]:
                    result_lines.append(f"  - {action}")
                result_lines.append("")

            result_lines.append(f"Mode: {response.mode.value.upper()}")
            if response.confidence > 0:
                result_lines.append(f"Confidence: {response.confidence:.0%}")

            self.dialog.msgbox(
                "Claude Assistant",
                "\n".join(result_lines)
            )
        except ImportError:
            self.dialog.msgbox(
                "Error",
                "Claude assistant not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Assistant failed: {e}")

    def _generate_coverage_map(self):
        """Generate a coverage map and open in browser."""
        # Get node data source
        source_choices = [
            ("all", "All sources (recommended)"),
            ("live", "Live from meshtasticd only"),
            ("mqtt", "From MQTT broker"),
            ("file", "From saved node file"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Coverage Map",
            "Select node data source:",
            source_choices
        )

        if choice is None or choice == "back":
            return

        self.dialog.infobox("Generating", "Creating coverage map...")

        try:
            from utils.coverage_map import CoverageMapGenerator, MapNode
            from utils.paths import get_real_user_home

            generator = CoverageMapGenerator()

            if choice == "all":
                # Use MapDataCollector to get nodes from ALL sources
                # (meshtasticd, MQTT, node_cache.json, RNS cache)
                try:
                    from utils.map_data_service import MapDataCollector
                    collector = MapDataCollector()
                    geojson = collector.collect()
                    features = geojson.get('features', [])
                    if features:
                        generator.add_nodes_from_geojson(geojson)
                        self.dialog.infobox(
                            "Generating",
                            f"Found {len(features)} nodes from all sources..."
                        )
                    else:
                        self.dialog.msgbox(
                            "No Nodes",
                            "No nodes found from any source.\n\n"
                            "Check meshtasticd, MQTT, or node cache."
                        )
                        return
                except ImportError as e:
                    self.dialog.msgbox("Error", f"MapDataCollector not available: {e}")
                    return

            elif choice == "live":
                # Try to get nodes from meshtasticd
                nodes = self._get_nodes_from_meshtastic()
                if nodes:
                    for node in nodes:
                        generator.add_node(node)
                else:
                    self.dialog.msgbox(
                        "No Nodes",
                        "Could not get nodes from meshtasticd.\n\n"
                        "Ensure meshtasticd is running and has nodes."
                    )
                    return

            elif choice == "mqtt":
                # Try MQTT source
                nodes = self._get_nodes_from_mqtt()
                if nodes:
                    for node in nodes:
                        generator.add_node(node)
                else:
                    self.dialog.msgbox(
                        "No Nodes",
                        "Could not get nodes from MQTT.\n\n"
                        "Check MQTT broker connection."
                    )
                    return

            elif choice == "file":
                # Load from file
                file_path = self.dialog.inputbox(
                    "Node File",
                    "Enter path to node JSON file:"
                )
                if not file_path:
                    return
                try:
                    import json
                    with open(file_path) as f:
                        data = json.load(f)
                    generator.add_nodes_from_geojson(data)
                except Exception as e:
                    self.dialog.msgbox("Error", f"Failed to load file: {e}")
                    return

            # Generate map
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "coverage_map.html"

            generator.generate(str(output_file))

            # Open in browser
            self.dialog.msgbox(
                "Map Generated",
                f"Coverage map saved to:\n{output_file}\n\n"
                "Opening in browser..."
            )

            # Open browser in background
            self._open_in_browser(str(output_file))

        except ImportError as e:
            self.dialog.msgbox(
                "Error",
                f"Coverage map generator not available: {e}\n\n"
                "You may need to install folium:\n"
                "pip3 install folium"
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Map generation failed: {e}")

    def _get_nodes_from_meshtastic(self):
        """Get nodes from meshtasticd."""
        try:
            from utils.coverage_map import MapNode
            import socket

            # Check if meshtasticd is running
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()

            if result != 0:
                return []

            # Try to get node list via meshtastic CLI
            cli_path = self._get_meshtastic_cli()
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return []

            # Parse nodes from output (simplified)
            nodes = []
            # This is a simplified parser - real implementation would
            # parse the actual meshtastic output format
            return nodes

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            return []

    def _get_nodes_from_mqtt(self):
        """Get nodes from MQTT broker."""
        # Placeholder - would implement MQTT node retrieval
        return []

    def _open_in_browser(self, url: str):
        """Open URL in browser (in background thread).

        Handles running as root by using sudo -u to run browser as real user.
        """
        import threading
        import os

        def do_open():
            try:
                # When running as root, use sudo -u to run as real user
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    # Not root or no SUDO_USER - try xdg-open directly
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass

        threading.Thread(target=do_open, daemon=True).start()
