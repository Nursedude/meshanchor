"""MeshAnchor fleet_hosts membership — THE Python resolver.

The ONLY implementation of the resolution chain (the meshanchor namespace):
``scripts/lib/fleet_hosts.sh`` runs this module's ``--shell`` entry point
instead of re-deriving the decision (operator 2026-11-05, after review
rounds r7f..r7i each found a new path shape on which a shell copy
disagreed). Pinned by ``tests/test_fleet_hosts_resolver.py``.

Born 2026-07-29 closing the WS-A port artifact: ``mini_dudeai.rollup`` (and
through it the daemon's ``--preset auto``) resolved the MESHFORGE-namespaced
list verbatim from the MF twin — on meshanchor-server that file does not
exist, so the fleet pane silently degraded to a 1-box view, and on the one
box that HAS the meshforge list (the manager) the pane would have swept the
wrong fleet and ``auto`` would have crashed on a preset name MA does not
ship. The collector-of-record for /api/fleet/truth is untouched — it reads
``fleet.json`` peers by documented design, not this file.

Resolution order::

    $MESHANCHOR_FLEET_HOSTS   - AUTHORITATIVE when set: a missing/unreadable
    $FLEET_HOSTS                override yields NO list rather than falling
                                through to the box's real config (a degraded
                                state must not read as a valid value;
                                FLEET_HOSTS is the legacy alias
                                lab_traffic_rollup documented)
    ~/.config/meshanchor/fleet_hosts
    /etc/meshanchor/fleet_hosts

A tier that is ABSENT passes to the next one. A tier that is PRESENT but
cannot be read — permission, a directory, a dangling symlink — or cannot be
CHECKED (a parent directory is not searchable) raises
:class:`FleetHostsUnreadable` naming the path, and so does a read that fails
after resolution. Ported 2026-11-05 from MF r7e #2 (a7d198f0) + r7f
(d4fb041f); operator: "quietly failing is not an answer". Falling through
handed the caller the NEXT tier's list — a different fleet, reported as the
fleet.

Deliberate divergences from the MF twin's ``utils/fleet_hosts.py``: the
meshanchor namespace, and NO per-repo ``fleet_hosts.<basename>`` tier (no MA
consumer scopes by repo; an unused tier is speculative surface).

Home is the REAL user's home (sudo-safe) unless an ``env`` mapping is
injected — tests pass ``env={"HOME": ...}`` and ``etc_dir=`` so no assertion
ever depends on the machine running the suite.

File format: hosts separated by whitespace/newlines; ``#`` starts a comment
anywhere on the line, so ``"moc1  # retired"`` parses as host ``moc1``.
"""
from __future__ import annotations

import errno
import os
import re
import stat
import sys
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

DEFAULT_ETC_DIR = "/etc/meshanchor"
#: First SET (non-empty) key wins and is authoritative.
ENV_OVERRIDE_KEYS = ("MESHANCHOR_FLEET_HOSTS", "FLEET_HOSTS")


def _parse_fleet_hosts_text(text: str, path: Optional[Path] = None) -> List[str]:
    """Hosts from a fleet_hosts document: ``#``-to-EOL stripped, then
    whitespace-split. The ONLY parser: the shell lib asks ``--shell``.

    An entry that is not printable ASCII raises :class:`FleetHostsUnreadable`
    (r8 review #1, measured): a power-loss NUL fill read as ONE garbage host
    here while bash dropped the NULs and read an EMPTY list; a stray byte
    became U+FFFD in a name ssh would then dial. A host name is ASCII — a
    corrupt list is refused, never parsed into a different fleet. Bytes
    inside ``#`` comments stay harmless."""
    if text.startswith("\ufeff"):
        # r10 review: an editor's UTF-8 BOM is not a corrupt list.
        text = text[1:]
    # Split ONLY on newline / CR / space / tab (r13, Fable r12 #7):
    # str.splitlines()/split() also break on \x0b \x0c \x1c-\x1f, NEL and
    # U+2028/9 — a corrupt entry was SPLIT into two "hosts" instead of
    # refused. Anything else stays inside its token and the check below
    # refuses it.
    hosts: List[str] = []
    for raw in re.split(r"\r\n|\r|\n", text):
        hosts.extend(t for t in re.split(r"[ \t]+", raw.split("#", 1)[0]) if t)
    for h in hosts:
        if not all(0x21 <= ord(c) <= 0x7E for c in h):
            raise FleetHostsUnreadable(
                path if path is not None else Path("<fleet host list>"),
                "entry %s is not a host name - a corrupt file?" % ascii(h))
    return hosts



def read_fleet_hosts_file(path: "Path | str", *, allow_empty: bool = False) -> List[str]:
    """THE reader of a fleet host list file — every module that has a path
    reads it here (r11 seam, operator 2026-11-05; guarded by
    tests/test_fleet_hosts_resolver.py::TestOneReader).

    - absent file -> ``FileNotFoundError`` UNCHANGED: absence is a distinct,
      valid outcome (standalone), and a caller must choose it explicitly;
    - any other read failure, or a corrupt entry -> :class:`FleetHostsUnreadable`
      naming the path. It is an ``OSError``: NEVER catch ``OSError`` around
      this and return ``[]`` — that turns a refusal into "no fleet" (r10: five
      private readers did exactly that).
    - ``errors="replace"`` so a stray byte inside a ``#`` comment is harmless;
      a stray byte in a HOST is refused by the parser.
    - an EXISTING list naming no hosts -> FleetHostsUnreadable by DEFAULT
      (r19, Fable r18: consumers that resolve-then-read bypassed the r17
      resolver check and judged "a fleet of zero" — --fleet-check OK,
      rns_alignment aligned, naming audit 0 findings). Only a caller that
      must SHOW the file's contents (the TUI wizard) passes allow_empty."""
    p = Path(path)
    try:
        # stat BEFORE opening (Fable, standalone arc #5): opening a FIFO
        # blocks forever — it hung the whole TUI Fleet Membership menu.
        st = os.stat(p)
        if not stat.S_ISREG(st.st_mode):
            raise FleetHostsUnreadable(p, "a directory, not a file"
                                       if stat.S_ISDIR(st.st_mode) else "not a regular file")
        text = p.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError) as exc:
        if os.path.lexists(p):
            # r13 (Fable r12 #4, VERIFIED): a DANGLING symlink also raises
            # ENOENT — it is present, so it is refused, exactly as
            # _usable refuses it. Only a truly absent path is "absent".
            raise FleetHostsUnreadable(p, "dangling symlink") from exc
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(p)) from exc
    except FleetHostsUnreadable:
        raise                      # already says what and why — never re-wrap
    except OSError as exc:
        raise FleetHostsUnreadable(p, exc.strerror or str(exc)) from exc
    hosts = _parse_fleet_hosts_text(text, p)
    if not hosts and not allow_empty:
        raise FleetHostsUnreadable(p, LISTS_NO_HOSTS)
    return hosts


class FleetHostsUnreadable(OSError):
    """A fleet host list at ``path`` is present but unusable, or cannot be
    checked. An ``OSError`` so an existing ``except OSError`` still refuses;
    every caller must turn it into its own refusal / UNKNOWN, never "no
    fleet". Same class, same words as the MF twin."""

    def __init__(self, path: Path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(
            "fleet host list %s is unreadable (%s) - refusing to fall "
            "through to another list" % (self.path, reason))


#: The refusal reason for an existing list with no hosts (r17).
LISTS_NO_HOSTS = "lists no hosts - an empty list is not a fleet of zero"

#: A tier we cannot prove absent. Shared wording with the shell twin.
UNCHECKABLE = "cannot be checked: a parent directory is not searchable"


def _usable(p: Path) -> bool:
    """True = a readable regular file; False = provably absent (ENOENT, or a
    non-directory where a parent should be); raises when present but unusable
    OR uncheckable. Explicit ``lstat``, not ``Path.is_file`` (whose EACCES
    handling differs by Python version) — mirrors MF r7f F2."""
    if "\n" in str(p):
        # r8 review #7: the shell seam is line-based; refuse in ONE place so
        # the python API and the shell lib give the same answer.
        raise FleetHostsUnreadable(p, "a newline in the path")
    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError as exc:
        raise FleetHostsUnreadable(p, UNCHECKABLE) from exc
    except OSError as exc:
        # r7h: a loop in a PARENT component is a loop too — same words as the
        # final-component case and the shell twin.
        why = ("symlink loop" if exc.errno == errno.ELOOP
               else exc.strerror or str(exc))
        raise FleetHostsUnreadable(p, why) from exc
    except ValueError as exc:
        raise FleetHostsUnreadable(p, str(exc)) from exc
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(p)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise FleetHostsUnreadable(p, "dangling symlink") from exc
        except PermissionError as exc:
            raise FleetHostsUnreadable(p, "symlink target " + UNCHECKABLE) from exc
        except OSError as exc:
            # r7g F1: ELOOP is a loop, not a permission problem; same words
            # as the shell twin.
            why = ("symlink loop" if exc.errno == errno.ELOOP
                   else exc.strerror or str(exc))
            raise FleetHostsUnreadable(p, why) from exc
    if stat.S_ISDIR(st.st_mode):
        raise FleetHostsUnreadable(p, "a directory, not a file")
    if not stat.S_ISREG(st.st_mode):
        raise FleetHostsUnreadable(p, "not a regular file")
    if not os.access(p, os.R_OK):
        raise FleetHostsUnreadable(p, "permission denied")
    return True


def _tier_candidates(env: Optional[Mapping[str, str]], etc_dir: str) -> List[Path]:
    """The file tiers in resolution order (overrides excluded). ONE list —
    the resolver walks it and ``--shell`` names it when nothing resolves."""
    if env is None:
        from utils.paths import get_real_user_home
        home: Optional[Path] = get_real_user_home()
    else:
        home = Path(env["HOME"]) if env.get("HOME") else None
    candidates: List[Path] = []
    if home:
        candidates.append(home / ".config" / "meshanchor" / "fleet_hosts")
    candidates.append(Path(etc_dir) / "fleet_hosts")
    return candidates


def resolve_fleet_hosts_file(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> Optional[Path]:
    """The file that wins the resolution order, or ``None`` if no list exists
    (including a SET-but-ABSENT env override — authoritative, no
    fall-through). Raises :class:`FleetHostsUnreadable` when the winning
    candidate — or any tier above it — is present but unusable/uncheckable."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    for key in ENV_OVERRIDE_KEYS:
        val = env_map.get(key)
        if val:
            p = Path(val)
            return p if _usable(p) else None
    for c in _tier_candidates(env, etc_dir):
        if _usable(c):
            return c
    return None


def resolve_fleet_hosts(
    *,
    env: Optional[Mapping[str, str]] = None,
    etc_dir: str = DEFAULT_ETC_DIR,
) -> List[str]:
    """The host list, or ``[]`` when no list resolves. An unreadable list
    RAISES :class:`FleetHostsUnreadable` — never ``[]``.

    An EXISTING list that names no hosts RAISES too (r17, Fable r16: a
    zero-byte power-loss truncation read as "a fleet of zero" — health
    deleted a standing RED marker, sync reported success syncing nobody).
    Absence of the FILE is the standalone signal; an empty file is not."""
    f = resolve_fleet_hosts_file(env=env, etc_dir=etc_dir)
    if f is None:
        return []
    try:
        hosts = read_fleet_hosts_file(f)
    except FileNotFoundError as exc:   # vanished between resolve and read
        raise FleetHostsUnreadable(f, "vanished after it was resolved") from exc
    if not hosts:
        raise FleetHostsUnreadable(f, LISTS_NO_HOSTS)
    return hosts


# --- the generated /etc/hosts fleet block -------------------------------------
#
# Ported from MeshForge 2026-09-07 (MF is the lead repo). ⚠️ These literals are
# deliberately NOT namespaced to meshanchor, unlike the rest of this module:
# the block is written by MeshForge's ``scripts/gen_fleet_hosts.py``, ONE per
# box, and both twins are READERS of that single artifact. Namespacing them
# would make this reader find no block at all — silently, since an absent block
# is indistinguishable from a box with no fleet.
HOSTS_BLOCK_BEGIN = "# BEGIN meshforge fleet (generated by scripts/gen_fleet_hosts.py)"
HOSTS_BLOCK_END = "# END meshforge fleet"
DEFAULT_DOMAIN = "mf.internal"


def parse_hosts_block(text: str) -> List[Tuple[str, List[str]]]:
    """``[(addr, [name, ...]), ...]`` from the DELIMITED meshforge block only.

    A second membership registry, and the only one present on EVERY box:
    ``fleet_hosts`` lives on the manager, while this block is generated and
    hourly self-healed on all of them.

    ⚠️ **Only ever the delimited block.** Parsing the whole file would make
    ``localhost``, the ``127.0.1.1`` line and any hand-added entry count as
    fleet members — widening what is "expected" is the one direction a
    membership registry must never fail in.

    ⚠️ **A torn block yields nothing.** BEGIN without END means the artifact
    is damaged; the safe read is to know less, not to guess more.
    """
    if HOSTS_BLOCK_BEGIN not in text or HOSTS_BLOCK_END not in text:
        return []
    body = text.split(HOSTS_BLOCK_BEGIN, 1)[1].split(HOSTS_BLOCK_END, 1)[0]
    out: List[Tuple[str, List[str]]] = []
    for raw in body.splitlines():
        fields = raw.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        addr = fields[0].strip().lower()
        names = [h.strip().lower().rstrip(".") for h in fields[1:] if h.strip()]
        if addr and names:
            out.append((addr, names))
    return out


# --- the shell twin's ONLY implementation ------------------------------------
#
# scripts/lib/fleet_hosts.sh runs this instead of re-deriving the decision
# (operator 2026-11-05, after four review rounds r7f..r7i each found a new
# path shape on which a shell re-implementation disagreed with ``_usable``:
# an unsearchable parent, a symlink loop, ENAMETOOLONG, a trailing slash).
# One implementation cannot drift from itself.
#
# Exit codes are DISJOINT from python's own failure codes (1 = traceback,
# 2 = usage), so a crash can never read as "no list":
SHELL_RC_OK, SHELL_RC_ABSENT, SHELL_RC_UNREADABLE = 0, 10, 11
#: First stdout line on rc 0 — the lib requires it, so a chatty wrapper's
#: stray line can never become FLEET_HOSTS_FILE (r17, Fable r16 O3).
SHELL_FORMAT = "fleet_hosts/1"


def _shell_out(lines: List[str]) -> None:
    """Bytes, not the locale's text encoder: a path with non-UTF-8 bytes
    (surrogateescape) or U+FFFD from a replaced host byte must not crash the
    one resolver into a refusal of a readable list (r8 review #1). A newline
    inside one value is escaped so it cannot forge a second line."""
    sys.stdout.buffer.write(b"".join(
        os.fsencode(v.replace("\n", "\\n")) + b"\n" for v in lines))
    sys.stdout.flush()


def explain_absent(*, env: Optional[Mapping[str, str]] = None,
                   etc_dir: str = DEFAULT_ETC_DIR) -> Tuple[str, List[str]]:
    """THE words for "no list resolved": ``(why, advice_lines)``. Every
    consumer (``--shell`` rc 10 -> the shell lib's ``fleet_hosts_absent_say``,
    the rollup pane) says these - never its own hand-typed advice.

    Shape port of the MeshForge twin's 2026-11-06 fix (MF ``5d73d48b``): the
    consumers advised "create ~/.config/meshanchor/fleet_hosts" while a SET
    override pointed at a missing path - advice that cannot work, since a
    set override is authoritative. The advice follows the SAME inputs the
    resolver used (which overrides are set, whether HOME is). MeshAnchor has
    no per-repo tier and no example file, so MF's sister-repo and ``cp``
    lines do not apply here."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    set_keys = [k for k in ENV_OVERRIDE_KEYS if env_map.get(k)]
    if set_keys:
        key = set_keys[0]
        why = ("$%s=%s does not exist (authoritative - no fall-through to "
               "another list)" % (key, env_map[key]))
        return why, ["The override is authoritative: create %s, or unset %s "
                     "to use this box's own list." % (env_map[key], " and ".join(
                         "$" + k for k in set_keys))]
    tiers = _tier_candidates(env, etc_dir)
    why = "no fleet list found (checked %s)" % ", ".join(str(t) for t in tiers)
    if env is None:
        from utils.paths import get_real_user_home
        home: Optional[Path] = get_real_user_home()
    else:
        home = Path(env["HOME"]) if env.get("HOME") else None
    if home is None:
        why += "; HOME is unset, so no per-user list was checked"
        advice = ["Create %s (system-wide; one host per line, '#' comments "
                  "allowed), or run with HOME set." % (Path(etc_dir) / "fleet_hosts")]
    else:
        advice = ["Create one at %s (one host per line, '#' comments allowed)."
                  % (home / ".config" / "meshanchor" / "fleet_hosts")]
    advice.append("Or set $MESHANCHOR_FLEET_HOSTS to a different path.")
    return why, advice


def _shell_main(argv: List[str]) -> int:
    """``--shell``. Home and the overrides come from THIS process's
    environment exactly as the shell passed them (``$HOME``, not the
    sudo-real home — the shell lib's contract). rc 0: stdout = the file,
    then one host per line; rc 10: stdout = why there is no list; rc 11:
    stdout = the refusal."""
    env = dict(os.environ)
    try:
        f = resolve_fleet_hosts_file(env=env)
        if f is None:
            # line 1 = why (one line: $FLEET_HOSTS_WHY); the rest = advice
            why, advice = explain_absent(env=env)
            _shell_out([why] + advice)
            return SHELL_RC_ABSENT
        try:
            hosts = read_fleet_hosts_file(f)
        except FileNotFoundError as exc:
            raise FleetHostsUnreadable(f, "vanished after it was resolved") from exc
        if not hosts:
            raise FleetHostsUnreadable(f, LISTS_NO_HOSTS)
    except FleetHostsUnreadable as exc:
        _shell_out([str(exc)])
        return SHELL_RC_UNREADABLE
    _shell_out([SHELL_FORMAT, str(f)] + hosts)
    return SHELL_RC_OK


if __name__ == "__main__":
    if sys.argv[1:] != ["--shell"]:
        sys.stderr.write("usage: python3 -I <repo>/src/utils/fleet_hosts.py --shell\n")
        raise SystemExit(2)
    raise SystemExit(_shell_main(sys.argv[1:]))
