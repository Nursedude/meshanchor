"""`/fleet/slo` must never fetch its OWN listening port.

The end-to-end companion to `TestMeshForgeBlockMergeSelfPortGuard`. Those tests
call `_merge_mesh_forge_blocks` directly with an injected `fetch`, so they pin
the decision but never prove that *serving the endpoint* stops making the call.
On 2026-08-13 that distinction was the whole incident: the merge's logic was
fine in isolation, and the box still fetched itself ~4.5 times a second for
~30 days.

⚠️ **This is NOT MeshForge's invariant.** The MF twin pins "no outbound HTTP
from /fleet/slo at all" (MF 9cbf2bb4), which is right there because MF's
handler legitimately does no outbound work. MeshAnchor's does: the aggregator
fetches the daemon (`/radio`, `/chat/messages`) on every call, and the
MeshForge passthrough is a real feature on a genuine co-install where MF owns
:5000 and MA serves a different port. Porting MF's blanket rule here would
either fail on legitimate traffic or quietly forbid a shipped feature. The
invariant that actually travels is the narrow one: **never call yourself.**

Egress is intercepted at `http.client.HTTPConnection.connect`, the single
chokepoint under `urllib.request.urlopen` (what `_http_get_json` uses), so the
test does not care HOW a future caller builds the URL.
"""
from unittest.mock import MagicMock, patch

import pytest

from monitoring import fleet_rollup as fr
from utils.map_http_handler import MapRequestHandler


SELF_PORT = 5000          # the port MESHFORGE_LOCAL_SLO_URL points at


class _SelfEgress(BaseException):
    """Raised when the endpoint dials its own listening port.

    ⚠️ Derives from **BaseException** deliberately. Drilled on the MeshForge
    twin: with an `Exception` subclass the equivalent test passed against
    planted violations, because a passthrough is defensive by nature — this
    repo's own `_merge_mesh_forge_blocks` is documented "Never raises" and
    wraps its fetch in `except Exception`. That swallows the signal and the
    guard reads green while detecting nothing. A detector whose signal the
    defect can absorb is not a detector.
    """


def _make_handler():
    """A handler with just enough state to call `_serve_fleet_slo`.

    The real `__init__` wants a live socket, so build via `__new__` — same
    technique as the twin's handler tests.
    """
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {}
    h.collector = None
    h.wfile = MagicMock()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h._send_cors_header = MagicMock()
    h._serve_json = MagicMock()
    return h


@pytest.fixture
def attempts():
    """Record every TCP target the endpoint dials, and block real network.

    A self-dial raises `_SelfEgress`; anything else raises OSError, which is
    what a test box looks like anyway (nothing listening) and which the
    aggregator's own never-raise handling absorbs. Recording happens BEFORE
    either raise, so a swallowing caller cannot erase the evidence.
    """
    seen = []

    def fake_connect(self):
        seen.append((self.host, self.port))
        if self.port == SELF_PORT and self.host in ("localhost", "127.0.0.1"):
            raise _SelfEgress(
                f"/fleet/slo dialled its own listening port {self.host}:"
                f"{self.port} — this is the 2026-08-13 self-recursion storm "
                f"(0f8419bb). Serving this endpoint must never re-enter it.")
        raise OSError("no network in tests")

    with patch("http.client.HTTPConnection.connect", fake_connect):
        yield seen


@pytest.fixture(autouse=True)
def _restore_self_port():
    prior = fr.SELF_HTTP_PORT
    yield
    fr.set_self_http_port(prior)


def _serve(handler):
    """Run the endpoint, tolerating ambient failure but never a self-dial."""
    try:
        handler._serve_fleet_slo()
    except _SelfEgress:
        raise
    except Exception:
        # Missing daemon, unreadable state, no systemd — none of that is what
        # this test judges. A verdict that changed with the box pins nothing.
        pass


class TestFleetSloNeverFetchesItself:

    def test_no_self_dial_when_we_own_the_passthrough_port(self, attempts):
        """THE storm: MeshForge 'co-installed', but :5000 is us."""
        fr.set_self_http_port(SELF_PORT)
        with patch.object(fr, "_meshforge_co_installed", lambda: True):
            _serve(_make_handler())
        self_dials = [a for a in attempts
                      if a[1] == SELF_PORT and a[0] in ("localhost", "127.0.0.1")]
        assert self_dials == [], f"endpoint dialled itself: {self_dials!r}"

    def test_the_handler_actually_ran(self, attempts):
        """Anti-vacuous guard. The assertion above is satisfied trivially if
        the handler dies before reaching slo_view, so prove the body executed
        by requiring it to have dialled SOMETHING (the daemon)."""
        fr.set_self_http_port(SELF_PORT)
        with patch.object(fr, "_meshforge_co_installed", lambda: True):
            _serve(_make_handler())
        assert attempts, (
            "no outbound attempt at all — the handler probably failed before "
            "reaching the passthrough, so the guard above proved nothing")

    def test_a_real_co_install_still_gets_the_passthrough(self, attempts):
        """The feature must survive its own guard. Where MA serves a different
        port and MeshForge genuinely owns :5000, the fetch SHOULD happen —
        otherwise the fix would have been a silent feature removal, and the
        test above would pass for the wrong reason forever."""
        fr.set_self_http_port(5001)
        with patch.object(fr, "_meshforge_co_installed", lambda: True):
            try:
                _serve(_make_handler())
            except _SelfEgress:
                pass          # expected here: 5000 is NOT us in this scenario
        assert any(a[1] == SELF_PORT for a in attempts), (
            "the MeshForge passthrough never fired on a genuine co-install — "
            f"the guard is over-broad. Dialled: {attempts!r}")
