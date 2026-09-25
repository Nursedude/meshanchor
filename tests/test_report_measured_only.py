"""The status report may only score, or recommend from, what was measured
(2026-09-25, port of MeshForge's 09-22/09-25 fixes): an empty health scorer
rendered its DEFAULTS as "65/100 (fair)" and then recommended "Network health
is degraded" from them."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(HERE, "..", "src") not in sys.path:
    sys.path.insert(0, os.path.join(HERE, "..", "src"))

import utils.report_generator as rg  # noqa: E402


def _scorer(nodes, services, score=65.0):
    snap = SimpleNamespace(overall_score=score, status="fair", node_count=nodes,
                           service_count=services,
                           category_scores={"connectivity": 30.0, "reliability": 100.0})
    return SimpleNamespace(get_snapshot=lambda: snap, get_trend=lambda: "stable")


def _sections(scorer):
    gen = rg.ReportGenerator()
    with patch.object(rg, "_HAS_HEALTH_SCORE", True), \
            patch.object(rg, "_get_health_scorer", lambda: scorer), \
            patch.object(rg, "_get_maintenance_predictor", lambda: None):
        gen._add_health_section()
        gen._add_recommendations_section()
    return "\n".join(s.content for s in gen._sections)


def test_empty_scorer_is_unknown_and_recommends_nothing():
    text = _sections(_scorer(0, 0))
    assert "UNKNOWN" in text and "/100" not in text
    assert "degraded" not in text and "score is low" not in text
    assert "Network is healthy" not in text


def test_a_real_score_still_recommends():
    text = _sections(_scorer(3, 0))
    assert "**Overall Score: 65/100** (fair)" in text
    assert "Network health is degraded" in text
    assert "Connectivity score is low (30/100)" in text
