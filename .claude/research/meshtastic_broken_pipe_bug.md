# Meshtastic Python Library - Broken Pipe Bug Report

**Date**: 2026-01-13
**Affects**: meshtastic Python package (all versions using TCP interface)
**Severity**: Low (cosmetic - command still succeeds)

## Description

When using the meshtastic CLI with `--host localhost` (TCP interface to meshtasticd), commands complete successfully but produce a "Broken pipe" error on exit.

## Error Output

```
Aborting due to: [Errno 32] Broken pipe

STDERR:
Traceback (most recent call last):
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/__main__.py", line 1090, in onConnected
    interface.close()
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/tcp_interface.py", line 80, in close
    super().close()
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/stream_interface.py", line 134, in close
    MeshInterface.close(self)
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/mesh_interface.py", line 148, in close
    self._sendDisconnect()
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/mesh_interface.py", line 1193, in _sendDisconnect
    self._sendToRadio(m)
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/mesh_interface.py", line 1218, in _sendToRadio
    self._sendToRadioImpl(toRadio)
  File "/usr/local/lib/python3.13/dist-packages/meshtastic/stream_interface.py", line 129, in _sendToRadioImpl
    self._writeBytes(header + b)
```

## Root Cause

In `mesh_interface.py:_sendDisconnect()`, the code attempts to send a disconnect message after the command completes. However, `meshtasticd` has already closed the TCP connection, resulting in `EPIPE` (broken pipe).

## Impact

- **Commands succeed** - the actual operation completes before the error
- **User confusion** - error message suggests failure when there was none
- **Log noise** - fills logs with stack traces

## Suggested Fix

In `/usr/local/lib/python3.13/dist-packages/meshtastic/mesh_interface.py`, modify `_sendDisconnect()` to catch `BrokenPipeError`:

```python
def _sendDisconnect(self):
    """Send disconnect message to radio"""
    try:
        # ... existing code ...
        self._sendToRadio(m)
    except (BrokenPipeError, OSError) as e:
        # Connection already closed by peer - this is expected
        logging.debug(f"Disconnect send skipped (connection closed): {e}")
```

## Workaround for Users

Suppress stderr when running meshtastic CLI:

```bash
meshtastic --host localhost --info 2>/dev/null
```

## MeshForge Mitigation

MeshForge already uses `capture_output=True` in subprocess calls, which hides this error from the GUI. Direct CLI users will see it.

## References

- meshtastic-python: https://github.com/meshtastic/python
- Issue to file: https://github.com/meshtastic/python/issues
