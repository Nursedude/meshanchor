"""MeshAnchor is MeshCore-primary; its installer must act like it (domain
audit 2026-10-18, measured on install_noc.sh: INSTALL_MESHTASTICD=true by
default, 129 meshtasticd mentions, 0 MeshCore, and the shipped
scripts/99-meshcore.rules installed by nothing).

Behavior, not text: the real argument parser and the real helper functions
are extracted from the installer and run in bash.
"""
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
INSTALL = _ROOT / "scripts" / "install_noc.sh"


def _block(start: str, end_re: str) -> str:
    lines = INSTALL.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith(start))
    j = next(n for n in range(i + 1, len(lines)) if re.match(end_re, lines[n]))
    return "\n".join(lines[i:j + 1]) + "\n"


def _parse(*args) -> str:
    defaults = _block("# Defaults", r"^INSTALL_RNS=")
    parser = _block("# Parse arguments", r"^done$")
    script = ("RED=''; YELLOW=''; GREEN=''; CYAN=''; BOLD=''; NC=''\n"
              + defaults + parser + 'echo "MTD=$INSTALL_MESHTASTICD"\n')
    r = subprocess.run(["bash", "-c", script, "x", *args],
                       capture_output=True, text=True, timeout=30)
    return r.stdout + r.stderr


def test_meshtasticd_flags_parse():
    assert "MTD=auto" in _parse()                        # resolved after detection
    assert "MTD=true" in _parse("--with-meshtasticd")
    assert "MTD=false" in _parse("--skip-meshtasticd")   # still accepted
    # a meshtasticd MODE implies meshtasticd (they were silent no-ops)
    assert "MTD=true" in _parse("--force-native")
    assert "MTD=true" in _parse("--force-python")


def _run_fn(name: str, body: str, *args) -> subprocess.CompletedProcess:
    fn = _block(f"{name}() {{", r"^}$")
    script = ("RED=''; YELLOW=''; GREEN=''; CYAN=''; NC=''\n" + fn
              + body)
    return subprocess.run(["bash", "-c", script, "x", *args],
                          capture_output=True, text=True, timeout=30)


def test_running_from_another_checkout_says_so(tmp_path):
    other = tmp_path / "home-clone"
    other.mkdir()
    inst = tmp_path / "opt-meshanchor"
    inst.mkdir()
    call = 'ma_warn_foreign_checkout "$1" "$2"\n'
    r = _run_fn("ma_warn_foreign_checkout", call, str(other), str(inst))
    assert "NOT the code that gets installed" in r.stdout
    same = _run_fn("ma_warn_foreign_checkout", call, str(inst), str(inst))
    assert same.stdout.strip() == ""


def test_auto_keeps_an_existing_meshtasticd_in_scope():
    """Review F1: a re-run on a box that already runs meshtasticd must not
    silently demote it to unmanaged; a fresh box skips it."""
    call = 'ma_resolve_meshtasticd "$1" "$2"\n'
    def res(choice, exists):
        return _run_fn("ma_resolve_meshtasticd", call, choice, exists).stdout.strip()
    assert res("auto", "true") == "true"
    assert res("auto", "false") == "false"
    # a deliberately stopped+disabled / masked meshtasticd is left alone
    assert _run_fn("ma_resolve_meshtasticd", 'ma_resolve_meshtasticd "$1" "$2" "$3"\n',
                   "auto", "true", "disabled").stdout.strip() == "false"
    assert _run_fn("ma_resolve_meshtasticd", 'ma_resolve_meshtasticd "$1" "$2" "$3"\n',
                   "auto", "true", "masked").stdout.strip() == "false"
    assert res("false", "true") == "false"   # explicit --skip-meshtasticd wins
    assert res("true", "false") == "true"    # explicit --with-meshtasticd wins


def test_the_generic_meshcore_udev_rule_is_not_installed_by_default():
    """Review F2: scripts/99-meshcore.rules matches generic CP210x / CH340 /
    ESP32-S3 serial chips — installed blindly it would claim a Meshtastic
    node or an RNode as /dev/ttyMeshCore."""
    text = INSTALL.read_text()
    # any command that would put the rule in place (round-1's function built
    # the path from a variable, so a literal-path check passed against it)
    assert re.search(r"^\s*[^#\n]*\b(install|cp|ln)\b[^\n]*99-meshcore\.rules",
                     text, re.M) is None
    assert "ma_install_meshcore_udev" not in text
