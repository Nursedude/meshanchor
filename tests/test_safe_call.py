"""Tests for _safe_call dispatch pattern — proves TUI error resilience.

Verifies that _safe_call:
1. Passes return values through on success
2. Catches specific exception types and shows appropriate dialogs
3. Logs errors to the error log file
4. Lets KeyboardInterrupt propagate (clean exit)
5. Never crashes the TUI on any exception
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def launcher():
    """Create a MeshForgeLauncher with mocked dialog for testing."""
    with patch.dict('sys.modules', {
        'gi': MagicMock(),
        'gi.repository': MagicMock(),
    }):
        from launcher_tui.main import MeshForgeLauncher

        instance = MeshForgeLauncher.__new__(MeshForgeLauncher)
        instance.dialog = MagicMock()
        instance.src_dir = Path(__file__).parent.parent / "src"

        # Use a temp dir for error logs so we can inspect them
        tmp = tempfile.mkdtemp()
        instance._test_log_dir = Path(tmp)
        instance._get_error_log_path = lambda: Path(tmp) / "tui_errors.log"

        return instance


class TestSafeCallSuccess:
    """_safe_call should pass through return values on success."""

    def test_returns_method_result(self, launcher):
        """Successful calls return the method's return value."""
        def good_method():
            return 42
        result = launcher._safe_call("Test Method", good_method)
        assert result == 42

    def test_passes_args(self, launcher):
        """Arguments are forwarded to the method."""
        def add(a, b):
            return a + b
        result = launcher._safe_call("Add", add, 3, 7)
        assert result == 10

    def test_passes_kwargs(self, launcher):
        """Keyword arguments are forwarded."""
        def greet(name="world"):
            return f"hello {name}"
        result = launcher._safe_call("Greet", greet, name="mesh")
        assert result == "hello mesh"

    def test_lambda_dispatch(self, launcher):
        """Lambda dispatch (used in web_client_mixin) works."""
        result = launcher._safe_call("Lambda", lambda: "ok")
        assert result == "ok"

    def test_no_dialog_on_success(self, launcher):
        """No error dialog is shown on successful call."""
        launcher._safe_call("Quiet", lambda: None)
        launcher.dialog.msgbox.assert_not_called()


class TestSafeCallExceptionHandling:
    """_safe_call catches exceptions and shows user-friendly dialogs."""

    def test_import_error_shows_module_name(self, launcher):
        """ImportError shows the missing module name."""
        def bad_import():
            raise ImportError("No module named 'meshtastic'")
        launcher._safe_call("Feature", bad_import)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Module Not Available" in title
        assert "meshtastic" in body

    def test_timeout_shows_message(self, launcher):
        """TimeoutExpired shows service timeout message."""
        def slow():
            raise subprocess.TimeoutExpired(cmd="rnsd", timeout=30)
        launcher._safe_call("RNS Check", slow)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Timed Out" in title

    def test_permission_error_shows_sudo_hint(self, launcher):
        """PermissionError mentions running with sudo."""
        def no_perms():
            raise PermissionError("Operation not permitted")
        launcher._safe_call("Config Write", no_perms)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Permission" in title
        assert "sudo" in body

    def test_file_not_found_shows_path(self, launcher):
        """FileNotFoundError mentions the missing file."""
        def missing():
            raise FileNotFoundError("No such file: /usr/bin/meshtastic")
        launcher._safe_call("Device Check", missing)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Not Found" in title

    def test_connection_error_shows_service_hint(self, launcher):
        """ConnectionError mentions checking the service."""
        def offline():
            raise ConnectionError("Connection refused")
        launcher._safe_call("Bridge Status", offline)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Connection" in title
        assert "service" in body.lower()

    def test_generic_exception_caught(self, launcher):
        """Unexpected exceptions are caught and shown."""
        def kaboom():
            raise RuntimeError("Something unexpected")
        launcher._safe_call("Boom", kaboom)
        launcher.dialog.msgbox.assert_called_once()
        title, body = launcher.dialog.msgbox.call_args[0]
        assert "Error" in title
        assert "RuntimeError" in body

    def test_keyboard_interrupt_propagates(self, launcher):
        """KeyboardInterrupt is NOT caught — allows clean exit."""
        def ctrl_c():
            raise KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            launcher._safe_call("Exit", ctrl_c)

    def test_returns_none_on_exception(self, launcher):
        """Methods that raise exceptions return None."""
        def fail():
            raise ValueError("bad")
        result = launcher._safe_call("Fail", fail)
        assert result is None


class TestSafeCallErrorLogging:
    """_safe_call logs errors to the error log file."""

    def test_error_written_to_log(self, launcher):
        """Exceptions are written to the error log with traceback."""
        def fail():
            raise RuntimeError("test error 12345")
        launcher._safe_call("Log Test", fail)

        log_path = launcher._get_error_log_path()
        assert log_path.exists()
        content = log_path.read_text()
        assert "RuntimeError" in content
        assert "test error 12345" in content
        assert "Log Test" in content

    def test_import_error_logged(self, launcher):
        """ImportError is logged before showing dialog."""
        def bad():
            raise ImportError("No module named 'folium'")
        launcher._safe_call("Map Gen", bad)

        log_path = launcher._get_error_log_path()
        content = log_path.read_text()
        assert "ImportError" in content
        assert "Map Gen" in content


class TestErrorLogRotation:
    """Error log rotation prevents unbounded disk growth."""

    def test_rotation_on_large_file(self, launcher):
        """Log rotates when it exceeds 1 MB."""
        log_path = launcher._get_error_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Write > 1 MB to the log
        log_path.write_text("x" * 1_100_000)
        assert log_path.stat().st_size > 1_048_576

        # Trigger rotation via _log_error
        launcher._log_error("Rotation test", RuntimeError("trigger"))

        # Old file should be rotated to .log.1
        rotated = log_path.with_suffix('.log.1')
        assert rotated.exists()
        assert rotated.stat().st_size > 1_000_000

        # New log should be small (just the new entry)
        assert log_path.exists()
        assert log_path.stat().st_size < 10_000

    def test_no_rotation_under_limit(self, launcher):
        """Log is NOT rotated when under 1 MB."""
        log_path = launcher._get_error_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("small log content\n")

        launcher._log_error("Small test", RuntimeError("small"))

        rotated = log_path.with_suffix('.log.1')
        assert not rotated.exists()
        # Original log should have the new entry appended
        assert "Small test" in log_path.read_text()
