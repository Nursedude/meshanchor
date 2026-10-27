#!/usr/bin/env bash
# MeshAnchor MeshChatX wrapper — refuses-loud on rpc_key mismatch.
#
# Version: 2
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
# Storage dir, port, and HTTPS settings are pinned here (host: section 4) so the
# unit's ExecStart is independent of MeshChatX's CLI defaults — those
# may shift between releases. To change a flag, edit this wrapper and
# re-run: sudo bash /opt/meshanchor/scripts/install_meshchatx.sh --refresh
#
# The canonical installer copies this file verbatim into
# ``~/.config/meshanchor/meshchatx_wrapper.sh`` and replaces it whenever the
# bytes differ (``cmp``); the TUI runs that installer. The ``Version:`` line
# is informational - it does not drive the refresh.

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
# 4. Listen address — the operator's setting, never re-rendered
# --------------------------------------------------------------------
# The installer replaces this wrapper whenever its bytes differ (cmp), so a
# hand edit of --host is silently reverted. The knob lives in an
# operator-owned file the installer never writes:
#   ~/.config/meshanchor/meshchatx.env   ->   MESHCHATX_HOST=0.0.0.0
# then:  systemctl --user restart meshchatx   (a refresh only STARTs a
# stopped unit; a running one keeps its old bind until restarted).
# Read as DATA, never sourced: `export ` and `# comments` are tolerated,
# the last MESHCHATX_HOST line wins. Absent -> 127.0.0.1 (SSH-tunnel for
# remote). Every fallback says so in the journal; a value that is not a
# usable IP literal refuses loud (exit 2) instead of a bind nobody chose.
# A non-loopback bind says so on every start: it is plain HTTP and, unless
# --auth is set, anyone who can reach it speaks as this box's LXMF identity.
MCX_HOST=127.0.0.1
MCX_ENV="${HOME}/.config/meshanchor/meshchatx.env"

valid_ip() {
    [[ "$1" =~ ^[0-9a-fA-F:.]+$ ]] || return 1
    python3 -c 'import ipaddress, sys; ipaddress.ip_address(sys.argv[1])' \
        "$1" 2>/dev/null
}

if [[ -e "${MCX_ENV}" ]]; then
    if [[ ! -f "${MCX_ENV}" || ! -r "${MCX_ENV}" ]]; then
        err "${MCX_ENV} is not a readable file - using 127.0.0.1."
    else
        line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?MESHCHATX_HOST[[:space:]]*=' \
            "${MCX_ENV}" | tail -n 1 || true)"
        if [[ -n "${line}" ]]; then
            v="${line#*=}"
            v="${v%%#*}"
            v="${v//[[:space:]]/}"; v="${v//\"/}"; v="${v//\'/}"
            if [[ -z "${v}" ]]; then
                err "MESHCHATX_HOST is empty in ${MCX_ENV} - using 127.0.0.1."
            elif [[ "${v}" == "::" ]]; then
                err "MESHCHATX_HOST=:: in ${MCX_ENV} may bind IPv6 only (unreachable over IPv4)."
                err "  FIX: MESHCHATX_HOST=0.0.0.0 for the LAN."
                exit 2
            elif ! valid_ip "${v}"; then
                err "MESHCHATX_HOST='${v}' in ${MCX_ENV} is not an IP literal."
                err "  FIX: MESHCHATX_HOST=127.0.0.1 (local) or 0.0.0.0 (LAN), or delete the line."
                exit 2
            else
                MCX_HOST="${v}"
            fi
        fi
    fi
fi
if [[ "${MCX_HOST}" != 127.* && "${MCX_HOST}" != "::1" ]]; then
    err "listening on ${MCX_HOST}:8000 (set in ${MCX_ENV}) - plain HTTP, reachable beyond this box"
fi

# --------------------------------------------------------------------
# 5. Wait for rnsd to OWN the shared-instance socket (#69 boot race)
# --------------------------------------------------------------------
# MeshChatX embeds stock RNS: whoever binds @rns/<instance> first becomes
# the shared-instance HOST. On the 2026-10-26 meshanchor-server reboot this
# (lingering, user) unit reached its RNS stage 0.6s before rnsd (system
# unit, behind its IPv6 gate) bound the socket, so MeshChatX hosted and
# rnsd ran as its client. The unit's After=rnsd.service cannot prevent
# that: a USER manager cannot order against a SYSTEM unit.
#
# So wait for an OWNER, not a socket (MeshForge cfad33b2): the listener
# must belong to rnsd's MainPID (or a process named rnsd). A squatter
# keeps us waiting; past the budget we exit 75 and Restart= retries, so
# MeshChatX comes up once rnsd does. A deliberate absence — rnsd not
# installed, or disabled AND stopped — is the operator's call: skip, say so.
# ⚠️ Behaviour change: an rnsd that is enabled but FAILED now keeps
# MeshChatX down (loudly, retrying) where it used to run standalone — a
# standalone MeshChatX IS the squatter rnsd later loses to.
# MESHANCHOR_RNSD_WAIT_S exists for tests; keep the budget well above
# RestartSec x StartLimitBurst / StartLimitIntervalSec or 75 parks the unit.
EXIT_RNSD_NOT_OWNER=75
RNSD_WAIT_S="${MESHANCHOR_RNSD_WAIT_S:-90}"

# rns_cfg_value <key> — last matching line, unquoted the way configobj does
# (instance_name = "mf" binds @rns/mf, not @rns/"mf").
rns_cfg_value() {
    local cfg="" v=""
    if [[ -r /etc/reticulum/config ]]; then
        cfg=/etc/reticulum/config
    elif [[ -r "${HOME}/.reticulum/config" ]]; then
        cfg="${HOME}/.reticulum/config"
    fi
    [[ -n "${cfg}" ]] || return 0
    v="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "${cfg}" | tail -n 1 || true)"
    v="${v#*=}"; v="${v%%#*}"; v="${v//[[:space:]]/}"
    v="${v//\"/}"; v="${v//\'/}"
    printf '%s' "${v}"
}

prop() {   # prop <Key> — from the current RNSD_PROPS snapshot
    sed -n "s/^$1=//p" <<<"${RNSD_PROPS}" | head -n 1
}

# Which manager runs rnsd? The fleet's is a SYSTEM unit; rnsd-user.service
# documents a USER-unit install. RNSD_SCOPE: system | user | none (asked
# both, neither has it) | "" (systemctl unreadable — UNOBSERVABLE, never
# read as "not installed").
read_rnsd_unit() {
    local q=(show rnsd.service -p LoadState -p UnitFileState
             -p ActiveState -p MainPID) sys usr
    sys="$(systemctl "${q[@]}" 2>/dev/null || true)"
    RNSD_PROPS="${sys}"
    if [[ -n "${sys}" && "$(prop LoadState)" != "not-found" ]]; then
        RNSD_SCOPE=system; return
    fi
    usr="$(systemctl --user "${q[@]}" 2>/dev/null || true)"
    RNSD_PROPS="${usr}"
    if [[ -n "${usr}" && "$(prop LoadState)" != "not-found" ]]; then
        RNSD_SCOPE=user; return
    fi
    if [[ -n "${sys}" ]]; then
        RNSD_SCOPE=none; RNSD_PROPS="${sys}"
    else
        RNSD_SCOPE=""; RNSD_PROPS=""
    fi
}

SHARED_TYPE="$(rns_cfg_value shared_instance_type)"
INSTANCE="$(rns_cfg_value instance_name)"
RNS_SOCK="@rns/${INSTANCE:-default}"
read_rnsd_unit

if [[ "${SHARED_TYPE,,}" == "tcp" ]]; then
    err "shared_instance_type = tcp - there is no ${RNS_SOCK} socket to own;" \
        "not waiting (the #69 owner check covers unix shared instances only)."
elif [[ "${RNSD_SCOPE}" == "none" ]]; then
    err "rnsd.service not installed here (system or user) - not waiting;" \
        "MeshChatX may host ${RNS_SOCK} itself."
elif [[ -n "${RNSD_SCOPE}" \
        && ( "$(prop LoadState)" == "masked" \
             || "$(prop UnitFileState)" == "disabled" \
             || "$(prop UnitFileState)" == "masked" ) \
        && "$(prop ActiveState)" != "active" \
        && "$(prop ActiveState)" != "activating" ]]; then
    # Only disabled/masked is deliberate: static/indirect/enabled-runtime/...
    # read `inactive` at boot before rnsd's job runs — that IS the race.
    err "rnsd.service (${RNSD_SCOPE}) is $(prop UnitFileState) and" \
        "$(prop ActiveState) - a deliberate stop; not waiting," \
        "MeshChatX may host ${RNS_SOCK} itself."
elif ! command -v ss >/dev/null 2>&1; then
    err "ss (iproute2) not found - cannot see who owns ${RNS_SOCK};" \
        "starting UNCHECKED (#69 boot race possible)."
else
    deadline=$(( SECONDS + RNSD_WAIT_S ))
    owner="nobody (socket absent)"
    while :; do
        read_rnsd_unit
        mainpid="$(prop MainPID)"
        row="$(ss -xHnlp 2>/dev/null \
            | awk -v s="${RNS_SOCK}" '$5 == s' | head -n 1 || true)"
        ok=false
        if [[ -z "${RNSD_SCOPE}" ]]; then
            owner="UNKNOWN (systemctl unreadable - rnsd state unobservable)"
        elif [[ -n "${row}" && "${row}" == *users:* ]]; then
            # Every ("name",pid=N) holder; any one being rnsd is the owner.
            owner=""
            while IFS= read -r h; do
                [[ -n "${h}" ]] || continue
                hname="${h#(\"}"; hname="${hname%%\"*}"
                hpid="${h##*pid=}"
                if [[ "${hname}" == "rnsd" || ( -n "${hpid}" \
                        && "${mainpid:-0}" != "0" && "${hpid}" == "${mainpid}" ) ]]; then
                    ok=true; break
                fi
                owner="${owner:+${owner}, }${hname} pid ${hpid}"
            done < <(grep -oE '\("[^"]*",pid=[0-9]+' <<<"${row}" || true)
            owner="${owner:-unparseable holder: ${row##*users:}} (not rnsd)"
        elif [[ -n "${row}" ]]; then
            if [[ "$(prop ActiveState)" == "active" ]]; then
                err "owner of ${RNS_SOCK} not visible to $(id -un);" \
                    "rnsd.service is active - ASSUMING it is rnsd's, unverified."
                ok=true
            else
                owner="not visible to $(id -un), rnsd $(prop ActiveState)"
            fi
        fi
        "${ok}" && break
        if (( SECONDS >= deadline )); then
            err "rnsd has not taken ${RNS_SOCK} in ${RNSD_WAIT_S}s - owner:" \
                "${owner}."
            err "  Refusing to start: MeshChatX would become the shared-instance"
            err "  host and rnsd its client (#69). Restart= retries in a moment."
            err "  CHECK: sudo ss -xnlp | grep '@rns/'   (owner must be rnsd)"
            err "         systemctl status rnsd"
            err "  REPAIR ORDER: stop the squatter -> restart rnsd -> start it."
            exit "${EXIT_RNSD_NOT_OWNER}"
        fi
        sleep 1
    done
fi

# --------------------------------------------------------------------
# 6. Exec MeshChatX with canonical flags
# --------------------------------------------------------------------
# --headless        : no Electron/browser auto-launch
# --host            : MCX_HOST above (default 127.0.0.1)
# --port 8000       : free in MeshAnchor (config_api on 8081, map on 5000)
# --no-https        : self-signed cert friction is a UX cost we pay
#                     only when exposing beyond localhost
# --storage-dir     : pin to ~/.local/share/meshchatx so the service
#                     doesn't depend on cwd
exec "${MCX_BIN}" \
    --headless \
    --host "${MCX_HOST}" \
    --port 8000 \
    --no-https \
    --storage-dir "${STORAGE_DIR}"
