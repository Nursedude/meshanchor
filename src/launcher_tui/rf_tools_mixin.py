"""
RF Tools Mixin for MeshForge Launcher TUI.

Provides RF calculation methods extracted from main launcher
to reduce file size and improve maintainability.
"""

import math


class RFToolsMixin:
    """Mixin providing RF calculation tools for the TUI launcher."""

    def _rf_tools_menu(self):
        """RF tools menu."""
        choices = [
            ("freq", "Frequency Slot Calculator"),
            ("fspl", "Free Space Path Loss"),
            ("link", "Link Budget Calculator"),
            ("fresnel", "Fresnel Zone"),
            ("power", "EIRP Calculator"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "RF Tools",
                "Radio frequency calculations:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "freq": ("Frequency Slots", self._calc_frequency_slot),
                "fspl": ("FSPL Calculator", self._calc_fspl),
                "link": ("Link Budget", self._calc_link_budget),
                "fresnel": ("Fresnel Zone", self._calc_fresnel),
                "power": ("EIRP Calculator", self._calc_eirp),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _calc_frequency_slot(self):
        """
        Meshtastic Frequency Slot Calculator.

        Uses djb2 hash algorithm to calculate frequency slot from channel name.
        Based on Meshtastic firmware RadioInterface.cpp

        All 22 Meshtastic regions supported with correct band definitions.
        """
        # Region definitions: (name, freq_start_mhz, freq_end_mhz, default_slots, description)
        regions = [
            # Americas
            ("US", 902.0, 928.0, 104, "United States ISM"),
            ("ANZ", 915.0, 928.0, 52, "Australia/NZ"),
            # Europe
            ("EU_868", 869.4, 869.65, 1, "EU 869 MHz (SRD)"),
            ("EU_433", 433.0, 434.0, 8, "EU 433 MHz"),
            ("UK_868", 869.4, 869.65, 1, "UK 869 MHz"),
            ("UA_868", 868.0, 868.6, 2, "Ukraine 868 MHz"),
            ("UA_433", 433.0, 434.79, 8, "Ukraine 433 MHz"),
            ("RU", 868.7, 869.2, 2, "Russia"),
            # Asia-Pacific
            ("JP", 920.8, 923.8, 10, "Japan"),
            ("KR", 920.0, 923.0, 12, "Korea"),
            ("TW", 920.0, 925.0, 20, "Taiwan"),
            ("CN", 470.0, 510.0, 80, "China"),
            ("IN", 865.0, 867.0, 8, "India"),
            ("TH", 920.0, 925.0, 20, "Thailand"),
            ("PH", 920.0, 925.0, 20, "Philippines"),
            ("SG_923", 920.0, 925.0, 20, "Singapore 923"),
            ("MY_433", 433.0, 435.0, 8, "Malaysia 433 MHz"),
            ("MY_919", 919.0, 924.0, 20, "Malaysia 919 MHz"),
            # Oceania
            ("NZ_865", 864.0, 868.0, 16, "New Zealand 865 MHz"),
            # 2.4 GHz ISM (worldwide)
            ("LORA_24", 2400.0, 2483.5, 39, "2.4 GHz ISM (worldwide)"),
        ]

        # Select region
        region_choices = [(r[0], f"{r[0]}: {r[1]:.1f}-{r[2]:.1f} MHz") for r in regions]
        region_choices.append(("back", "Back"))

        region_choice = self.dialog.menu(
            "Frequency Slot",
            "Select region:",
            region_choices
        )

        if not region_choice or region_choice == "back":
            return

        # Find region
        region = None
        for r in regions:
            if r[0] == region_choice:
                region = r
                break

        if not region:
            return

        # Get channel name or slot number
        mode_choices = [
            ("name", "Calculate from Channel Name"),
            ("slot", "Enter Slot Number Directly"),
            ("back", "Back"),
        ]

        mode = self.dialog.menu(
            "Input Mode",
            "Calculate frequency from:",
            mode_choices
        )

        if not mode or mode == "back":
            return

        # Get bandwidth for modem preset
        bw_choices = [
            ("500", "500 kHz (SHORT_TURBO)"),
            ("250", "250 kHz (FAST presets)"),
            ("125", "125 kHz (SLOW presets)"),
            ("62.5", "62.5 kHz (VERY_LONG_SLOW)"),
        ]

        bw_choice = self.dialog.menu(
            "Bandwidth",
            "Select modem bandwidth:",
            bw_choices
        )

        if not bw_choice:
            return

        try:
            bw_khz = float(bw_choice)

            region_name = region[0]
            freq_start = region[1]
            freq_end = region[2]
            max_slots = region[3]
            region_desc = region[4]

            # Calculate num_channels based on bandwidth
            calculated_slots = int(math.floor((freq_end - freq_start) / (bw_khz / 1000)))
            num_channels = min(calculated_slots, max_slots) if calculated_slots > 0 else max_slots

            if mode == "name":
                # Get channel name
                channel_name = self.dialog.inputbox(
                    "Channel Name",
                    "Enter channel name:",
                    "LongFast"
                )

                if not channel_name:
                    return

                # djb2 hash algorithm
                def djb2_hash(s):
                    h = 5381
                    for c in s:
                        h = ((h << 5) + h) + ord(c)
                        h &= 0xFFFFFFFF  # Keep as 32-bit
                    return h

                hash_val = djb2_hash(channel_name)
                slot = hash_val % num_channels

            else:  # slot mode
                slot_str = self.dialog.inputbox(
                    "Slot Number",
                    f"Enter slot number (0-{num_channels-1}):",
                    "20"
                )

                if not slot_str:
                    return

                slot = int(slot_str)
                if slot < 0 or slot >= num_channels:
                    self.dialog.msgbox("Error", f"Slot must be 0-{num_channels-1}")
                    return

            # Calculate frequency
            freq_mhz = freq_start + (bw_khz / 2000) + (slot * (bw_khz / 1000))

            text = f"""Frequency Slot Calculation:

Region: {region_name} ({region_desc})
Band: {freq_start:.1f} - {freq_end:.1f} MHz
Bandwidth: {bw_khz} kHz
Available Slots: {num_channels}

Slot Number: {slot}
Center Frequency: {freq_mhz:.3f} MHz

Channel spans:
  {freq_mhz - bw_khz/2000:.3f} - {freq_mhz + bw_khz/2000:.3f} MHz"""

            if mode == "name":
                text += f"\n\nChannel Name: {channel_name}"
                text += f"\nHash Value: {hash_val}"

            self.dialog.msgbox("Frequency Result", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_fspl(self):
        """Calculate Free Space Path Loss."""
        try:
            dist_str = self.dialog.inputbox(
                "FSPL Calculator",
                "Distance (km):",
                "1"
            )
            if not dist_str:
                return

            freq_str = self.dialog.inputbox(
                "FSPL Calculator",
                "Frequency (MHz):",
                "915"
            )
            if not freq_str:
                return

            distance = float(dist_str)
            freq = float(freq_str)

            # FSPL formula: 20*log10(d) + 20*log10(f) + 32.45
            fspl = 20 * math.log10(distance) + 20 * math.log10(freq) + 32.45

            text = f"""Free Space Path Loss:

Distance: {distance} km
Frequency: {freq} MHz

FSPL: {fspl:.1f} dB

Note: This is theoretical minimum loss.
Actual loss will be higher due to
terrain, vegetation, and atmospheric
conditions."""

            self.dialog.msgbox("FSPL Result", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_link_budget(self):
        """Calculate link budget."""
        try:
            tx_pwr = self.dialog.inputbox("Link Budget", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            tx_gain = self.dialog.inputbox("Link Budget", "TX Antenna Gain (dBi):", "2")
            if not tx_gain:
                return

            rx_gain = self.dialog.inputbox("Link Budget", "RX Antenna Gain (dBi):", "2")
            if not rx_gain:
                return

            path_loss = self.dialog.inputbox("Link Budget", "Path Loss (dB):", "100")
            if not path_loss:
                return

            rx_sens = self.dialog.inputbox("Link Budget", "RX Sensitivity (dBm):", "-130")
            if not rx_sens:
                return

            tx_p = float(tx_pwr)
            tx_g = float(tx_gain)
            rx_g = float(rx_gain)
            pl = float(path_loss)
            rx_s = float(rx_sens)

            # Link budget: RX Power = TX Power + TX Gain + RX Gain - Path Loss
            rx_power = tx_p + tx_g + rx_g - pl
            link_margin = rx_power - rx_s

            status = "GOOD" if link_margin > 10 else "MARGINAL" if link_margin > 0 else "NO LINK"

            text = f"""Link Budget Analysis:

TX Power: {tx_p} dBm
TX Antenna: +{tx_g} dBi
RX Antenna: +{rx_g} dBi
Path Loss: -{pl} dB
RX Sensitivity: {rx_s} dBm

Received Power: {rx_power:.1f} dBm
Link Margin: {link_margin:.1f} dB

Status: {status}"""

            self.dialog.msgbox("Link Budget", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_fresnel(self):
        """Calculate Fresnel zone."""
        try:
            dist_str = self.dialog.inputbox("Fresnel Zone", "Distance (km):", "5")
            if not dist_str:
                return

            freq_str = self.dialog.inputbox("Fresnel Zone", "Frequency (MHz):", "915")
            if not freq_str:
                return

            distance = float(dist_str) * 1000  # Convert to meters
            freq = float(freq_str) * 1e6  # Convert to Hz

            # Fresnel zone radius at midpoint
            c = 3e8
            wavelength = c / freq
            d1 = d2 = distance / 2

            r1 = math.sqrt(wavelength * d1 * d2 / (d1 + d2))

            # 60% clearance recommendation
            clearance = r1 * 0.6

            text = f"""Fresnel Zone Calculator:

Distance: {distance/1000:.1f} km
Frequency: {freq/1e6:.0f} MHz
Wavelength: {wavelength:.3f} m

1st Fresnel Zone Radius: {r1:.1f} m
60% Clearance Needed: {clearance:.1f} m

For best signal, ensure no obstacles
within {clearance:.1f}m of the line
of sight at the midpoint."""

            self.dialog.msgbox("Fresnel Zone", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))

    def _calc_eirp(self):
        """Calculate EIRP."""
        try:
            tx_pwr = self.dialog.inputbox("EIRP", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            cable_loss = self.dialog.inputbox("EIRP", "Cable Loss (dB):", "1")
            if not cable_loss:
                return

            ant_gain = self.dialog.inputbox("EIRP", "Antenna Gain (dBi):", "6")
            if not ant_gain:
                return

            tx = float(tx_pwr)
            loss = float(cable_loss)
            gain = float(ant_gain)

            eirp = tx - loss + gain

            # Convert to watts
            eirp_watts = 10 ** ((eirp - 30) / 10)

            # FCC limit for 915MHz ISM is 36dBm EIRP (4W) for frequency hopping
            legal = "LEGAL (under 36 dBm)" if eirp <= 36 else "EXCEEDS FCC LIMIT"

            text = f"""EIRP Calculator:

TX Power: {tx} dBm
Cable Loss: -{loss} dB
Antenna Gain: +{gain} dBi

EIRP: {eirp:.1f} dBm ({eirp_watts*1000:.0f} mW)

US 915MHz ISM: {legal}

Note: Check local regulations."""

            self.dialog.msgbox("EIRP Result", text)

        except ValueError:
            self.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.dialog.msgbox("Error", str(e))
