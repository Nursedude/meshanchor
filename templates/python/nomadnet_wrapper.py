"""MeshAnchor NomadNet wrapper — refuses-loud on rpc_key mismatch.

Version: 1

Ported from MeshForge (templates/python/nomadnet_wrapper.py) for
MeshAnchor's Phase 8.4 tmux/systemd-user integration. Same monkey-
patch pattern, MeshAnchor-specific install hint.

NomadNet's TextUI startup calls RNS.Reticulum.get_interface_stats(),
which opens a multiprocessing.connection.Client to rnsd's RPC socket.
Two distinct failures arrive through that codepath:

  - Transient (rnsd not yet up, or briefly down): swallow + degrade
    (empty interface stats, NomadNet UI still renders).
  - AuthenticationError: rpc_key mismatch — rnsd and NomadNet are
    using different config dirs / different identities / different
    rpc_keys. Refuse-loud, exit 87, and let the operator fix the
    alignment manually.

The systemd unit pairs this with StartLimitBurst=5 to park the
service in failed state after a few retries, so the journal carries
one clear diagnostic line instead of a silent restart loop.

Both the TUI's NomadNet Service Control > Install user unit and
any future install_nomadnet.sh copy this file verbatim into
``~/.config/meshanchor/nomadnet_wrapper.py``. Bump the ``Version:``
line above and the wrapper is re-rendered on the next install or
refresh.
"""
import sys
from multiprocessing.context import AuthenticationError
import RNS

_orig_get_interface_stats = RNS.Reticulum.get_interface_stats

_FALLBACK = dict(interfaces=[])

# Transient RPC failures: degrade to empty stats, keep UI alive.
_TRANSIENT_EXC = (
    ConnectionRefusedError,
    BrokenPipeError,
    TypeError,
    KeyError,
    OSError,
)

# Sentinel exit code so systemd + the TUI can recognise this failure.
_EXIT_AUTH_MISMATCH = 87


def _safe_get_interface_stats(self):
    try:
        result = _orig_get_interface_stats(self)
    except AuthenticationError as e:
        # Refuse-loud — rpc_key mismatch is the actual root cause.
        sys.stderr.write(
            "\n[meshanchor nomadnet_wrapper] RNS rpc_key MISMATCH detected.\n"
            f"  Underlying error: {type(e).__name__}: {e}\n"
            "  rnsd and NomadNet are using different identities / rpc_keys.\n"
            "  FIX:\n"
            "    Edit /etc/reticulum/config (or ~/.reticulum/config) so\n"
            "    rnsd and NomadNet share the same [reticulum] rpc_key.\n"
            "    Then: sudo systemctl restart rnsd\n"
            "          systemctl --user restart nomadnet\n"
            "\n"
        )
        sys.stderr.flush()
        # Hard-exit before NomadNet's TUI loop starts crash-spinning.
        sys.exit(_EXIT_AUTH_MISMATCH)
    except _TRANSIENT_EXC:
        return _FALLBACK
    if not isinstance(result, dict) or 'interfaces' not in result:
        return _FALLBACK
    return result


RNS.Reticulum.get_interface_stats = _safe_get_interface_stats

from nomadnet.nomadnet import main
sys.exit(main())
