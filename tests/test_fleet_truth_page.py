"""Tests for the /fleet/truth honest-NOC visual (ported from MF `7085f4e4`).

The page (web/fleet_truth.html) is a faithful PROJECTION of /api/fleet/truth —
same bytes both consumers read. Pins the projection's honesty contract
statically, mirroring MF's tests/test_fleet_page.py, plus the MA-specific
invariant: the route is ADDITIVE — the legacy /fleet Fleet Monitor dashboard
keeps serving web/fleet.html untouched.
"""

import re
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.map_http_handler import MapRequestHandler

TRUTH_HTML = Path(__file__).parent.parent / "web" / "fleet_truth.html"
LEGACY_HTML = Path(__file__).parent.parent / "web" / "fleet.html"


@pytest.fixture(scope="module")
def page_source() -> str:
    assert TRUTH_HTML.exists(), "web/fleet_truth.html missing"
    return TRUTH_HTML.read_text(encoding="utf-8")


def _make_handler() -> MapRequestHandler:
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    sent_headers: list = []
    h.send_header = lambda k, v: sent_headers.append((k, v))
    h._sent_headers = sent_headers
    h.web_dir = None  # fall back to repo web/ next to src/
    return h


class TestFleetTruthPageRoute:
    def test_serve_truth_page_serves_html(self):
        h = _make_handler()
        h._serve_fleet_truth_page()
        h.send_response.assert_called_once_with(200)
        body = h.wfile.getvalue()
        assert b"Fleet Truth" in body
        assert b"MeshAnchor NOC" in body
        assert b"/api/fleet/truth" in body

    def test_serve_truth_page_sends_no_cache_headers(self):
        h = _make_handler()
        h._serve_fleet_truth_page()
        names = [k for k, _ in h._sent_headers]
        assert "Cache-Control" in names
        assert "Content-Type" in names

    def test_dispatch_routes_fleet_truth_to_page(self):
        h = _make_handler()
        h.path = "/fleet/truth"
        h._serve_fleet_truth_page = MagicMock()
        h._dispatch_get()
        h._serve_fleet_truth_page.assert_called_once()

    def test_endpoint_label_covers_fleet_truth(self):
        assert MapRequestHandler._endpoint_label("/fleet/truth") == "/fleet/truth"

    def test_truth_page_is_not_heavy_gated(self):
        """Static file, no collection — gating it would 429 a human
        clicking into the page during a poll spike."""
        from utils._map_fleet import FleetEndpointsMixin
        method = getattr(FleetEndpointsMixin, "_serve_fleet_truth_page")
        assert not hasattr(method, "__wrapped__")


class TestLegacyFleetRouteUntouched:
    """The port is ADDITIVE: /fleet keeps serving the legacy Fleet Monitor."""

    def test_dispatch_routes_fleet_to_legacy_dashboard(self):
        h = _make_handler()
        h.path = "/fleet"
        h._serve_fleet_dashboard = MagicMock()
        h._serve_fleet_truth_page = MagicMock()
        h._dispatch_get()
        h._serve_fleet_dashboard.assert_called_once()
        h._serve_fleet_truth_page.assert_not_called()

    def test_legacy_page_file_is_distinct(self):
        assert LEGACY_HTML.exists(), "legacy web/fleet.html missing"
        legacy = LEGACY_HTML.read_text(encoding="utf-8")
        assert "Fleet Truth — MeshAnchor NOC" not in legacy


class TestFleetTruthPageHonestyContract:
    def test_reads_only_the_truth_endpoint(self, page_source):
        """The visual must never re-derive: every fetch() targets the SSOT."""
        fetches = re.findall(r"fetch\(\s*([A-Za-z_'\"/ .]+?)[,)]", page_source)
        assert fetches, "page must fetch the truth endpoint"
        for target in fetches:
            assert target.strip() in ("TRUTH_URL", "'/api/fleet/truth'"), (
                f"unexpected fetch target: {target!r}"
            )
        assert page_source.count("/api/fleet/truth") >= 1

    def test_state_mapping_is_default_dark(self, page_source):
        """stClass: only exact 'healthy'/'failed' escape dark. An unknown or
        undefined state falls through to 'dark' — never green."""
        m = re.search(
            r"function stClass\(s\)\s*\{(.*?)\}", page_source, re.DOTALL)
        assert m, "stClass function missing"
        body = m.group(1)
        assert "if (s === 'healthy') return 'healthy';" in body
        assert "if (s === 'failed') return 'failed';" in body
        assert re.search(r"return 'dark';\s*$", body.strip()), (
            "stClass must END by returning 'dark' for everything else"
        )
        returns = re.findall(r"return\s+'(\w+)'", body)
        assert returns == ["healthy", "failed", "dark"]

    def test_no_raw_ip_in_source(self, page_source):
        """MF014/MF015 money test: no dotted-quad anywhere in the page."""
        hits = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", page_source)
        assert hits == [], f"raw IP-like literals in fleet_truth.html: {hits}"

    def test_self_contained_no_external_resources(self, page_source):
        assert "<script src=" not in page_source
        assert "<link " not in page_source
        assert "https://" not in page_source and "http://" not in page_source

    def test_legend_names_dark_as_first_class(self, page_source):
        assert "DARK" in page_source
        assert "no data never reads green" in page_source

    def test_fetch_failure_renders_dark_not_held_green(self, page_source):
        """The page itself obeys honest_failure_modes #2: a truth-endpoint
        fetch failure flips the verdict DARK with the failure named."""
        assert "renderFetchFailure" in page_source
        m = re.search(r"function renderFetchFailure\([^)]*\)\s*\{(.*?)\n    \}",
                      page_source, re.DOTALL)
        assert m, "renderFetchFailure missing"
        assert "'state dark'" in m.group(1)
        assert "unreachable" in m.group(1)
