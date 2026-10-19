"""verify_post_install.sh on a MeshCore-only box (meshtasticd is opt-in since
2026-10-18): a deliberate absence must read SKIP, never warn/fail — and the
--json report must be valid JSON (it was not whenever a fix hint contained a
double quote). Runs the REAL verifier; probe paths are pointed at nothing to
model a clean MeshCore-only box on a machine that has meshtasticd.
"""
import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
VERIFY = _ROOT / "scripts" / "verify_post_install.sh"


def _run(tmp_path, **probes):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in probes.items()})
    r = subprocess.run(["bash", str(VERIFY), "--json"], capture_output=True,
                       text=True, timeout=180, env=env, cwd=str(tmp_path))
    assert r.stdout.strip(), f"verifier printed nothing (rc={r.returncode}): {r.stderr[-800:]}"
    return json.loads(r.stdout)  # must parse: the report is a contract


def _results(doc):
    return doc.get("results") or doc.get("checks") or []


ABSENT = dict(MTD_PROBE_BIN="nope_mtd_bin", MTD_PROBE_CLI="nope_mtd_cli",
              MTD_PROBE_UNIT="/nonexistent/meshtasticd.service",
              MTD_PROBE_CONFIG_DIR="/nonexistent/meshtasticd",
              MC_PROBE_UDEV_RULE="/nonexistent/99-meshcore.rules")


def test_meshcore_only_box_skips_meshtasticd_never_warns(tmp_path):
    res = _results(_run(tmp_path, **ABSENT))
    assert res, "verifier produced no results"
    assert any(r["check"] == "meshtasticd" and r["status"] == "skip"
               for r in res)
    bad = [r for r in res if r["status"] in ("warn", "fail") and (
        "meshtasticd" in r["check"].lower() or r["check"] in (
            "Port 4403 (TCP)", "Config directory", "meshtastic CLI",
            "udev rules", "MeshCore udev rule"))]
    assert not bad, bad
    mc = [r for r in res if r["check"] == "MeshCore udev rule"]
    assert mc and mc[0]["status"] in ("pass", "info")


def _present(tmp_path, **probes):
    """Run the verifier's REAL presence decision in isolation (the full
    verifier would EXECUTE meshtasticd on a present box — pytest's host
    fence forbids that, by design)."""
    lines = VERIFY.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("meshtasticd_present() {"))
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "}")
    script = "\n".join(lines[i:j + 1]) + "\nmeshtasticd_present && echo yes || echo no\n"
    env = dict(os.environ)
    env.update({k: str(v) for k, v in probes.items()})
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=30, env=env)
    return r.stdout.strip()


def test_presence_is_measured_from_any_trace_of_meshtasticd(tmp_path):
    probe = dict(ABSENT)
    assert _present(tmp_path, **probe) == "no"
    assert _present(tmp_path, **dict(probe, MTD_PROBE_CONFIG_DIR=tmp_path)) == "yes"
    unit = tmp_path / "meshtasticd.service"
    unit.write_text("[Unit]\n")
    assert _present(tmp_path, **dict(probe, MTD_PROBE_UNIT=unit)) == "yes"
    # the pip CLI alone is a client tool, not meshtasticd (review F5)
    assert _present(tmp_path, **dict(probe, MTD_PROBE_CLI="bash")) == "no"
    assert _present(tmp_path, **dict(probe, MTD_PROBE_BIN="bash")) == "yes"


def test_json_escape_handles_cr_and_control_bytes(tmp_path):
    lines = VERIFY.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("_json_esc() {"))
    j = next(n for n in range(i + 1, len(lines)) if lines[n] == "}")
    fn = "\n".join(lines[i:j + 1]) + "\n"
    script = fn + 'printf \'{"v":"%s"}\' "$(_json_esc "$1")"\n'
    raw = 'a"b\\c\r\nd\te\x01f'
    r = subprocess.run(["bash", "-c", script, "x", raw], capture_output=True,
                       text=True, timeout=30)
    assert json.loads(r.stdout)["v"] == 'a"b\\c\r\nd\tef'
