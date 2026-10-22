"""MeshAnchor's ma_refresh_installed_units never replaces a unit that runs a
DIFFERENT PROGRAM under the same name (2026-10-21: the launcher's unit over
the running orchestrator's crash-looped meshanchor.service 2,172 times).
Runs the REAL scripts/lib/unit_render.sh function against a sandbox."""
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
ORCH = "[Service]\nExecStart=/opt/meshanchor/venv/bin/python -m core.orchestrator --start\n"
LAUNCHER = "[Service]\nExecStart=/usr/bin/python3 /opt/meshanchor/src/launcher.py --no-services\n"


def _run(tmp_path, installed, template):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src" / "utils").mkdir(parents=True)
    (root / "src" / "utils" / "__init__.py").write_text("")
    shutil.copy(REPO / "src" / "utils" / "user_unit_templates.py", root / "src" / "utils")
    (root / "scripts" / "meshanchor.service").write_text(template)
    dst = tmp_path / "etc"
    dst.mkdir()
    (dst / "meshanchor.service").write_text(installed)
    script = (f'set -e\n. "{REPO}/scripts/lib/unit_render.sh"\n'
              f'ma_refresh_installed_units "{root}/scripts" "{dst}" meshanchor.service || true\n'
              'echo BLOCK_DONE\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    return r, (dst / "meshanchor.service").read_text()


def test_the_launcher_never_replaces_the_running_orchestrator(tmp_path):
    r, now = _run(tmp_path, ORCH, LAUNCHER)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert now == ORCH
    assert "NOT replaced" in r.stdout and "core.orchestrator" in r.stdout


def test_a_same_program_refresh_still_lands(tmp_path):
    newer = ORCH.replace("--start", "--start --monitor")
    r, now = _run(tmp_path, ORCH, newer)
    assert "BLOCK_DONE" in r.stdout, r.stderr
    assert now == newer
