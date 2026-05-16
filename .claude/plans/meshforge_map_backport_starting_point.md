# MeshForge map-fix backport — starting point

> Picking up from session that landed PRs #85–#88 in MeshAnchor on 2026-05-07.
> Goal: backport the relevant fixes to MeshForge (sister project at `/opt/meshforge`),
> with eyes-open on which PRs apply where.

---

## TL;DR — order to attack

1. **#88 (gzip) → MeshForge `:5000`** — biggest fleet-wide bandwidth win, lowest risk, no UX surface.
2. **#85 (SWR collector) → MeshForge** — bug-fix-class. Same 30s = 30s mismatch almost certainly applies; confirmed via 10s curl timeout on `:5000`.
3. **#86/#87 (cluster fixes) → MeshForge** — only if MeshForge's `node_map.html` has the same gaps (likely yes for #86 patterns; #87 depends on whether reticulum-tagged features show up there).
4. **meshforge-maps `:8808`** — **skip #88** (already gzipped). Audit for #85-class issues separately.

Stop after each PR; verify on a real browser before moving on. The 4-PR cascade in MeshAnchor was driven by symptoms surfaced after the previous fix shipped — same will happen here.

---

## Reference — what shipped in MeshAnchor

| PR | Class | File(s) | Key identifiers to grep |
|---|---|---|---|
| [#85](https://github.com/Nursedude/meshanchor/pull/85) | bug-fix | `src/utils/map_data_collector.py` | `_refresh_in_background`, `FORCE_CLUSTER_NETWORKS` (no — that's #86), `max_age_seconds == 0` |
| [#86](https://github.com/Nursedude/meshanchor/pull/86) | feature-coupled | `web/node_map.html` | `FORCE_CLUSTER_NETWORKS`, `filter-meshcore`, `stat-meshcore`, `mustCluster` |
| [#87](https://github.com/Nursedude/meshanchor/pull/87) | feature-coupled | `web/node_map.html` | `NETWORK_ALIASES`, the expanded set in `FORCE_CLUSTER_NETWORKS` |
| [#88](https://github.com/Nursedude/meshanchor/pull/88) | perf | `src/utils/map_http_handler.py` | `_maybe_gzip`, `_accepts_gzip`, `_GZIP_MIN_BYTES` |

Memory entry with diagnostic details:
`~/.claude/projects/-opt-meshanchor/memory/project_meshcore_map_render_fix.md`

---

## Audit BEFORE porting (per `feedback_audit_before_porting_from_sister_project.md`)

MeshForge's `map_http_handler.py` is **1572 lines vs MeshAnchor's 1221**. Don't drop-in-replace the file; the projects have diverged. Classes of divergence to expect:

- MeshForge has `map_federation.py` and `map_metrics.py` that MeshAnchor doesn't
- Phase 6.1 originated in MeshForge, then was backported
- MeshForge probably has its own `_serve_*` methods for federation endpoints

Concrete audit step before each PR:

```bash
# Side-by-side diff of the relevant files
diff -u /opt/meshanchor/src/utils/map_data_collector.py \
        /opt/meshforge/src/utils/map_data_collector.py | less

diff -u /opt/meshanchor/src/utils/map_http_handler.py \
        /opt/meshforge/src/utils/map_http_handler.py | less

diff -u /opt/meshanchor/web/node_map.html \
        /opt/meshforge/web/node_map.html | less
```

What you're looking for:
- Has MeshForge already addressed any of these? (Check `git log -- <file>` in /opt/meshforge for SWR/gzip/cluster keywords)
- Does the existing `collect()` / `_serve_json` / `_serve_geojson` shape match? Apply the same surgery, not a copy-paste.
- Any extra concerns specific to MeshForge (e.g., does federation aggregator have its own caching that conflicts with SWR)?

---

## Per-PR notes

### #85 SWR — `MapDataCollector.collect()`

**Confirmed applicable**: `curl -sS --max-time 10 http://127.0.0.1:5000/api/nodes/geojson` timed out at 10s on VolcanoAI's MeshForge. Same cold-collect block.

The fix is a stale-while-revalidate rewrite of `collect()`. Two key invariants from MeshAnchor:
- `max_age_seconds=0` MUST stay synchronous (tests + exporters depend on this).
- Background-refresh thread releases the lock in `finally:` — exception handling matters.

If MeshForge's collector has different caching (separate L1/L2, federation cache, etc.), wire SWR into the in-memory layer; don't disturb federation logic.

### #86 + #87 Cluster fixes — `web/node_map.html`

**Possibly already partially fixed in MeshForge** — Phase 6.1 originated there; they may have already addressed the cluster gaps. Check first:

```bash
grep -n "FORCE_CLUSTER\|filter-meshcore\|NETWORK_ALIASES" /opt/meshforge/web/node_map.html
```

If empty, port both #86 and #87 together. The expanded `FORCE_CLUSTER_NETWORKS` set from #87 is what you want — don't ship the narrow #86 version first and call it done.

### #88 gzip — `MapHTTPHandler._maybe_gzip`

**Cleanest backport**. ~50-line stdlib-only change (just `import gzip` + helper). Three call sites in MeshAnchor:
- `_serve_json` (covers most JSON endpoints)
- `_serve_geojson` (collapsed onto `_serve_json`)
- `_serve_map` (HTML)

MeshForge's handler may have additional `_serve_*` methods for federation/metrics — apply the same one-liner pattern (`payload, encoding = self._maybe_gzip(data)`) to each. The helper stays unchanged.

Verify after deploy:

```bash
curl -sS --compressed -D - -o /dev/null --max-time 30 \
  -w 'time %{time_total}s | size %{size_download}\n' \
  http://127.0.0.1:5000/api/nodes/geojson \
  | grep -iE "Content-Encoding|Vary|^time |size "
```

Expect `Content-Encoding: gzip`, `Vary: Accept-Encoding`, ~7-10x size reduction.

### meshforge-maps `:8808` — skip #88

Already serves gzip + ETag + Cache-Control:

```
Server: MeshForge-Maps/1.0
Content-Length: 3,711,453
ETag: "b40ec51a78b11c645fffb4d16157a9a5"
Content-Encoding: gzip
```

Different stack from SimpleHTTPRequestHandler (probably Flask or aiohttp). If you want SWR-class fixes there, audit `/opt/meshforge-maps/src/main.py` separately — different code, different patterns.

---

## Branch + PR conventions for MeshForge

Follow MeshForge's existing flow (different from MeshAnchor's `claude/<branch>` pattern). Check `git log` for recent PR style:

```bash
cd /opt/meshforge && git log --oneline --merges -10
```

Recent merges seen: WAL checkpoint, leaderboard fix, cloud :8808 scaffold. Pattern looks similar (claude/ branches → squash-merge). Confirm before pushing.

---

## Open questions (resolve before porting)

1. **Does MeshForge already have meshcore.dev directory pickup?** If yes, the cluster-overflow symptom is real there too. If no, #86/#87 are pre-emptive — still right to ship but lower urgency.
2. **Does federation aggregator (`map_federation.py`) have its own caching layer?** That changes how SWR should be wired.
3. **Is `:8808` (meshforge-maps) ever queried by MeshForge's `:5000`?** If yes, an SWR fix on `:5000` propagates load patterns upstream — coordinate.
4. **Backport order**: should #88 (gzip) ship first independently, or hold for the whole stack? Recommendation: ship #88 first standalone — pure perf, no dependencies, immediate fleet-wide win.

---

## Resume command

```bash
cd /opt/meshforge && \
  git status -s && \
  git log --oneline -3 && \
  diff -u /opt/meshanchor/src/utils/map_http_handler.py \
          /opt/meshforge/src/utils/map_http_handler.py | head -200
```

That gives: clean-tree check, recent activity check, and the first concrete diff to read. Start with #88 (gzip) on `:5000` since it's the highest-confidence win.
