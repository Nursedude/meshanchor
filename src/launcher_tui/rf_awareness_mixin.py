"""
RF Awareness Mixin for MeshForge Launcher TUI.

Provides SDR-based RF monitoring tools:
- Spectrum waterfall display
- Channel utilization monitoring
- Signal strength surveys
- LoRa band analysis
"""

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from backend import clear_screen

logger = logging.getLogger(__name__)


class RFAwarenessMixin:
    """Mixin providing RF awareness tools for the TUI launcher."""

    def _rf_awareness_menu(self):
        """RF Awareness and SDR monitoring menu."""
        choices = [
            ("status", "SDR Status & Connection"),
            ("spectrum", "Spectrum Snapshot"),
            ("waterfall", "Spectrum Waterfall (ASCII)"),
            ("utilization", "Channel Utilization Monitor"),
            ("survey", "Signal Strength Survey"),
            ("interference", "Interference Detection"),
            ("settings", "SDR Settings"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "RF Awareness (SDR)",
                "LoRa band monitoring with Airspy SDR:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("RF Status", self._rf_status),
                "spectrum": ("Spectrum Snapshot", self._rf_spectrum_snapshot),
                "waterfall": ("Waterfall Display", self._rf_waterfall),
                "utilization": ("Channel Utilization", self._rf_utilization),
                "survey": ("Signal Survey", self._rf_survey),
                "interference": ("Interference Detection", self._rf_interference),
                "settings": ("SDR Settings", self._rf_settings),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _get_rf_awareness(self):
        """Get or create RFAwareness instance."""
        try:
            from utils.rf_awareness import RFAwareness
            if not hasattr(self, '_rf_awareness') or self._rf_awareness is None:
                self._rf_awareness = RFAwareness()
            return self._rf_awareness
        except ImportError:
            return None

    def _rf_status(self):
        """Show SDR status and manage connection."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox(
                "Unavailable",
                "RF Awareness module not loaded.\n\n"
                "Required: numpy, SoapySDR (optional)"
            )
            return

        # List available devices
        try:
            from utils.rf_awareness import list_sdr_devices, SDRBackend, LoRaBand
            devices = list_sdr_devices()
        except Exception as e:
            devices = []

        lines = [
            "SDR STATUS",
            "=" * 50,
            "",
        ]

        status = rf.get_status()
        lines.append(f"Connected: {'Yes' if status['connected'] else 'No'}")
        lines.append(f"Backend: {status['backend']}")

        if status['device']:
            lines.append("")
            lines.append("DEVICE INFO:")
            lines.append(f"  Label: {status['device'].get('label', 'N/A')}")
            lines.append(f"  Driver: {status['device'].get('driver', 'N/A')}")
            lines.append(f"  Hardware: {status['device'].get('hardware', 'N/A')}")

        lines.append("")
        lines.append("SETTINGS:")
        lines.append(f"  Sample Rate: {status['sample_rate'] / 1e6:.2f} MSPS")
        lines.append(f"  FFT Size: {status['fft_size']}")
        lines.append(f"  Gain: {status['gain']:.1f} dB")

        lines.append("")
        lines.append("AVAILABLE DEVICES:")
        if devices:
            for dev in devices:
                lines.append(f"  • {dev.label} ({dev.driver})")
        else:
            lines.append("  No SoapySDR devices found")
            lines.append("  (Mock mode available for testing)")

        self.dialog.msgbox("SDR Status", "\n".join(lines))

        # Offer to connect/disconnect
        if not rf.is_connected:
            if self.dialog.yesno(
                "Connect SDR",
                "Connect to SDR device?\n\n"
                "If no hardware found, will use mock mode for testing."
            ):
                self.dialog.infobox("Connecting...", "Connecting to SDR...")

                # Check for Airspy specifically
                has_airspy = any("airspy" in d.driver.lower() for d in devices)

                if rf.connect(device_filter="airspy" if has_airspy else None):
                    self.dialog.msgbox(
                        "Connected",
                        f"Connected to: {rf.device_info.label}\n"
                        f"Backend: {rf.backend.name}"
                    )
                else:
                    self.dialog.msgbox("Error", "Failed to connect to SDR")
        else:
            if self.dialog.yesno(
                "Disconnect",
                "Disconnect from SDR?",
                default_no=True
            ):
                rf.disconnect()
                self.dialog.msgbox("Disconnected", "SDR disconnected")

    def _rf_spectrum_snapshot(self):
        """Get and display a spectrum snapshot."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        # Ensure connected
        if not rf.is_connected:
            if not rf.connect():
                self.dialog.msgbox("Error", "Failed to connect to SDR")
                return

        # Select band
        try:
            from utils.rf_awareness import LoRaBand
            band_choices = [
                ("US_915", "US 915 MHz (902-928 MHz)"),
                ("EU_868", "EU 868 MHz (863-870 MHz)"),
                ("EU_433", "EU 433 MHz (433-434 MHz)"),
                ("AS_923", "Asia 923 MHz (920-925 MHz)"),
                ("custom", "Custom Frequency"),
                ("back", "Back"),
            ]
        except ImportError:
            band_choices = [("custom", "Custom Frequency"), ("back", "Back")]

        band_choice = self.dialog.menu(
            "Select Band",
            "Choose frequency band to monitor:",
            band_choices
        )

        if not band_choice or band_choice == "back":
            return

        band = None
        center_freq = None

        if band_choice == "custom":
            freq_str = self.dialog.inputbox(
                "Center Frequency",
                "Enter center frequency (MHz):",
                "915.0"
            )
            if freq_str:
                try:
                    center_freq = float(freq_str) * 1e6
                except ValueError:
                    self.dialog.msgbox("Error", "Invalid frequency")
                    return
        else:
            band = LoRaBand[band_choice]

        self.dialog.infobox("Scanning...", "Capturing spectrum snapshot...")

        snapshot = rf.get_spectrum_snapshot(band=band, center_freq=center_freq)

        if snapshot is None:
            self.dialog.msgbox("Error", "Failed to capture spectrum")
            return

        # Generate ASCII display
        ascii_spectrum = rf.generate_ascii_spectrum(snapshot, width=70, height=12)

        # Add summary
        lines = [
            ascii_spectrum,
            "",
            f"Timestamp: {snapshot.timestamp.strftime('%H:%M:%S')}",
            f"Noise Floor: {snapshot.noise_floor_dbm:.1f} dBm",
            f"Peak: {snapshot.peak_power_dbm:.1f} dBm at {snapshot.peak_freq / 1e6:.3f} MHz",
        ]

        self.dialog.msgbox("Spectrum Snapshot", "\n".join(lines))

    def _rf_waterfall(self):
        """Display live ASCII waterfall."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        if not rf.is_connected:
            if not rf.connect():
                self.dialog.msgbox("Error", "Failed to connect to SDR")
                return

        try:
            from utils.rf_awareness import LoRaBand
            band = LoRaBand.US_915
        except ImportError:
            self.dialog.msgbox("Error", "LoRaBand not available")
            return

        # Show waterfall in terminal (exit TUI temporarily)
        clear_screen()
        print("=== RF Waterfall Display ===")
        print(f"Band: {band.description}")
        print(f"Center: {band.center_freq / 1e6:.3f} MHz")
        print("\nPress Ctrl+C to stop\n")

        try:
            while True:
                snapshot = rf.get_spectrum_snapshot(band=band, averaging=3)
                if snapshot:
                    # Simple one-line spectrum bar
                    power = snapshot.power_dbm
                    # Resample to 60 chars
                    import numpy as np
                    indices = np.linspace(0, len(power) - 1, 60).astype(int)
                    display = power[indices]

                    # Normalize
                    min_db = snapshot.noise_floor_dbm - 5
                    max_db = snapshot.peak_power_dbm + 5

                    bar_chars = " ▁▂▃▄▅▆▇█"
                    line = ""
                    for val in display:
                        level = (val - min_db) / (max_db - min_db)
                        level = max(0, min(1, level))
                        idx = int(level * (len(bar_chars) - 1))
                        line += bar_chars[idx]

                    timestamp = snapshot.timestamp.strftime('%H:%M:%S')
                    print(f"{timestamp} |{line}| {snapshot.peak_power_dbm:.0f}dBm")

                time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n\nWaterfall stopped.")

        self._wait_for_enter()

    def _rf_utilization(self):
        """Measure channel utilization."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        if not rf.is_connected:
            if not rf.connect():
                self.dialog.msgbox("Error", "Failed to connect to SDR")
                return

        # Get duration
        duration_str = self.dialog.inputbox(
            "Measurement Duration",
            "Enter measurement duration (seconds):",
            "10"
        )

        if not duration_str:
            return

        try:
            duration = float(duration_str)
            if duration < 1 or duration > 300:
                raise ValueError("Duration out of range")
        except ValueError:
            self.dialog.msgbox("Error", "Invalid duration (1-300 seconds)")
            return

        try:
            from utils.rf_awareness import LoRaBand
            band = LoRaBand.US_915
        except ImportError:
            band = None

        self.dialog.infobox(
            "Measuring...",
            f"Measuring channel utilization for {duration:.0f} seconds..."
        )

        util = rf.measure_channel_utilization(band=band, duration_sec=duration)

        if util is None:
            self.dialog.msgbox("Error", "Measurement failed")
            return

        # Display results
        lines = [
            "CHANNEL UTILIZATION",
            "=" * 50,
            "",
            f"Frequency: {util.frequency / 1e6:.3f} MHz",
            f"Duration: {util.duration_sec:.1f} seconds",
            "",
            "RESULTS:",
            f"  Utilization: {util.utilization_percent:.1f}%",
            f"  Duty Cycle: {util.duty_cycle:.4f}",
            f"  Active Time: {util.active_time_sec:.2f} sec",
            "",
            "SIGNALS:",
            f"  Signal Count: {util.signal_count}",
            f"  Avg Power: {util.avg_signal_power_dbm:.1f} dBm",
            f"  Peak Power: {util.peak_signal_power_dbm:.1f} dBm",
            "",
            f"Noise Floor: {util.noise_floor_dbm:.1f} dBm",
        ]

        # Utilization assessment
        if util.utilization_percent < 5:
            assessment = "Very Low - Channel is mostly clear"
        elif util.utilization_percent < 20:
            assessment = "Low - Occasional activity"
        elif util.utilization_percent < 50:
            assessment = "Moderate - Regular activity"
        elif util.utilization_percent < 80:
            assessment = "High - Heavy usage"
        else:
            assessment = "Very High - Channel congested"

        lines.append("")
        lines.append(f"Assessment: {assessment}")

        self.dialog.msgbox("Channel Utilization", "\n".join(lines))

    def _rf_survey(self):
        """Perform signal strength survey."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        if not rf.is_connected:
            if not rf.connect():
                self.dialog.msgbox("Error", "Failed to connect to SDR")
                return

        try:
            from utils.rf_awareness import LoRaBand
        except ImportError:
            self.dialog.msgbox("Error", "Module not available")
            return

        # Select band
        band_choices = [
            ("US_915", "US 915 MHz Band"),
            ("EU_868", "EU 868 MHz Band"),
            ("back", "Back"),
        ]

        band_choice = self.dialog.menu(
            "Signal Survey",
            "Select band to survey:",
            band_choices
        )

        if not band_choice or band_choice == "back":
            return

        band = LoRaBand[band_choice]

        self.dialog.infobox(
            "Surveying...",
            f"Surveying {band.description}...\n"
            "This will take about 20 seconds."
        )

        survey = rf.signal_survey(band=band, duration_per_freq_sec=2.0)

        if not survey.points:
            self.dialog.msgbox("Error", "Survey failed - no data collected")
            return

        # Display results
        lines = [
            "SIGNAL STRENGTH SURVEY",
            "=" * 50,
            "",
            f"Band: {band.description}",
            f"Duration: {survey.statistics.get('duration_sec', 0):.1f} seconds",
            f"Points: {survey.statistics.get('point_count', 0)}",
            "",
            "FREQUENCY SCAN:",
        ]

        # Show each frequency point
        for point in survey.points:
            freq_mhz = point.frequency / 1e6
            power_bar = self._power_bar(point.power_dbm, -100, -40)
            lines.append(f"  {freq_mhz:7.2f} MHz: {power_bar} {point.power_dbm:.1f} dBm")

        lines.append("")
        lines.append("STATISTICS:")
        stats = survey.statistics
        lines.append(f"  Min Power: {stats.get('power_min_dbm', 0):.1f} dBm")
        lines.append(f"  Max Power: {stats.get('power_max_dbm', 0):.1f} dBm")
        lines.append(f"  Avg Power: {stats.get('power_avg_dbm', 0):.1f} dBm")
        lines.append(f"  Avg SNR: {stats.get('snr_avg_db', 0):.1f} dB")

        self.dialog.msgbox("Survey Results", "\n".join(lines))

    def _power_bar(self, power_dbm: float, min_dbm: float, max_dbm: float,
                   width: int = 15) -> str:
        """Generate a simple power bar."""
        level = (power_dbm - min_dbm) / (max_dbm - min_dbm)
        level = max(0, min(1, level))
        filled = int(level * width)
        return "█" * filled + "░" * (width - filled)

    def _rf_interference(self):
        """Interference detection mode."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        if not rf.is_connected:
            if not rf.connect():
                self.dialog.msgbox("Error", "Failed to connect to SDR")
                return

        try:
            from utils.rf_awareness import LoRaBand
            band = LoRaBand.US_915
        except ImportError:
            self.dialog.msgbox("Error", "Module not available")
            return

        self.dialog.infobox(
            "Scanning...",
            "Scanning for interference sources..."
        )

        # Take multiple snapshots to detect consistent interference
        import numpy as np
        snapshots = []
        for _ in range(5):
            snapshot = rf.get_spectrum_snapshot(band=band, averaging=5)
            if snapshot:
                snapshots.append(snapshot)
            time.sleep(0.2)

        if len(snapshots) < 3:
            self.dialog.msgbox("Error", "Insufficient data collected")
            return

        # Analyze for interference
        lines = [
            "INTERFERENCE ANALYSIS",
            "=" * 50,
            "",
            f"Band: {band.description}",
            f"Snapshots: {len(snapshots)}",
            "",
        ]

        # Average spectrum
        avg_power = np.mean([s.power_dbm for s in snapshots], axis=0)
        noise_floor = np.percentile(avg_power, 10)

        # Find persistent signals (potential interference)
        threshold = noise_floor + 10  # 10 dB above noise
        interference_bins = np.where(avg_power > threshold)[0]

        if len(interference_bins) > 0:
            lines.append("DETECTED SIGNALS:")

            # Group adjacent bins into signals
            signals = []
            current_signal = [interference_bins[0]]

            for i in range(1, len(interference_bins)):
                if interference_bins[i] - interference_bins[i - 1] <= 2:
                    current_signal.append(interference_bins[i])
                else:
                    signals.append(current_signal)
                    current_signal = [interference_bins[i]]
            signals.append(current_signal)

            freqs = snapshots[0].frequencies

            for i, signal_bins in enumerate(signals[:5]):  # Show top 5
                center_bin = signal_bins[len(signal_bins) // 2]
                freq = freqs[center_bin] / 1e6
                power = avg_power[center_bin]
                width_khz = len(signal_bins) * (snapshots[0].bandwidth / len(freqs)) / 1e3

                lines.append(f"  Signal {i + 1}:")
                lines.append(f"    Frequency: {freq:.3f} MHz")
                lines.append(f"    Power: {power:.1f} dBm")
                lines.append(f"    Width: ~{width_khz:.0f} kHz")
                lines.append("")

            if len(signals) > 5:
                lines.append(f"  ... and {len(signals) - 5} more signals")
        else:
            lines.append("No significant interference detected.")
            lines.append("Channel appears clear.")

        lines.append("")
        lines.append(f"Noise Floor: {noise_floor:.1f} dBm")

        self.dialog.msgbox("Interference Analysis", "\n".join(lines))

    def _rf_settings(self):
        """Configure SDR settings."""
        rf = self._get_rf_awareness()

        if rf is None:
            self.dialog.msgbox("Unavailable", "RF Awareness module not loaded.")
            return

        settings_choices = [
            ("gain", f"Gain (current: {rf._gain:.0f} dB)"),
            ("sample_rate", f"Sample Rate (current: {rf._sample_rate / 1e6:.1f} MSPS)"),
            ("fft_size", f"FFT Size (current: {rf._fft_size})"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "SDR Settings",
                "Configure SDR parameters:",
                settings_choices
            )

            if choice is None or choice == "back":
                break

            if choice == "gain":
                gain_str = self.dialog.inputbox(
                    "Gain",
                    "Enter gain in dB (0-45 for Airspy):",
                    str(rf._gain)
                )
                if gain_str:
                    try:
                        gain = float(gain_str)
                        if 0 <= gain <= 50:
                            rf._gain = gain
                            if rf.is_connected:
                                rf.set_gain(gain)
                            self.dialog.msgbox("Updated", f"Gain set to {gain:.0f} dB")
                        else:
                            self.dialog.msgbox("Error", "Gain must be 0-50 dB")
                    except ValueError:
                        self.dialog.msgbox("Error", "Invalid gain value")

            elif choice == "sample_rate":
                rate_choices = [
                    ("2.5", "2.5 MSPS (recommended)"),
                    ("5.0", "5.0 MSPS"),
                    ("10.0", "10.0 MSPS (Airspy R2 only)"),
                ]
                rate_choice = self.dialog.menu(
                    "Sample Rate",
                    "Select sample rate:",
                    rate_choices
                )
                if rate_choice:
                    rf._sample_rate = float(rate_choice) * 1e6
                    self.dialog.msgbox(
                        "Updated",
                        f"Sample rate set to {rate_choice} MSPS\n"
                        "(Reconnect to apply)"
                    )

            elif choice == "fft_size":
                fft_choices = [
                    ("512", "512 (fast, lower resolution)"),
                    ("1024", "1024 (balanced)"),
                    ("2048", "2048 (high resolution)"),
                    ("4096", "4096 (very high resolution)"),
                ]
                fft_choice = self.dialog.menu(
                    "FFT Size",
                    "Select FFT size:",
                    fft_choices
                )
                if fft_choice:
                    rf._fft_size = int(fft_choice)
                    self.dialog.msgbox("Updated", f"FFT size set to {fft_choice}")

            # Update menu text
            settings_choices = [
                ("gain", f"Gain (current: {rf._gain:.0f} dB)"),
                ("sample_rate", f"Sample Rate (current: {rf._sample_rate / 1e6:.1f} MSPS)"),
                ("fft_size", f"FFT Size (current: {rf._fft_size})"),
                ("back", "Back"),
            ]
