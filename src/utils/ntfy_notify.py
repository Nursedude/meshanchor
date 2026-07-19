"""Push a MeshAnchor watchdog alert to an ntfy topic.

Ported in spirit from MeshForge's mini_dudeai NtfyAction (2026-07-18) so
MeshAnchor's fleet_watchdog can actively PAGE on a blackout transition instead
of only surfacing a dashboard banner. Same "don't fall silent" charter.

The fleet ntfy topic is NEVER hard-coded in source (MF014). It is read at
runtime from the environment (or an optional per-box config file). When no topic
is configured, ``publish`` is a safe no-op — the dashboard banner still works,
paging is simply off — so this can never crash a watchdog cycle or leak an
operator-specific topic into the repo.

Config (all optional; no topic => paging disabled):
    MESHANCHOR_NTFY_TOPIC       the ntfy topic to POST to
    MESHANCHOR_NTFY_BASE_URL    ntfy server (default https://ntfy.sh)
    MESHANCHOR_NTFY_TOKEN_ENV   NAME of an env var holding a bearer token
                                (keeps the secret out of config; never a literal)
Fallback file (if the env topic is unset): ~/.config/meshanchor/ntfy.json
    {"topic": "...", "base_url": "...", "token_env": "..."}
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("fleet.watchdog.ntfy")


def _config_file_values() -> dict:
    """Read ~/.config/meshanchor/ntfy.json if present. Never raises."""
    try:
        from utils.paths import get_real_user_home
        p = get_real_user_home() / ".config" / "meshanchor" / "ntfy.json"
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
    except Exception as e:  # config is best-effort; never break paging on it
        logger.debug("ntfy config file read failed: %s", e)
    return {}


def resolve_ntfy_config() -> Tuple[Optional[str], str, Optional[str]]:
    """Resolve (topic, base_url, token). ``topic`` is None when paging is not
    configured (env unset AND no config file) — the caller then no-ops."""
    cfg = None
    topic = os.environ.get("MESHANCHOR_NTFY_TOPIC", "").strip()
    if not topic:
        cfg = _config_file_values()
        topic = str(cfg.get("topic", "")).strip()

    base_url = os.environ.get("MESHANCHOR_NTFY_BASE_URL", "").strip()
    if not base_url:
        cfg = cfg if cfg is not None else _config_file_values()
        base_url = str(cfg.get("base_url", "")).strip()
    base_url = base_url or "https://ntfy.sh"

    token_env = os.environ.get("MESHANCHOR_NTFY_TOKEN_ENV", "").strip()
    if not token_env:
        cfg = cfg if cfg is not None else _config_file_values()
        token_env = str(cfg.get("token_env", "")).strip()
    token = os.environ.get(token_env, "").strip() if token_env else ""

    return (topic or None), base_url, (token or None)


def publish(
    title: str,
    message: str,
    *,
    priority: str = "default",
    tags: Optional[List[str]] = None,
    timeout_s: float = 8.0,
) -> bool:
    """POST to the configured ntfy topic. Returns True on a delivered request,
    False when paging is unconfigured (no-op) or the POST failed. Never raises —
    a paging failure must not sink a watchdog cycle."""
    topic, base_url, token = resolve_ntfy_config()
    if not topic:
        return False  # paging not configured -> dashboard-only
    url = f"{base_url.rstrip('/')}/{topic}"
    body = (message or "").encode("utf-8", "replace")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Title", title.encode("ascii", "replace").decode("ascii"))
        req.add_header("Priority", priority)
        if tags:
            req.add_header("Tags", ",".join(tags))
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
            r.read()
        return True
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        logger.warning("ntfy publish failed: %s: %s", type(e).__name__, e)
        return False
