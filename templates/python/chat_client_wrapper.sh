#!/usr/bin/env bash
# MeshAnchor — MeshCore chat client wrapper (tmux-detached)
#
# Version: 1
#
# Pairs with templates/systemd/meshcore-chat-user.service. The unit
# launches this wrapper inside a detached tmux session named
# `meshcore-chat`; operators reach the live chat by:
#
#   tmux attach -t meshcore-chat       # type messages, Ctrl-b d to detach
#   ChatPaneHandler "Attach" action    # same thing from the TUI
#
# This wrapper is intentionally minimal — the chat client itself
# (utils/chat_client.py) is the long-lived process. The wrapper:
#   1. Resolves a Python interpreter that can import utils.chat_client
#      (prefers the MeshAnchor venv if installed, falls back to the
#      system python3 + PYTHONPATH=/opt/meshanchor/src).
#   2. Probes the local gateway HTTP API on :8081 — refuses-loud with
#      exit 87 if it's not reachable (mirrors the MeshChatX wrapper's
#      refuses-loud pattern; systemd's StartLimitBurst then parks the
#      unit instead of restart-looping forever).
#   3. exec's the client.
#
# Both ``scripts/install_chat_pane.sh`` (if/when added) and the
# ChatPaneHandler's _install_user_unit copy this file verbatim into
# ``~/.config/meshanchor/chat_client_wrapper.sh``. Bumping the
# ``Version:`` line above forces both sides to refresh.

set -eu

EXIT_API_UNREACHABLE=87
CHAT_API="${MESHANCHOR_CHAT_API:-http://127.0.0.1:8081}"

err() {
    printf '\n[meshanchor chat_client_wrapper] %s\n' "$*" >&2
}

# --------------------------------------------------------------------
# 1. Refuse-loud: gateway daemon must be reachable
# --------------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf -m 5 "${CHAT_API}/chat/messages?since=0" >/dev/null; then
        err "Gateway daemon not reachable at ${CHAT_API}/chat/messages"
        err "  Ensure meshanchor-daemon.service is running:"
        err "    sudo systemctl status meshanchor-daemon"
        err "  (the chat pane talks to the daemon, not the radio directly)"
        exit "${EXIT_API_UNREACHABLE}"
    fi
fi

# --------------------------------------------------------------------
# 2. Resolve Python interpreter + chat_client module
# --------------------------------------------------------------------
PY_BIN=""
if [[ -x /opt/meshanchor/venv/bin/python ]]; then
    PY_BIN="/opt/meshanchor/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
else
    err "python3 not found on PATH"
    exit 2
fi

export PYTHONPATH="/opt/meshanchor/src${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export MESHANCHOR_CHAT_API="${CHAT_API}"

# --------------------------------------------------------------------
# 3. Exec the chat client
# --------------------------------------------------------------------
exec "${PY_BIN}" -m utils.chat_client "$@"
