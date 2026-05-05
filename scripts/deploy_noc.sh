#!/usr/bin/env bash
#
# deploy_noc.sh — single-shot NOC deploy (Track B3 of the road map).
#
# Installs the three meshanchor systemd unit files, runs daemon-reload,
# enables the daemon + map units (NO auto-start so the operator confirms
# port ownership before flipping the switch), and drops the cron file
# that schedules the per-host soak + fleet aggregator.
#
# What this script DOES NOT do:
#   - Install the sudoers grant (use scripts/install_sudoers.sh; that
#     bootstrap is one-time and lives in its own validator).
#   - Start any service. Starting meshanchor-map without verifying
#     port ownership = potential MeshForge :5000/:5001 stomp during
#     the coexist field test. Operator runs `systemctl start ...`
#     after eyeballing `ss -tlnp`.
#   - Stop or remove MeshForge. The cutover comes at the end of the
#     2-week field test, not now.
#
# Pre-requisites:
#   - PR #57 (freshness), #58 (sudoers), #59 (coexist port) merged into
#     main, and `git pull --ff-only` already run on this host.
#   - scripts/install_sudoers.sh already executed (so unattended cron
#     restarts work).
#
# Usage:
#   sudo bash scripts/deploy_noc.sh           # install + enable
#   sudo bash scripts/deploy_noc.sh --dry-run # preview, change nothing
#
# Idempotent: re-running on an already-deployed host is a no-op except
# for `daemon-reload` (which is harmless to re-run).
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

# Operator account that owns the soak reports + receives cron mail.
# Edit if you deploy under a different user.
OPERATOR="${MESHANCHOR_OPERATOR:-wh6gxz}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="/etc/systemd/system"
CRON_PATH="/etc/cron.d/meshanchor-soak"

UNITS=(
    "meshanchor.service"
    "meshanchor-daemon.service"
    "meshanchor-map.service"
)

# Units that should be started by systemd at boot. meshanchor.service is
# the TUI launcher and doesn't make sense as a background service — it's
# left available for `systemctl start` on demand but never auto-enabled.
ENABLE_UNITS=(
    "meshanchor-daemon.service"
    "meshanchor-map.service"
)

log() { printf '[deploy_noc] %s\n' "$*"; }
run() {
    if [[ ${DRY_RUN} -eq 1 ]]; then
        printf '  would run: %s\n' "$*"
    else
        "$@"
    fi
}

if [[ ${EUID} -ne 0 && ${DRY_RUN} -eq 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    echo "       use --dry-run to preview without sudo" >&2
    exit 1
fi

if ! id -u "${OPERATOR}" >/dev/null 2>&1; then
    echo "error: operator user '${OPERATOR}' does not exist on this host" >&2
    echo "       set MESHANCHOR_OPERATOR=<user> if you deploy under a different name" >&2
    exit 1
fi

# Pre-flight: source files must exist. Catches the "PRs not merged yet"
# class of error before we touch /etc — much easier to recover from a
# clear error than from a half-installed state.
log "pre-flight checks"
for u in "${UNITS[@]}"; do
    src="${REPO_ROOT}/scripts/${u}"
    [[ -f "${src}" ]] || { echo "error: missing ${src}" >&2; exit 1; }
done

soak_script="${REPO_ROOT}/scripts/boundary_soak.py"
fleet_script="${REPO_ROOT}/scripts/boundary_soak_fleet.py"
[[ -f "${soak_script}" ]] || { echo "error: missing ${soak_script}" >&2; exit 1; }
[[ -f "${fleet_script}" ]] || { echo "error: missing ${fleet_script}" >&2; exit 1; }

# Defensive: confirm the freshness self-check is in this checkout.
# A deploy that ships boundary_soak.py without check_liveness would
# silently re-introduce the silent-failure mode this stack is built
# to close.
if ! grep -q '^def check_liveness' "${soak_script}"; then
    echo "error: ${soak_script} does not contain check_liveness()" >&2
    echo "       PR #57 (freshness self-check) is not merged on this branch" >&2
    exit 1
fi

# Defensive: confirm meshanchor-map.service is on the coexist port.
# If we install the unit with --port 5000 while MeshForge is running
# there, systemd will start, fail, and crash-loop. Better to fail loud
# at deploy time.
if ! grep -q -- '--port 5002' "${REPO_ROOT}/scripts/meshanchor-map.service"; then
    echo "error: meshanchor-map.service is not on the coexist port (5002)" >&2
    echo "       PR #59 (coexist port) is not merged on this branch" >&2
    echo "       (if you intentionally cut over, edit this script and remove" >&2
    echo "        this guard before re-running)" >&2
    exit 1
fi

# Sudoers sanity check — warn but don't block. Deploy can proceed
# without sudoers; cron-driven recovery just won't be unattended.
if ! sudo -n -U "${OPERATOR}" -l 2>/dev/null | grep -q "MESHANCHOR_SVC_RESTART\|systemctl restart meshanchor"; then
    log "warning: ${OPERATOR} does not appear to have NOPASSWD systemctl on the meshanchor units"
    log "         run scripts/install_sudoers.sh after this deploy for unattended recovery"
fi

# Install system packages the daemon needs at runtime:
#   - python3-paho-mqtt: MQTT client library used by mqtt_subscriber.
#   - mosquitto: local broker the daemon connects to. Without it the
#     subscriber attempts to connect to a missing :1883 and observability
#     silently goes dark — the exact silent-failure mode this stack is
#     built to surface, not absorb.
# apt path because the user's environment denies system pip3 install
# (and because apt is the right tool for system packages anyway).
# If apt is not available (non-Debian host), warn and continue.
if command -v apt-get >/dev/null 2>&1; then
    log "installing system packages (mosquitto, python3-paho-mqtt)"
    run apt-get install -y mosquitto python3-paho-mqtt
else
    log "warning: apt-get not found; install mosquitto + python3-paho-mqtt manually for MQTT observability"
fi

# Provision mosquitto's safe-default config: localhost-only listener,
# anonymous allowed. This is the SAFE-AND-FUNCTIONAL pair — nothing
# remote can reach the broker, so anonymous is fine. Public exposure
# (binding 0.0.0.0) must come as a deliberate operator change that
# bundles the auth/TLS setup in the SAME action; the deploy script
# does not provide a one-flag "make it public" path because that's
# how production hosts end up as open relays. See
# scripts/mosquitto-localhost.conf for the in-repo source of truth.
mosquitto_conf_src="${REPO_ROOT}/scripts/mosquitto-localhost.conf"
mosquitto_conf_dst="/etc/mosquitto/conf.d/01-meshanchor.conf"
if [[ -f "${mosquitto_conf_src}" ]] && command -v mosquitto >/dev/null 2>&1; then
    log "installing ${mosquitto_conf_dst} (localhost-only, anonymous allowed)"
    run install -m 0644 -o root -g root "${mosquitto_conf_src}" "${mosquitto_conf_dst}"
    log "enabling + restarting mosquitto"
    run systemctl enable mosquitto.service
    # restart (not reload): mosquitto's SIGHUP reload only picks up
    # log/persistence/auth changes, NOT listener changes (per the
    # mosquitto manual). Since our conf.d sets `listener 1883 127.0.0.1`,
    # a reload would silently leave the old listeners active. Restart
    # is the only way to make the safe-default listener take effect.
    # Brief MQTT connection blip on idempotent re-runs is acceptable
    # vs. shipping a deploy that doesn't actually deploy.
    run systemctl restart mosquitto.service
elif [[ ! -f "${mosquitto_conf_src}" ]]; then
    log "warning: ${mosquitto_conf_src} missing; skipping mosquitto config drop"
fi

log "installing systemd units"
for u in "${UNITS[@]}"; do
    src="${REPO_ROOT}/scripts/${u}"
    dst="${UNIT_DIR}/${u}"
    run install -m 0644 -o root -g root "${src}" "${dst}"
done

log "systemctl daemon-reload"
run systemctl daemon-reload

log "enabling units (no auto-start): ${ENABLE_UNITS[*]}"
for u in "${ENABLE_UNITS[@]}"; do
    run systemctl enable "${u}"
done

# Cron file: per-host soak every 6h, fleet aggregator every 24h at 07:15
# UTC. Both run as the operator so reports land in their home dir.
# /etc/cron.d format requires the username column, so this naturally
# scopes the run to that user without needing crontab editing.
log "installing ${CRON_PATH}"
cron_content="$(cat <<EOF
# /etc/cron.d/meshanchor-soak — boundary observability schedule
# Installed by deploy_noc.sh. Edit MESHANCHOR_OPERATOR= if user changes.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=${OPERATOR}

# Per-host soak every 6h. Exits non-zero on liveness failure (cron mail).
0 */6 * * * ${OPERATOR} /usr/bin/python3 /opt/meshanchor/scripts/boundary_soak.py

# Fleet rollup once a day at 07:15 UTC. Add --host <peer> args for
# multi-host fleets. Exits non-zero on stale or unreachable hosts.
15 7 * * * ${OPERATOR} /usr/bin/python3 /opt/meshanchor/scripts/boundary_soak_fleet.py
EOF
)"
if [[ ${DRY_RUN} -eq 1 ]]; then
    printf '  would install %s with content:\n' "${CRON_PATH}"
    printf '  ---\n%s\n  ---\n' "${cron_content}"
else
    printf '%s\n' "${cron_content}" | install -m 0644 -o root -g root /dev/stdin "${CRON_PATH}"
fi

log "done"
echo
echo "Next steps (manual, in this order):"
echo "  1. ss -tlnp | grep -E ':500[0-3]'   # confirm port plan before starting"
echo "  2. sudo systemctl start meshanchor-daemon.service"
echo "  3. sudo systemctl start meshanchor-map.service"
echo "  4. sudo systemctl status meshanchor-daemon meshanchor-map --no-pager"
echo "  5. journalctl -u meshanchor-daemon --since '1 min ago' | grep 'rpc\\['"
echo
echo "Cron schedule installed at ${CRON_PATH}; first soak run at the next 6h boundary."
