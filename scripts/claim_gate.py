#!/usr/bin/env python3
"""claim_gate.py — the calibrated-claims reflective Stop hook.

Ported from MeshForge's calibration spine 2026-06-15 (the rule + gate; the
MeshForge-only calibration ledger/watchdog is NOT ported here). Born from the
operator's concern: *"honesty is not enough when honesty is a house of cards…
when you say 100%% and we do it N more times the math is wrong."*
`.claude/rules/calibrated_claims.md` is the write-time discipline; THIS is the
harness-level enforcement that survives a model swap, because it lives here and
not in the model's disposition.

What it does (and deliberately does NOT do):
  * On a Stop, it reads my last assistant message. If that message makes an
    unqualified completion claim ("all green / 100%% / all tests pass / fully
    verified / deployed successfully …") with NO evidence tag, it BLOCKS ONCE
    and injects a pointer to the rule + the checks of record. One reflective beat.
  * It is NOT a cage. `stop_hook_active` guarantees at most one block per stop
    sequence — after the reflective beat my judgment stands. A message that is
    already calibrated (tags VERIFIED/BELIEVED/UNKNOWN, quotes a real exit code,
    or hedges honestly) passes untouched. Enforcement = truth-injection, not
    speech-prohibition.

FAIL-OPEN, ALWAYS. A bug in this gate must never wedge a session: every
unexpected error is swallowed into a silent pass (exit 0) with a stderr witness.
A gate that wedges sessions gets ripped out and protects nothing.

MeshAnchor has no honest_status.sh verdict marker (a MeshForge-side artifact), so
the marker leg here is inert by default and the gate runs on the text-calibration
path — the marker code is kept (env-overridable) so it future-proofs if MA grows
its own verifier. Stdlib only. Pure core (`evaluate`) is unit-tested (RED + GREEN).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Repo root (this file lives in <repo>/scripts/).
REPO = Path(__file__).resolve().parent.parent

# A verified marker (if MA ever grows a verifier that writes one) is honored only
# when fresh AND covering the current HEAD AND a full run. Same HEAD = same
# committed code; the age cap bounds the working-tree-drift window.
MARKER_MAX_AGE_S = 2 * 60 * 60  # 2h

# Unqualified completion claims that have burned the operator. Lowercased
# substring match. Deliberately CONSERVATIVE — bare "done"/"fixed" are excluded
# (too common in honest use); we fire only on certainty-of-completion phrasing.
STRONG_CLAIMS = (
    "all green", "everything is green", "all checks pass", "all checks passing",
    "fully verified", "fully tested", "verified green",
    "100%", "all tests pass", "all tests passing", "tests all pass",
    "all passing", "everything works", "everything is working",
    "everything passes", "it works now", "works perfectly",
    "successfully deployed", "deployed successfully", "deploy succeeded",
    "confirmed working", "guaranteed to work", "definitely works",
    "definitely fixed",
)

# If ANY of these appear in the message, it is already calibrated → pass. The
# autonomy-preserving escape: honest hedging and quoted evidence always pass.
CALIBRATION_MARKERS = (
    "verified", "believed", "unverified", "untested", "unknown",
    "not verified", "haven't verified", "have not verified", "not yet verified",
    "not tested", "needs verification", "need to verify", "should work",
    "i believe", "i think this", "can't verify", "cannot verify",
    "couldn't verify", "unable to verify", "not been verified",
)

# Quoted real exit codes / results = evidence → calibrated.
EVIDENCE_PATTERNS = (
    r"exit\s*(?:code\s*)?\d",
    r"\bEXIT=\d",
    r"\brc=\d",
    r"return\s*code",
    r"\(exit\s*\d",
    r"_EXIT=\d",
    r"exit\s*status\s*\d",
)


def extract_last_assistant_text(transcript_lines) -> str:
    """Concatenated text of the LAST assistant message in a JSONL transcript.

    Defensive: skips unparseable lines, tolerates both the
    ``{"type":"assistant","message":{"content":[…]}}`` shape and a bare
    ``{"role":"assistant","content":[…]}``. Returns "" if none found."""
    last_text = ""
    for line in transcript_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = msg.get("role") or obj.get("type")
        if role != "assistant":
            continue
        content = msg.get("content")
        parts = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block)
        if parts:
            last_text = "\n".join(parts)
    return last_text


def has_strong_claim(text: str) -> bool:
    low = text.lower()
    return any(claim in low for claim in STRONG_CLAIMS)


def is_calibrated(text: str) -> bool:
    """True if the message already shows evidence or honest hedging — the
    autonomy-preserving exempt path."""
    low = text.lower()
    if any(m in low for m in CALIBRATION_MARKERS):
        return True
    return any(re.search(p, text, re.IGNORECASE) for p in EVIDENCE_PATTERNS)


def marker_satisfies(marker, head_full, now_ts, max_age_s=MARKER_MAX_AGE_S) -> bool:
    """A fresh, full, green verdict marker covering the current HEAD backs a
    completion claim. Inert on MeshAnchor until/unless a verifier writes one."""
    if not isinstance(marker, dict) or not head_full:
        return False
    if marker.get("head_full") != head_full:
        return False
    if marker.get("exit_code") != 0:
        return False
    if not marker.get("ran_full_suite"):
        return False
    ts = marker.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    age = now_ts - ts
    return 0 <= age <= max_age_s


def evaluate(text, head_full, marker, now_ts):
    """Pure decision core. Returns (block: bool, reason: str|None).

    block=True only when the message makes an unqualified strong completion
    claim AND it is neither already-calibrated NOR backed by a fresh verdict
    marker. Everything else passes."""
    if not text or not has_strong_claim(text):
        return False, None
    if is_calibrated(text):
        return False, None
    if marker_satisfies(marker, head_full, now_ts):
        return False, None

    if isinstance(marker, dict) and marker.get("summary"):
        marker_line = (f"Latest verification verdict: {marker.get('summary')} "
                       f"(HEAD {str(marker.get('head_full',''))[:7]}, "
                       f"exit {marker.get('exit_code')}).")
    else:
        marker_line = "No verification verdict on record this turn."

    reason = (
        "CALIBRATED-CLAIMS CHECK (.claude/rules/calibrated_claims.md): your "
        "closing message makes an unqualified completion claim, but this turn "
        "ran no external check you quoted.\n"
        f"{marker_line}\n\n"
        "This is one reflective beat, not a cage — reconcile your wording with "
        "the evidence, then your judgment stands:\n"
        "  • If it IS verified: run `python3 -m pytest tests/ -q` + `python3 "
        "scripts/lint.py --all`, quote the real captured exit code, and tag the "
        "claim VERIFIED.\n"
        "  • If it is NOT yet verified: say so — tag it BELIEVED (written, not "
        "run) or UNKNOWN (couldn't check), and name the check that would confirm "
        "it. Unobservable is never 'healthy'; 'worked once' is not 'reliable'.\n"
        "Then finish."
    )
    return True, reason


# ---------------------------------------------------------------------------
# I/O wrapper — everything here fails OPEN (exit 0) on any error.
# ---------------------------------------------------------------------------


def _current_head():
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    h = out.stdout.strip()
    return h or None


def _marker_path():
    env = os.environ.get("HONEST_VERDICT_PATH")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".cache", "meshanchor", "honest_verdict.json")


def _read_marker():
    try:
        with open(_marker_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    # 1. Parse the Stop-hook stdin envelope. Empty/garbage → pass.
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0  # fail-open
    if not isinstance(data, dict):
        return 0

    # 2. Loop guard — the one-beat guarantee. If we already blocked once, pass.
    if data.get("stop_hook_active"):
        return 0

    # 3. Read the last assistant message. Unreadable transcript → pass.
    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return 0
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"claim_gate: WARN — transcript unreadable ({e}); passing open",
              file=sys.stderr)
        return 0

    try:
        text = extract_last_assistant_text(lines)
        head = _current_head()
        marker = _read_marker()
        block, reason = evaluate(text, head, marker, time.time())
    except Exception as e:  # noqa: BLE001 — fail-open is the whole contract
        print(f"claim_gate: WARN — internal error ({e!r}); passing open",
              file=sys.stderr)
        return 0

    if block:
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
