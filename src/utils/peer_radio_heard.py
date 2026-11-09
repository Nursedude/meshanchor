"""This box's RADIO hearing time, as a peer exports it (MAP-H H14 peer half).

Port of MeshForge ``utils.peer_radio_heard`` (9672ca99), PEER HALF ONLY: a
MeshForge manager federates this box's ``/api/nodes/directory`` and judges
each row's ``radio_heard`` (``peer_radio_trust`` / ``peer_heard_verdict`` live
in MeshForge; MeshAnchor runs no federation manager). ``node_history`` uses
``coerce_radio_ts`` to refuse a value that cannot be a hearing time before it
is ever stored.

Kept byte-for-byte equal to MeshForge's ``coerce_radio_ts`` — the manager
re-applies the same rule on ingest, so the two must not drift (hfm #5).
"""

import math
from typing import Any, Optional

from utils._map_collector_config import _ONLINE_FUTURE_SKEW_TOLERANCE_S


def strip_radio_stamp(features: Any) -> Any:
    """Drop ``radio_last_heard`` from features that did NOT come off this
    box's radio drain THIS cycle (a cache replay, a relayed aggregate), in
    place; returns ``features``. The stamp is a claim about one drain; a
    replayed copy keeps its first ``source_origin`` tag (``local_radio``), so
    without this its old stamp would read as a hearing now (2026-11-09: the
    MeshForge manager's live cache held 222 such features). Same as
    MeshForge's ``strip_radio_stamp``."""
    for f in features or ():
        props = f.get("properties") if isinstance(f, dict) else None
        if isinstance(props, dict):
            props.pop("radio_last_heard", None)
    return features


def coerce_radio_ts(value: Any, now: float) -> Optional[float]:
    """A plausible hearing epoch, or None. Rejects bool, non-finite, <= 0, and
    anything more than the skew tolerance in the future (a future stamp would
    read fresh forever)."""
    if isinstance(value, bool):
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ts) or ts <= 0:
        return None
    try:
        if not math.isfinite(now) or ts > now + _ONLINE_FUTURE_SKEW_TOLERANCE_S:
            return None
    except TypeError:
        return None
    return ts
