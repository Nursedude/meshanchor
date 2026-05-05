#!/usr/bin/env bash
#
# install_sudoers.sh — validate + install /etc/sudoers.d/010_meshanchor.
#
# Why a wrapper instead of "just cp": a malformed sudoers file locks
# everyone out of sudo. visudo -c validates *before* the install, and
# install -m 0440 -o root -g root sets the strict perms sudoers requires
# (sudo refuses files with permissions wider than 0440 or non-root owner).
#
# Run with: sudo bash scripts/install_sudoers.sh
# Idempotent: re-running with no source changes is a no-op.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/010_meshanchor.sudoers"
DEST="/etc/sudoers.d/010_meshanchor"

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    exit 1
fi

if [[ ! -f "${SRC}" ]]; then
    echo "error: source sudoers file not found at ${SRC}" >&2
    exit 1
fi

# Validate the source file BEFORE touching /etc/sudoers.d.
# visudo -c -f exits non-zero on any syntax error.
if ! visudo -c -f "${SRC}" >/dev/null; then
    echo "error: sudoers source has syntax errors — refusing to install" >&2
    visudo -c -f "${SRC}"
    exit 1
fi

# install -m 0440 -o root -g root sets exactly the perms sudoers requires.
install -m 0440 -o root -g root "${SRC}" "${DEST}"

# Final guard: validate the entire /etc/sudoers tree post-install.
# If this fails, the just-installed file is the most likely culprit;
# remove it before exiting so a broken file doesn't lock out sudo.
if ! visudo -c >/dev/null; then
    echo "error: post-install sudoers validation failed — removing ${DEST}" >&2
    rm -f "${DEST}"
    visudo -c
    exit 1
fi

echo "installed: ${DEST}"
echo
echo "verify with: sudo -n -l -U wh6gxz | grep -i meshanchor"
