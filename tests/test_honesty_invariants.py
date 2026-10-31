"""Honest-by-construction invariants — MeshAnchor.

Ported from MeshForge, the lead repo for the honest-dev-env arc
(MeshForge: ``tests/test_honesty_invariants.py``). Each invariant converts a
known blind-spot lesson into a BUILD FAILURE — passive knowledge does not
prevent regression; only enforcement at the moment of action does.

DISCIPLINE (load-bearing): every invariant ships with BOTH a GREEN test (the
repo holds it now) AND a RED test (a deliberately-seeded violation is actually
caught). A guard that cannot be shown to fail is a vacuous false guard. Green
tests also fail loud — never vacuously pass — if their input path moved.

This file starts with the §3b-ii deploy-restart guard (#79). Future ports of
the other honest-dev-env families land here too, keeping parity with MeshForge.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ═════════════════════════════════════════════════════════════════════
# §3b-ii — every long-lived MeshAnchor-code daemon has a deploy-restart hook
# ═════════════════════════════════════════════════════════════════════

# Issue #79 (ported from MeshForge 37fd01f/e1ee1af; MA fix 845269ed): a deploy
# path that copies a unit template + daemon-reloads but never RESTARTS the
# running daemon leaves it on OLD code after a `git pull` until a hand-restart.
# MA's only pull-deploy path is scripts/update.sh (no fleet_sync.sh — that is a
# MeshForge-fleet tool). The invariant: every long-lived daemon whose code lives
# in THIS repo — USER-bus or SYSTEM — is restarted by update.sh after a pull.
#
# CURATED, not a glob (the MeshForge-sanctioned approach): "runs MeshAnchor code"
# is a semantic property of the ExecStart, not a filename. Each entry verified
# 2026-06-15 against the unit's ExecStart. The system daemons were wired into
# update.sh's CODE_CHANGED restart on 2026-06-15 (closing the broader on-pull
# gap that this guard previously only documented).
MESHANCHOR_CODE_DAEMONS: dict = {
    # unit base name → ExecStart provenance + where its restart is wired
    "meshanchor-echo":   "lab.lxmf_echo responder, USER bus (update.sh user-unit try-restart)",
    # The headless NOC daemon (src/daemon.py start --foreground) is the unit
    # ENABLED at boot (deploy_noc.sh ENABLE_UNITS) — NOT meshanchor.service, which
    # deploy_noc.sh leaves never-auto-enabled. update.sh must restart THIS one on a
    # code pull (the 2026-06-20 fix: it previously restarted the on-demand launcher,
    # so the running daemon kept serving stale code — the #79 gap, un-caught because
    # this guard checked the wrong unit too).
    "meshanchor-daemon": "src/daemon.py start --foreground — headless NOC daemon, "
                         "ENABLED at boot, SYSTEM (update.sh CODE_CHANGED restart)",
    "meshanchor-map":    "map daemon, SYSTEM (update.sh CODE_CHANGED restart)",
}

# Long-lived user/forking daemons MeshAnchor installs that a /opt/meshanchor pull
# does NOT change (external binary) OR that are operator-driven interactive tmux
# sessions — restarting them on a code pull is wrong, so they are correctly
# absent from the deploy-restart path.
RESTART_EXEMPT_DAEMONS: dict = {
    "meshanchor":    "src/launcher.py --no-services — the on-demand interactive TUI NOC. "
                     "deploy_noc.sh leaves it NEVER auto-enabled (the long-lived headless "
                     "code daemon is meshanchor-daemon). Auto-restarting it on a pull would "
                     "kill an attached TUI session, and it picks up new code on the next "
                     "manual start anyway — so it is deliberately NOT deploy-restarted",
    "meshcore-chat": "operator-attached interactive tmux pane (utils.chat_client); its "
                     "lifecycle is owned by the chat_pane TUI handler — an auto-restart "
                     "would kill an attached session",
    "nomadnet-tmux": "interactive tmux pane wrapping the external nomadnet app",
    "nomadnet":      "external `nomadnet --daemon` binary; a MeshAnchor pull doesn't change it",
    "meshchatx":     "wraps the external meshchatx app; pull doesn't change it",
    "rnsd":          "runs pip-installed (forked) rnsd, not repo code; restart is "
                     "explicitly dangerous (RNS rapid-cycle @rns race, #69)",
    "meshtasticd-native": "external meshtasticd binary (upstream)",
    "meshtasticd-alt":    "external meshtasticd binary (TUI-deployed secondary radio)",
}

_RESTART_KEYWORDS = ("try-restart", "restart", "sync_repo", "sync_local_unit",
                     "sync_user_unit", "sync_local_user_unit")


def deploy_restarted_units(*script_texts: str) -> set:
    """Set of systemd unit base names a deploy script restarts after a pull. A
    unit is "restart-wired" if its base name appears (as a whole token, not a
    hyphen-prefix of a longer name) on a non-comment line that also contains a
    restart verb — covering `systemctl try-restart <unit>.service` and the
    unit-name-as-arg wrappers. Pure over the passed text so the red proof can
    feed synthetic scripts."""
    found = set()
    candidates = set(MESHANCHOR_CODE_DAEMONS) | set(RESTART_EXEMPT_DAEMONS)
    for text in script_texts:
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#"):
                continue
            if not any(kw in line for kw in _RESTART_KEYWORDS):
                continue
            for unit in candidates:
                if re.search(re.escape(unit) + r"(?![\w-])", line):
                    found.add(unit)
    return found


class TestDeployRestartHook:
    """§3b-ii — the #79 deploy gap: a long-lived daemon running THIS repo's code
    must be restarted by update.sh after a pull, or it silently serves stale
    code. MA's only pull-deploy path is update.sh."""

    def _deploy_sources(self):
        update = REPO / "scripts" / "update.sh"
        assert update.exists(), f"{update} moved — the deploy-restart guard would vacuously pass"
        return (update.read_text(),)

    def test_every_code_daemon_is_deploy_restarted(self):
        restarted = deploy_restarted_units(*self._deploy_sources())
        # Non-vacuity anchor: the known-wired echo daemon MUST be found, else the
        # parser broke against the real script (refuse to pass empty).
        assert "meshanchor-echo" in restarted, (
            "parser found NO meshanchor-echo restart wiring in update.sh — it "
            "broke against the real script, or the #79 fix regressed.")
        missing = set(MESHANCHOR_CODE_DAEMONS) - restarted
        assert not missing, (
            f"long-lived MeshAnchor-code daemon(s) {sorted(missing)} are NOT "
            f"restarted by update.sh after a pull → they serve OLD code until a "
            f"hand-restart (the #79 deploy gap). Wire a try-restart (the user-unit "
            f"block for USER daemons, the CODE_CHANGED block for SYSTEM daemons). "
            f"Provenance: {[MESHANCHOR_CODE_DAEMONS[m] for m in sorted(missing)]}")

    def test_exempt_daemons_are_genuinely_distinct(self):
        """A daemon can't be both 'runs repo code' and 'external/interactive' —
        an overlap would let a real gap hide as exempt."""
        overlap = set(MESHANCHOR_CODE_DAEMONS) & set(RESTART_EXEMPT_DAEMONS)
        assert not overlap, f"daemon(s) {overlap} listed as BOTH code-daemon and exempt"

    def test_red_unrestarted_daemon_is_detected(self):
        """RED proof — a code-daemon whose name is on NO restart line is flagged.
        If this passed, the gate would miss the #79 gap."""
        update = "echo just deploying templates\ncp foo bar\n"  # no restart at all
        restarted = deploy_restarted_units(update)
        assert "meshanchor-echo" not in restarted  # the seeded gap is caught

    def test_red_token_boundary_no_false_match(self):
        """The token guard must not let a longer-named unit satisfy a shorter
        one (or vice versa) — proven on synthetic hyphenated names."""
        line = "run_user_systemctl try-restart nomadnet-tmux.service\n"
        restarted = deploy_restarted_units(line)
        assert "nomadnet-tmux" in restarted
        assert "nomadnet" not in restarted  # 'nomadnet' must not match 'nomadnet-tmux'

    def test_red_comment_line_is_not_a_wiring(self):
        """A restart verb inside a comment must NOT count as wiring."""
        commented = "# TODO: try-restart meshanchor-echo.service someday\n"
        assert "meshanchor-echo" not in deploy_restarted_units(commented)


# ═════════════════════════════════════════════════════════════════════
# Operator login must not be resolved from advisory env alone
# ═════════════════════════════════════════════════════════════════════

def test_no_script_resolves_the_operator_from_advisory_env_alone():
    """Ported from MeshForge 2026-09-10 (lead repo for this class).

    `REAL_USER="${SUDO_USER:-$USER}"` looks total and is not: a root cron or a
    systemd unit sets NEITHER var, so it yields "" and `eval echo "~"` then
    expands to the INVOKING user's home. The user units get written there, the
    chown runs as ":", every error is swallowed by `2>/dev/null || true`, and
    the script prints "✓ User service templates deployed" — a false success
    claim built on a degraded value that looked valid (honest_failure_modes #1).

    MeshForge found this when its 04:45 calibration_reverify cron — an env with
    no $USER — marked a verified head "broke". MeshAnchor has no dry-run test
    harness to surface it the same way, which is exactly why the guard is
    ported rather than waiting for the symptom to show up here.

    The bare two-level form is the defect signature; longer chains ending in a
    real resolution (`$(id -un)`, `$(whoami)`) are the fix, so this greps for
    the bare form rather than for the variable names.
    """
    import re
    offenders = []
    for path in sorted((REPO / "scripts").glob("*.sh")):
        for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\$\{SUDO_USER:-\$USER\}", ln):
                offenders.append(f"{path.name}:{n}: {ln.strip()}")
    assert not offenders, (
        "operator login resolved from advisory env alone — $USER and $SUDO_USER "
        "are both unset under cron and systemd. End the chain in the real uid "
        "(${SUDO_USER:-${USER:-$(id -un)}}) and handle the no-operator case "
        "explicitly:\n  " + "\n  ".join(offenders))


def test_red_bare_advisory_form_is_detected():
    """RED proof — the guard's pattern must actually fire on the pre-fix text.
    Without this, a grep that silently matches nothing reads identical to a
    clean tree (a guard that has never failed is not evidence)."""
    import re
    pre_fix = 'REAL_USER="${SUDO_USER:-$USER}"'
    post_fix = 'REAL_USER="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || true)}}"'
    pat = re.compile(r"\$\{SUDO_USER:-\$USER\}")
    assert pat.search(pre_fix), "the pattern must match the defect it exists for"
    assert not pat.search(post_fix), "the fixed form must not be flagged"


# ═════════════════════════════════════════════════════════════════════
# Step 4 — no false operator-facing instructions (dead AREDN sysinfo URL)
# ═════════════════════════════════════════════════════════════════════

# Ported from MeshForge ``tests/test_honesty_invariants.py`` (Step 4). AREDN
# 4.x retired the old node API path: a GET to ``:8080/cgi-bin/sysinfo.json``
# answers HTTP 307 → ``:8080/a/sysinfo`` (re-observed live 2026-10-31 on
# AREDN 4.26.10.0: a non-redirect-following client gets an EMPTY body).
# MA's AREDNClient already used ``/a/sysinfo``, but the operator-FACING text
# (KB entries + the TUI troubleshooting hint) still pointed at the dead path —
# a half-port of MeshForge 243d8a9. Instructing the operator toward a
# redirecting/dead endpoint is a false instruction (MF018).
_DEAD_AREDN_PATH = "cgi-bin/sysinfo.json"
_SRC = REPO / "src"


def _src_files_referencing(needle: str) -> dict:
    hits = {}
    for path in _SRC.rglob("*.py"):
        n = path.read_text(encoding="utf-8", errors="replace").count(needle)
        if n:
            hits[str(path.relative_to(_SRC))] = n
    return hits


class TestNoDeadArednSysinfoUrl:
    """No source string points the operator at AREDN's retired
    ``/cgi-bin/sysinfo.json`` (307 → ``/a/sysinfo``)."""

    # Intentional references (e.g. a migration note) go here WITH a reason.
    ALLOWED: set = set()

    def test_src_tree_present(self):
        """Fail loud, never vacuously pass, if the scanned tree moved."""
        assert any(_SRC.rglob("*.py")), f"no .py files under {_SRC}"

    def test_no_dead_aredn_url_in_src(self):
        hits = {f: n for f, n in _src_files_referencing(_DEAD_AREDN_PATH).items()
                if f not in self.ALLOWED}
        assert not hits, (
            f"deprecated AREDN path {_DEAD_AREDN_PATH!r} (HTTP 307 → /a/sysinfo) "
            f"still referenced in: {hits}. Point the operator at "
            f"'/a/sysinfo' (add '?hosts=1' for the node/host list) — instructing "
            f"them toward a redirecting/dead endpoint is a false instruction (MF018).")

    def test_red_checker_detects_the_dead_path(self):
        """RED proof — the substring scanner really flags the dead path."""
        sample = "see http://node:8080/cgi-bin/sysinfo.json for details"
        assert sample.count(_DEAD_AREDN_PATH) == 1
        assert "ok /a/sysinfo only".count(_DEAD_AREDN_PATH) == 0
