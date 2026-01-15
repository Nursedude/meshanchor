"""
esptool Wrapper for safe firmware flashing.

Provides subprocess management, locking, and progress reporting
for ESP32-based device firmware flashing.
"""

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Global flash lock - only one flash operation at a time
FLASH_LOCK = threading.Lock()


class FlashStage(Enum):
    """Stages of firmware flashing."""
    CONNECTING = "connecting"
    ERASING = "erasing"
    WRITING = "writing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class FlashProgress:
    """Progress information during flashing."""
    stage: FlashStage
    percent: int = 0
    message: str = ""
    bytes_written: int = 0
    bytes_total: int = 0


@dataclass
class FlashResult:
    """Result of a flash operation."""
    success: bool
    message: str
    duration_seconds: float = 0.0
    output: str = ""


class EsptoolWrapper:
    """Wrapper for esptool flash operations."""

    DEFAULT_BAUD = 460800
    FLASH_TIMEOUT = 300  # 5 minutes max for flashing

    # Partition offsets for Meshtastic firmware
    PARTITION_OFFSETS = {
        "bootloader": 0x1000,
        "partition_table": 0x8000,
        "firmware": 0x10000,
    }

    def __init__(self, esptool_path: Optional[str] = None):
        """Initialize esptool wrapper.

        Args:
            esptool_path: Path to esptool.py or esptool binary.
        """
        self._esptool_path = esptool_path or self._find_esptool()
        self._progress_callback: Optional[Callable[[FlashProgress], None]] = None
        self._abort_flag = threading.Event()

    def _find_esptool(self) -> str:
        """Find esptool executable.

        Returns:
            Path to esptool or 'esptool.py' if using Python module.

        Raises:
            RuntimeError: If esptool not found.
        """
        # Check if esptool is in PATH
        try:
            result = subprocess.run(
                ["esptool.py", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"[Flash] Found esptool.py: {result.stdout.strip()}")
                return "esptool.py"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try as Python module
        try:
            result = subprocess.run(
                ["python3", "-m", "esptool", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"[Flash] Found python3 -m esptool: {result.stdout.strip()}")
                return "python3 -m esptool"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        raise RuntimeError(
            "esptool not found. Install with: pip install esptool"
        )

    @property
    def is_available(self) -> bool:
        """Check if esptool is available."""
        try:
            self._find_esptool()
            return True
        except RuntimeError:
            return False

    def set_progress_callback(self, callback: Callable[[FlashProgress], None]):
        """Set callback for progress updates.

        Args:
            callback: Function to call with FlashProgress updates.
        """
        self._progress_callback = callback

    def _report_progress(self, stage: FlashStage, percent: int = 0,
                         message: str = "", **kwargs):
        """Report progress to callback."""
        if self._progress_callback:
            progress = FlashProgress(
                stage=stage,
                percent=percent,
                message=message,
                **kwargs
            )
            self._progress_callback(progress)

    def _build_command(self, port: str, baud: int, extra_args: List[str]) -> List[str]:
        """Build esptool command list.

        Args:
            port: Serial port.
            baud: Baud rate.
            extra_args: Additional arguments.

        Returns:
            Command as list of strings.
        """
        if self._esptool_path.startswith("python"):
            cmd = ["python3", "-m", "esptool"]
        else:
            cmd = [self._esptool_path]

        cmd.extend([
            "--port", port,
            "--baud", str(baud),
        ])
        cmd.extend(extra_args)

        return cmd

    def _parse_progress(self, line: str) -> Optional[Tuple[FlashStage, int]]:
        """Parse progress from esptool output.

        Args:
            line: Output line from esptool.

        Returns:
            Tuple of (stage, percent) or None.
        """
        line = line.strip()

        if "Connecting" in line:
            return (FlashStage.CONNECTING, 0)

        if "Erasing" in line:
            return (FlashStage.ERASING, 10)

        if "Writing at" in line:
            # Parse: Writing at 0x00010000... (1 %)
            match = re.search(r'\((\d+)\s*%\)', line)
            if match:
                percent = int(match.group(1))
                return (FlashStage.WRITING, 20 + int(percent * 0.7))

        if "Hash of data verified" in line or "Verifying" in line:
            return (FlashStage.VERIFYING, 95)

        if "Hard resetting" in line:
            return (FlashStage.COMPLETE, 100)

        return None

    def get_chip_info(self, port: str, baud: int = DEFAULT_BAUD) -> Optional[dict]:
        """Get chip information from connected device.

        Args:
            port: Serial port.
            baud: Baud rate.

        Returns:
            Dict with chip info or None.
        """
        cmd = self._build_command(port, baud, ["chip_id"])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"[Flash] chip_id failed: {result.stderr}")
                return None

            output = result.stdout + result.stderr
            info = {
                "raw": output,
                "chip": "",
                "mac": "",
            }

            # Parse chip type
            for pattern in [
                r"Chip is (ESP\d+\S*)",
                r"Detecting chip type[.]+\s*(\S+)",
            ]:
                match = re.search(pattern, output)
                if match:
                    info["chip"] = match.group(1)
                    break

            # Parse MAC address
            match = re.search(r"MAC:\s*([0-9a-fA-F:]+)", output)
            if match:
                info["mac"] = match.group(1)

            return info

        except subprocess.TimeoutExpired:
            logger.error("[Flash] Chip detection timed out")
            return None
        except Exception as e:
            logger.error(f"[Flash] Chip detection failed: {e}")
            return None

    def flash_firmware(self, port: str, firmware_path: Path,
                       baud: int = DEFAULT_BAUD,
                       erase_all: bool = False) -> FlashResult:
        """Flash firmware to device.

        Args:
            port: Serial port.
            firmware_path: Path to firmware binary.
            baud: Baud rate for flashing.
            erase_all: Erase entire flash before writing.

        Returns:
            FlashResult with success status and details.
        """
        if not firmware_path.exists():
            return FlashResult(
                success=False,
                message=f"Firmware file not found: {firmware_path}"
            )

        # Acquire flash lock
        if not FLASH_LOCK.acquire(timeout=5):
            return FlashResult(
                success=False,
                message="Another flash operation is in progress"
            )

        start_time = time.time()
        self._abort_flag.clear()

        try:
            logger.info(f"[Flash] Starting flash: {port} <- {firmware_path}")
            self._report_progress(FlashStage.CONNECTING, 0, "Connecting to device...")

            # Build flash command
            args = []

            if erase_all:
                args.extend(["--before", "default_reset", "--after", "hard_reset"])
                args.append("erase_flash")
            else:
                args.extend([
                    "write_flash",
                    "--flash_mode", "dio",
                    "--flash_freq", "80m",
                    "--flash_size", "detect",
                    hex(self.PARTITION_OFFSETS["firmware"]),
                    str(firmware_path)
                ])

            cmd = self._build_command(port, baud, args)
            logger.debug(f"[Flash] Command: {' '.join(cmd)}")

            # Run flash command with live output parsing
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            output_lines = []
            last_percent = 0

            try:
                while True:
                    if self._abort_flag.is_set():
                        process.terminate()
                        return FlashResult(
                            success=False,
                            message="Flash aborted by user",
                            duration_seconds=time.time() - start_time
                        )

                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break

                    line = line.strip()
                    if line:
                        output_lines.append(line)
                        logger.debug(f"[Flash] {line}")

                        # Parse and report progress
                        progress = self._parse_progress(line)
                        if progress:
                            stage, percent = progress
                            if percent > last_percent:
                                last_percent = percent
                                self._report_progress(stage, percent, line)

            except Exception as e:
                logger.error(f"[Flash] Error reading output: {e}")

            # Wait for process to complete
            return_code = process.wait(timeout=30)
            duration = time.time() - start_time
            output = "\n".join(output_lines)

            if return_code == 0:
                self._report_progress(FlashStage.COMPLETE, 100, "Flash complete")
                return FlashResult(
                    success=True,
                    message="Firmware flashed successfully",
                    duration_seconds=duration,
                    output=output
                )
            else:
                self._report_progress(FlashStage.ERROR, 0, "Flash failed")
                return FlashResult(
                    success=False,
                    message=f"Flash failed with code {return_code}",
                    duration_seconds=duration,
                    output=output
                )

        except subprocess.TimeoutExpired:
            self._report_progress(FlashStage.ERROR, 0, "Flash timed out")
            return FlashResult(
                success=False,
                message=f"Flash timed out after {self.FLASH_TIMEOUT}s",
                duration_seconds=time.time() - start_time
            )
        except Exception as e:
            self._report_progress(FlashStage.ERROR, 0, str(e))
            return FlashResult(
                success=False,
                message=str(e),
                duration_seconds=time.time() - start_time
            )
        finally:
            FLASH_LOCK.release()

    def abort(self):
        """Abort ongoing flash operation."""
        self._abort_flag.set()
        logger.warning("[Flash] Abort requested")

    def erase_flash(self, port: str, baud: int = DEFAULT_BAUD) -> FlashResult:
        """Erase device flash memory.

        Args:
            port: Serial port.
            baud: Baud rate.

        Returns:
            FlashResult with success status.
        """
        if not FLASH_LOCK.acquire(timeout=5):
            return FlashResult(
                success=False,
                message="Another flash operation is in progress"
            )

        start_time = time.time()

        try:
            logger.info(f"[Flash] Erasing flash: {port}")
            self._report_progress(FlashStage.ERASING, 0, "Erasing flash...")

            cmd = self._build_command(port, baud, ["erase_flash"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            duration = time.time() - start_time
            output = result.stdout + result.stderr

            if result.returncode == 0:
                self._report_progress(FlashStage.COMPLETE, 100, "Erase complete")
                return FlashResult(
                    success=True,
                    message="Flash erased successfully",
                    duration_seconds=duration,
                    output=output
                )
            else:
                return FlashResult(
                    success=False,
                    message=f"Erase failed: {result.stderr}",
                    duration_seconds=duration,
                    output=output
                )

        except subprocess.TimeoutExpired:
            return FlashResult(
                success=False,
                message="Erase timed out",
                duration_seconds=time.time() - start_time
            )
        except Exception as e:
            return FlashResult(
                success=False,
                message=str(e),
                duration_seconds=time.time() - start_time
            )
        finally:
            FLASH_LOCK.release()


def check_esptool_available() -> bool:
    """Check if esptool is installed and available.

    Returns:
        True if esptool is available.
    """
    try:
        wrapper = EsptoolWrapper()
        return wrapper.is_available
    except RuntimeError:
        return False
