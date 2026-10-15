"""requirements/meshcore.txt must PIN meshcore, and pin it to CI's version.

2026-10-14: `meshcore-cli>=1.6.4` floated meshcore to whatever PyPI had
newest. 1.6.6 requires meshcore>=2.3.16, whose PEP 701 f-strings are a
SyntaxError at import below Python 3.12 — a fresh install on the 3.10/3.11
floor got a library that cannot load. The file's own comment claimed the CLI
"depends on meshcore>=2.3.7"; in fact every CLI >= 1.6.4 needs >= 2.3.11, so
the file could NEVER install the 2.3.7 the channel_idx wire fix was verified
against (meshanchor-server's daemon imports 2.3.7 from the user site).

One constant, two consumers (honest_failure_modes #5): CI installs meshcore
by name in .github/workflows/ci.yml, the installers read this file. Both
must name the same exact version or CI measures a library no box runs.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements" / "meshcore.txt"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _requirement_lines():
    return [
        ln.split("#", 1)[0].strip()
        for ln in REQ.read_text().splitlines()
        if ln.split("#", 1)[0].strip()
    ]


def _exact_pin(name):
    for ln in _requirement_lines():
        m = re.fullmatch(rf"{re.escape(name)}\s*==\s*([0-9][0-9A-Za-z.+-]*)", ln)
        if m:
            return m.group(1)
    return None


def test_meshcore_is_exactly_pinned():
    assert _exact_pin("meshcore"), (
        "requirements/meshcore.txt must carry `meshcore==<ver>` — a range "
        "(directly or via meshcore-cli's own floor) floats to PyPI's newest"
    )


def test_meshcore_cli_is_exactly_pinned():
    # A ranged CLI drags meshcore up with it (1.6.6 -> meshcore>=2.3.16);
    # with meshcore pinned, that is ResolutionImpossible, not a quiet float.
    assert _exact_pin("meshcore-cli"), (
        "requirements/meshcore.txt must carry `meshcore-cli==<ver>` whose "
        "own meshcore floor admits the pinned meshcore"
    )


def test_no_unpinned_meshcore_requirement_remains():
    for ln in _requirement_lines():
        if re.match(r"meshcore(-cli)?\b", ln):
            assert "==" in ln, f"unpinned meshcore requirement: {ln!r}"


def test_ci_installs_the_same_meshcore_as_the_requirements_file():
    pinned = _exact_pin("meshcore")
    ci_pins = re.findall(r"meshcore==([0-9][0-9A-Za-z.+-]*)", CI.read_text())
    assert ci_pins, "ci.yml no longer pins meshcore — it will float again"
    assert set(ci_pins) == {pinned}, (
        f"ci.yml installs meshcore {sorted(set(ci_pins))} but "
        f"requirements/meshcore.txt pins {pinned}"
    )
