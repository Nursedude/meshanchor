"""MeshChatX never starts until rnsd OWNS the shared-instance socket (#69).

2026-10-26, meshanchor-server reboot: the lingering meshchatx user unit
started at 22:40:12, rnsd (system unit, behind its IPv6 gate) at 22:40:17.09,
and MeshChatX reached its RNS stage at 22:40:17.72 — before rnsd had bound
``@rns/default`` — so stock RNS inside MeshChatX became the shared-instance
host and rnsd ran as its client. The unit's ``After=rnsd.service`` cannot
help: a USER manager cannot order against a SYSTEM unit.

The wrapper therefore waits for an OWNER, not a socket (MeshForge cfad33b2):
present-but-squatted keeps waiting; a deliberate rnsd absence (not installed,
or disabled AND stopped) is the operator's call and skips the wait loudly.
Runs the real wrapper with stub ``ss`` / ``systemctl`` on PATH, so no test
reads this box's live sockets or units.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "templates" / "python" / "meshchatx_wrapper.sh"

RNSD_UNIT = ("LoadState=loaded\nUnitFileState=enabled\n"
             "ActiveState=active\nMainPID=2505\n")
OWNED = ('u_str LISTEN 0 0 @rns/default 16074 * 0 '
         'users:(("rnsd",pid=2505,fd=4))\n'
         'u_str LISTEN 0 0 @rns/default/rpc 16129 * 0 '
         'users:(("rnsd",pid=2505,fd=9))\n')
SQUATTED = ('u_str LISTEN 0 0 @rns/default 16074 * 0 '
            'users:(("python3",pid=909,fd=7))\n')
INVISIBLE = 'u_str LISTEN 0 0 @rns/default 16074 * 0\n'


def write_stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)


def run_wrapper(tmp_path, ss_out=OWNED, unit=RNSD_UNIT, cfg_extra="",
                wait_s="2", ss_after=None):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "meshanchor").mkdir(parents=True, exist_ok=True)
    write_stub(home / ".local" / "bin", "meshchatx", 'printf "%s\\n" "$@"\n')
    cfg = tmp_path / "rns_config"
    cfg.write_text("[reticulum]\n  rpc_key = " + "a" * 64 + "\n" + cfg_extra)
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    (tmp_path / "ss.out").write_text(ss_out)
    # ss_after: what ss reports from the second poll on (rnsd arriving late).
    if ss_after is not None:
        (tmp_path / "ss.after").write_text(ss_after)
        write_stub(stubs, "ss",
                   f'n=$(cat {tmp_path}/ss.n 2>/dev/null || echo 0)\n'
                   f'echo $((n+1)) > {tmp_path}/ss.n\n'
                   f'if [ "$n" -ge 1 ]; then cat {tmp_path}/ss.after; '
                   f'else cat {tmp_path}/ss.out; fi\n')
    else:
        write_stub(stubs, "ss", f"cat {tmp_path}/ss.out\n")
    # The system manager answers `unit`; the USER manager knows no rnsd —
    # except under __USER_SCOPE__, where rnsd exists only as a user unit.
    user_unit = "LoadState=not-found\n"
    if unit == "__USER_SCOPE__":
        unit = "LoadState=not-found\n"
        user_unit = ("LoadState=loaded\nUnitFileState=enabled\n"
                     "ActiveState=inactive\nMainPID=0\n")
    (tmp_path / "unit.out").write_text(unit)
    (tmp_path / "user_unit.out").write_text(user_unit)
    write_stub(stubs, "systemctl",
               f'if [[ " $* " == *" --user "* ]]; then '
               f'cat {tmp_path}/user_unit.out; '
               f'else cat {tmp_path}/unit.out; fi\n')
    script = tmp_path / "wrapper.sh"
    script.write_text(WRAPPER.read_text().replace("/etc/reticulum/config",
                                                  str(cfg)))
    env = {"HOME": str(home),
           "PATH": f"{stubs}:" + os.environ.get("PATH", "/usr/bin:/bin"),
           "MESHANCHOR_RNSD_WAIT_S": wait_s}
    return subprocess.run(["bash", str(script)], env=env, capture_output=True,
                          text=True, timeout=30)


def started(r) -> bool:
    return "--headless" in r.stdout


def test_rnsd_owns_the_socket_so_meshchatx_starts(tmp_path):
    r = run_wrapper(tmp_path)
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_a_squatter_holds_the_socket_so_meshchatx_refuses(tmp_path):
    r = run_wrapper(tmp_path, ss_out=SQUATTED)
    assert r.returncode == 75, r.stderr
    assert not started(r)
    assert "python3" in r.stderr          # names the squatter
    assert "ss -xnlp" in r.stderr         # says what to do


def test_no_socket_yet_refuses_after_the_budget(tmp_path):
    r = run_wrapper(tmp_path, ss_out="")
    assert r.returncode == 75, r.stderr
    assert not started(r)


def test_rnsd_arriving_late_is_waited_for(tmp_path):
    """The 10-26 boot: nothing bound at first, rnsd binds a moment later."""
    r = run_wrapper(tmp_path, ss_out="", ss_after=OWNED, wait_s="5")
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_rnsd_not_installed_skips_the_wait_and_says_so(tmp_path):
    r = run_wrapper(tmp_path, ss_out="",
                    unit="LoadState=not-found\nUnitFileState=\n"
                         "ActiveState=inactive\nMainPID=0\n")
    assert r.returncode == 0, r.stderr
    assert started(r)
    assert "not installed" in r.stderr


def test_rnsd_deliberately_stopped_skips_the_wait_and_says_so(tmp_path):
    r = run_wrapper(tmp_path, ss_out="",
                    unit="LoadState=loaded\nUnitFileState=disabled\n"
                         "ActiveState=inactive\nMainPID=0\n")
    assert r.returncode == 0, r.stderr
    assert started(r)
    assert "disabled" in r.stderr


def test_rnsd_enabled_but_down_is_waited_for_not_skipped(tmp_path):
    """Enabled-but-inactive is the boot window itself, never a skip."""
    r = run_wrapper(tmp_path, ss_out="",
                    unit="LoadState=loaded\nUnitFileState=enabled\n"
                         "ActiveState=activating\nMainPID=0\n")
    assert r.returncode == 75, r.stderr
    assert not started(r)


def test_owner_invisible_accepts_only_an_active_rnsd_and_says_so(tmp_path):
    r = run_wrapper(tmp_path, ss_out=INVISIBLE)
    assert r.returncode == 0, r.stderr
    assert started(r)
    assert "not visible" in r.stderr


def test_owner_invisible_with_rnsd_inactive_keeps_waiting(tmp_path):
    r = run_wrapper(tmp_path, ss_out=INVISIBLE,
                    unit="LoadState=loaded\nUnitFileState=enabled\n"
                         "ActiveState=activating\nMainPID=0\n")
    assert r.returncode == 75, r.stderr
    assert not started(r)


def test_instance_name_comes_from_the_config(tmp_path):
    other = OWNED.replace("@rns/default", "@rns/mf")
    r = run_wrapper(tmp_path, ss_out=other,
                    cfg_extra="  instance_name = mf\n")
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_another_instance_owned_by_rnsd_does_not_satisfy_ours(tmp_path):
    other = OWNED.replace("@rns/default", "@rns/mf")
    r = run_wrapper(tmp_path, ss_out=other)
    assert r.returncode == 75, r.stderr
    assert not started(r)


def test_owner_matched_by_rnsd_mainpid_not_only_name(tmp_path):
    """A venv rnsd can show comm 'python3'; MainPID is the measured owner."""
    by_pid = OWNED.replace('"rnsd"', '"python3"')
    r = run_wrapper(tmp_path, ss_out=by_pid)
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_a_pull_refreshes_an_installed_wrapper_without_restarting_it():
    """The cure must reach the box: install_meshchatx.sh is TUI-driven, so
    update.sh refreshes the wrapper where installed, never starts or
    restarts meshchatx (a deploy never changes which services run)."""
    text = (REPO_ROOT / "scripts" / "update.sh").read_text()
    block = text[text.index('mcx_src="$INSTALL_DIR/templates/python/'
                            'meshchatx_wrapper.sh"'):]
    block = block[:block.index("chown -R")]
    assert '[[ -f "$mcx_dst"' in block            # only where installed
    assert "cmp -s" in block                       # byte-compared
    assert "restart" not in block.replace("next start", "")
    assert "meshchatx.service" not in block


# ── contextless review, 2026-10-26 ──────────────────────────────────────

import pytest  # noqa: E402


@pytest.mark.parametrize("state", ["static", "indirect", "enabled-runtime",
                                   "linked", "generated", "alias"])
def test_only_disabled_or_masked_is_a_deliberate_stop(tmp_path, state):
    """At boot rnsd is inactive before its job runs; any non-disabled
    install state is the race window, never a skip."""
    r = run_wrapper(tmp_path, ss_out="",
                    unit=f"LoadState=loaded\nUnitFileState={state}\n"
                         "ActiveState=inactive\nMainPID=0\n")
    assert r.returncode == 75, (state, r.stderr)
    assert not started(r)


def test_masked_rnsd_skips_the_wait(tmp_path):
    r = run_wrapper(tmp_path, ss_out="",
                    unit="LoadState=masked\nUnitFileState=masked\n"
                         "ActiveState=inactive\nMainPID=0\n")
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_an_unreadable_unit_state_is_not_read_as_not_installed(tmp_path):
    """systemctl failing is UNOBSERVABLE, never 'absent by design'."""
    r = run_wrapper(tmp_path, ss_out="", unit="")
    assert r.returncode == 75, r.stderr
    assert not started(r)
    assert "not installed" not in r.stderr


def test_a_quoted_instance_name_is_unquoted_like_configobj(tmp_path):
    other = OWNED.replace("@rns/default", "@rns/mf")
    r = run_wrapper(tmp_path, ss_out=other,
                    cfg_extra='  instance_name = "mf"\n')
    assert r.returncode == 0, r.stderr
    assert started(r)


def test_tcp_shared_instance_has_no_socket_to_wait_for(tmp_path):
    r = run_wrapper(tmp_path, ss_out="",
                    cfg_extra="  shared_instance_type = tcp\n")
    assert r.returncode == 0, r.stderr
    assert started(r)
    assert "tcp" in r.stderr


def test_a_user_scope_rnsd_is_found_and_waited_for(tmp_path):
    """templates/systemd/rnsd-user.service: rnsd as a USER unit. The system
    manager says not-found; the user manager must be asked before skipping."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    r = run_wrapper(tmp_path, ss_out="",
                    unit="__USER_SCOPE__")
    assert r.returncode == 75, r.stderr
    assert not started(r)


def test_an_empty_pid_never_matches_an_empty_mainpid(tmp_path):
    r = run_wrapper(tmp_path,
                    ss_out='u_str LISTEN 0 0 @rns/default 1 * 0 '
                           'users:(("python3",fd=7))\n',
                    unit="LoadState=loaded\nUnitFileState=enabled\n"
                         "ActiveState=active\n")
    assert r.returncode == 75, r.stderr
    assert not started(r)


def _run_update_refresh(tmp_path, dst_is_symlink):
    """Execute update.sh's wrapper-refresh block itself, not its text."""
    text = (REPO_ROOT / "scripts" / "update.sh").read_text()
    start = text.index('mcx_src="$INSTALL_DIR/templates/python/'
                       'meshchatx_wrapper.sh"')
    block = text[start:text.index("chown -R", start)]
    install_dir = tmp_path / "repo"
    (install_dir / "templates" / "python").mkdir(parents=True)
    (install_dir / "templates" / "python" / "meshchatx_wrapper.sh").write_text(
        "NEW\n")
    home = tmp_path / "home"
    (home / ".config" / "meshanchor").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("ROOT-OWNED\n")
    dst = home / ".config" / "meshanchor" / "meshchatx_wrapper.sh"
    if dst_is_symlink:
        dst.symlink_to(victim)
    else:
        dst.write_text("OLD\n")
    user = subprocess.run(["id", "-un"], capture_output=True,
                          text=True).stdout.strip()
    script = (f'INSTALL_DIR={install_dir}\nREAL_HOME={home}\n'
              f'REAL_USER={user}\nGREEN=\nYELLOW=\nNC=\n' + block)
    subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                   timeout=30)
    return dst, victim


def test_update_refresh_replaces_a_stale_installed_wrapper(tmp_path):
    dst, _ = _run_update_refresh(tmp_path, dst_is_symlink=False)
    assert dst.read_text() == "NEW\n"
    assert os.access(dst, os.X_OK)


def test_update_refresh_never_writes_through_a_symlink(tmp_path):
    """update.sh runs as root: following a user-planted link would overwrite
    and chown an arbitrary file to the user."""
    _, victim = _run_update_refresh(tmp_path, dst_is_symlink=True)
    assert victim.read_text() == "ROOT-OWNED\n"
