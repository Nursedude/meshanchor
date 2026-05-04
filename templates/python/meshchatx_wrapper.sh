#!/usr/bin/env bash
# MeshAnchor MeshChatX wrapper — refuses-loud on rpc_key mismatch.
#
# Version: 1
#
# Ported from MeshForge (which paired this with the canonical NomadNet
# wrapper). MeshChatX is a web daemon, not a TUI that imports RNS
# in-process, so the refuses-loud guard happens at startup as a
# precondition check rather than a runtime monkeypatch:
#
#   - If /etc/reticulum/config (or ~/.reticulum/config) lacks a 64-hex
#     rpc_key line, exit 87. The systemd unit's StartLimitBurst then
#     parks the service in failed state after 5 retries so the journal
#     carries one clear diagnostic line instead of a silent restart
#     loop.
#
# Storage dir, host, port, and HTTPS settings are pinned here so the
# unit's ExecStart is independent of MeshChatX's CLI defaults — those
# may shift between releases. To change a flag, edit this wrapper and
# re-run: sudo bash /opt/meshanchor/scripts/install_meshchatx.sh --refresh
#
# Both the canonical installer and the MeshChatX TUI handler's
# _install_user_unit copy this file verbatim into
# ``~/.config/meshanchor/meshchatx_wrapper.sh``. Bumping the
# ``Version:`` line above forces both sides to refresh.

set -eu

# --------------------------------------------------------------------
# Refuse-loud sentinel
# --------------------------------------------------------------------
EXIT_AUTH_MISMATCH=87

err() {
    printf '\n[meshanchor meshchatx_wrapper] %s\n' "$*" >&2
}

# --------------------------------------------------------------------
# 1. rpc_key precondition
# --------------------------------------------------------------------
rpc_key_pinned() {
    local cfg
    if [[ -r /etc/reticulum/config ]]; then
        cfg=/etc/reticulum/config
    elif [[ -r "${HOME}/.reticulum/config" ]]; then
        cfg="${HOME}/.reticulum/config"
    else
        return 1
    fi
    grep -qE '^[[:space:]]*rpc_key[[:space:]]*=[[:space:]]*[0-9a-fA-F]{64}' "${cfg}"
}

if ! rpc_key_pinned; then
    err "RNS rpc_key MISMATCH detected (or rpc_key not pinned)."
    err "  rnsd and MeshChatX would use different identities -> AuthenticationError."
    err "  FIX:"
    err "    Edit /etc/reticulum/config (or ~/.reticulum/config) and add"
    err "    a [reticulum] section with: rpc_key = <64 hex chars>"
    err "    See: https://markqvist.github.io/Reticulum/manual/using.html"
    exit "${EXIT_AUTH_MISMATCH}"
fi

# --------------------------------------------------------------------
# 2. Resolve the meshchatx binary (pipx-installed under ~/.local/bin)
# --------------------------------------------------------------------
MCX_BIN="${HOME}/.local/bin/meshchatx"
if [[ ! -x "${MCX_BIN}" ]]; then
    if command -v meshchatx >/dev/null 2>&1; then
        MCX_BIN="$(command -v meshchatx)"
    else
        err "meshchatx binary not found in ~/.local/bin or on PATH."
        err "  Run: sudo bash /opt/meshanchor/scripts/install_meshchatx.sh --reinstall"
        exit 2
    fi
fi

# --------------------------------------------------------------------
# 3. Storage dir (canonical, survives cwd changes)
# --------------------------------------------------------------------
STORAGE_DIR="${HOME}/.local/share/meshchatx"
mkdir -p "${STORAGE_DIR}"

# --------------------------------------------------------------------
# 4. Exec MeshChatX with canonical flags
# --------------------------------------------------------------------
# --headless        : no Electron/browser auto-launch
# --host 127.0.0.1  : localhost-only by default; SSH-tunnel for remote
# --port 8000       : free in MeshAnchor (config_api on 8081, map on 5000)
# --no-https        : self-signed cert friction is a UX cost we pay
#                     only when exposing beyond localhost
# --storage-dir     : pin to ~/.local/share/meshchatx so the service
#                     doesn't depend on cwd
exec "${MCX_BIN}" \
    --headless \
    --host 127.0.0.1 \
    --port 8000 \
    --no-https \
    --storage-dir "${STORAGE_DIR}"
