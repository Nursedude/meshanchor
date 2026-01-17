"""
AI Tools Mixin for MeshForge Launcher TUI.

Provides AI-powered diagnostics, knowledge base queries, and coverage map
generation for the TUI launcher. Brings GTK AI features to terminal users.

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
        """AI-powered tools menu."""
        choices = [
            ("diagnose", "Intelligent Diagnostics"),
            ("knowledge", "Knowledge Base Query"),
            ("assistant", "Claude Assistant"),
            ("coverage", "Generate Coverage Map"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "AI Tools",
                "AI-powered mesh network assistance:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "diagnose":
                self._intelligent_diagnostics()
            elif choice == "knowledge":
                self._knowledge_base_query()
            elif choice == "assistant":
                self._claude_assistant()
            elif choice == "coverage":
                self._generate_coverage_map()

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
            ("live", "Live from meshtasticd"),
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

            if choice == "live":
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
            result = subprocess.run(
                ['meshtastic', '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return []

            # Parse nodes from output (simplified)
            nodes = []
            # This is a simplified parser - real implementation would
            # parse the actual meshtastic output format
            return nodes

        except Exception:
            return []

    def _get_nodes_from_mqtt(self):
        """Get nodes from MQTT broker."""
        # Placeholder - would implement MQTT node retrieval
        return []

    def _open_in_browser(self, url: str):
        """Open URL in browser (in background thread)."""
        import threading

        def do_open():
            try:
                # Try xdg-open first (Linux)
                subprocess.run(
                    ['xdg-open', url],
                    capture_output=True,
                    timeout=10
                )
            except Exception:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass

        threading.Thread(target=do_open, daemon=True).start()
