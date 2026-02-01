# NGINX Reliability Patterns for MeshForge

> **Research Date**: 2026-02-01
> **Author**: Claude Code (Opus 4.5)
> **Session**: `claude/nginx-meshforge-research-MXS2U`

## Executive Summary

Deep analysis of the [NGINX GitHub organization](https://github.com/nginx) reveals several patterns that can enhance MeshForge's reliability and functionality as a mesh network NOC. MeshForge already has sophisticated health monitoring and reliability infrastructure—this research identifies **gaps and enhancements** based on proven NGINX patterns.

### Key NGINX Projects Analyzed

| Project | Stars | Relevance to MeshForge |
|---------|-------|------------------------|
| [nginx](https://github.com/nginx/nginx) | 29.2k | Core reliability patterns, health checks |
| [nginx-agent](https://github.com/nginx/agent) | 345 | Remote management, metrics collection |
| [kubernetes-ingress](https://github.com/nginx/kubernetes-ingress) | 5k | Load balancing, retry policies |
| [nginx-gateway-fabric](https://github.com/nginx/nginx-gateway-fabric) | 936 | Gateway API patterns |
| nginx-unit (archived) | - | Dynamic reconfiguration patterns |

---

## Pattern Comparison: NGINX vs MeshForge

### What MeshForge Already Does Well

MeshForge has strong foundations (see `src/utils/service_check.py`, `src/gateway/bridge_health.py`):

- **Error classification**: Transient vs permanent error patterns
- **Exponential backoff with jitter**: In `src/gateway/reconnect.py`
- **Health scoring**: Multi-component 0-100 scoring in `src/utils/health_score.py`
- **Bounded memory monitoring**: Deques with maxlen
- **Actionable fix hints**: Specific recovery instructions
- **Message persistence**: SQLite-backed message queue with lifecycle tracking

### Gaps & Enhancement Opportunities

---

## Recommended Patterns from NGINX

### 1. Active Health Checks (High Priority)

**NGINX Pattern**: Periodic probes independent of traffic flow

```nginx
# NGINX active health check
health_check interval=5s fails=3 passes=2;
```

**Current MeshForge Gap**: Health checks are primarily passive (triggered by operations). No dedicated health probe thread for RNS/Meshtastic services.

**Proposed Implementation**:

```python
# src/utils/active_health_probe.py
class ActiveHealthProbe:
    """
    Proactive health checking for mesh services.
    Based on NGINX active health check pattern.
    """
    def __init__(self, interval: int = 30, fails: int = 3, passes: int = 2):
        self.interval = interval  # seconds between checks
        self.fails = fails        # consecutive failures to mark unhealthy
        self.passes = passes      # consecutive passes to mark healthy
        self._running = threading.Event()
        self._thread = None

    def check_meshtastic(self) -> HealthResult:
        """Probe meshtasticd with lightweight request."""
        try:
            # Use meshtastic CLI with timeout
            result = subprocess.run(
                ["meshtastic", "--info"],
                capture_output=True,
                timeout=5
            )
            return HealthResult(healthy=result.returncode == 0)
        except subprocess.TimeoutExpired:
            return HealthResult(healthy=False, reason="timeout")

    def check_rns(self) -> HealthResult:
        """Probe RNS shared instance."""
        try:
            # UDP probe to RNS port
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.sendto(b"probe", ("127.0.0.1", 37428))
            # Just check if port is open, RNS won't respond to garbage
            return HealthResult(healthy=True)
        except socket.error:
            return HealthResult(healthy=False, reason="port unreachable")
```

**Benefits**:
- Detect failures before user attempts connection
- Pre-emptive alerting in TUI status bar
- Better uptime metrics (not just reactive)

---

### 2. Zone-Based Shared State (Medium Priority)

**NGINX Pattern**: Worker processes share state via memory zones

```nginx
upstream backend {
    zone backend_zone 64k;  # Shared memory for health state
    server 192.168.1.1;
    server 192.168.1.2;
}
```

**Current MeshForge Gap**: Health state is per-process. If gateway and TUI run separately, they don't share health observations.

**Proposed Implementation**:

```python
# src/utils/shared_health_state.py
class SharedHealthState:
    """
    SQLite-backed shared health state for multi-process access.
    Similar to NGINX zone for shared worker state.
    """
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or get_real_user_home() / ".config/meshforge/health.db"
        self._init_db()

    def mark_service_state(self, service: str, state: str, timestamp: float = None):
        """Update service health state atomically."""
        with sqlite3.connect(self.db_path, timeout=5) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO health_state (service, state, updated_at)
                VALUES (?, ?, ?)
            """, (service, state, timestamp or time.time()))

    def get_service_state(self, service: str) -> Optional[dict]:
        """Get current service state (readable by any process)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT state, updated_at FROM health_state WHERE service = ?",
                (service,)
            ).fetchone()
            return {"state": row[0], "updated_at": row[1]} if row else None
```

**Benefits**:
- Gateway bridge can share health discoveries with TUI
- Multiple MeshForge instances see same health state
- Survives process restarts

---

### 3. Slow Start Recovery (Medium Priority)

**NGINX Pattern**: Gradually increase load to recovered server

```nginx
server 192.168.1.1 slow_start=30s;
```

**Current MeshForge Gap**: After reconnection, gateway immediately attempts full message throughput. Recently recovered Meshtastic radio can be overwhelmed.

**Proposed Enhancement** to `src/gateway/reconnect.py`:

```python
class SlowStartRecovery:
    """
    Gradually increase message throughput after service recovery.
    Prevents overwhelming recently-recovered Meshtastic radio.
    """
    def __init__(self, slow_start_seconds: int = 30):
        self.slow_start_seconds = slow_start_seconds
        self.recovery_time: Optional[float] = None

    def start_recovery(self):
        """Mark start of recovery period."""
        self.recovery_time = time.time()

    def get_throughput_multiplier(self) -> float:
        """
        Returns 0.0-1.0 multiplier for message sending rate.
        Linearly increases from 0.1 to 1.0 over slow_start_seconds.
        """
        if self.recovery_time is None:
            return 1.0

        elapsed = time.time() - self.recovery_time
        if elapsed >= self.slow_start_seconds:
            self.recovery_time = None
            return 1.0

        # Linear ramp from 0.1 to 1.0
        return 0.1 + (0.9 * (elapsed / self.slow_start_seconds))
```

**Usage in message sender**:
```python
def send_message(self, message):
    multiplier = self.slow_start.get_throughput_multiplier()
    delay = self.base_delay / multiplier  # Longer delays during recovery
    time.sleep(delay)
    self._do_send(message)
```

---

### 4. Dynamic Configuration API (Lower Priority but Strategic)

**NGINX Unit Pattern**: RESTful JSON API for live configuration changes

```bash
# NGINX Unit dynamic config
curl -X PUT --unix-socket /var/run/unit.sock \
    -d '{"listeners": {"*:8080": {"pass": "routes"}}}' \
    http://localhost/config
```

**Current MeshForge**: Configuration via `SettingsManager` (JSON file) requires restart for some changes.

**Proposed Enhancement**: Add internal REST API for dynamic reconfiguration

```python
# src/utils/config_api.py
class ConfigurationAPI:
    """
    Internal REST-like API for dynamic configuration.
    Based on NGINX Unit control API pattern.

    Enables:
    - TUI to update gateway config without restart
    - External tools to query/modify settings
    - Future: remote management agent
    """

    def __init__(self, settings_manager: SettingsManager):
        self.settings = settings_manager
        self.validators = {}  # Config key -> validator function

    def get(self, path: str) -> Any:
        """Get configuration value by path (e.g., 'gateway.rns.port')."""
        parts = path.split(".")
        value = self.settings._settings
        for part in parts:
            value = value.get(part, {})
        return value

    def put(self, path: str, value: Any) -> ConfigResult:
        """
        Update configuration value.
        Validates before applying, returns success/failure.
        """
        # Validate first (like NGINX Unit)
        if path in self.validators:
            validation = self.validators[path](value)
            if not validation.valid:
                return ConfigResult(success=False, error=validation.error)

        # Apply atomically
        parts = path.split(".")
        current = self.settings._settings
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

        self.settings.save()
        return ConfigResult(success=True)
```

**Benefits**:
- Zero-downtime config changes
- Validation before apply
- Foundation for future NGINX Agent-like management

---

### 5. Proxy Next Upstream (Retry Policy)

**NGINX Pattern**: Define which errors trigger retry to different upstream

```nginx
proxy_next_upstream error timeout http_502 http_503;
proxy_next_upstream_tries 3;
proxy_next_upstream_timeout 10s;
```

**Current MeshForge Gap**: Message retry logic exists but isn't configurable per-error-type.

**Proposed Enhancement** to `src/gateway/message_queue.py`:

```python
class RetryPolicy:
    """
    NGINX-style retry policy for message delivery.
    """
    # Errors that should trigger retry (transient)
    RETRIABLE_ERRORS = {
        "connection_reset",
        "connection_refused",
        "timeout",
        "temporarily_unavailable",
        "network_unreachable"
    }

    # Errors that should NOT retry (permanent)
    NON_RETRIABLE_ERRORS = {
        "permission_denied",
        "invalid_destination",
        "message_too_large",
        "authentication_failed"
    }

    def __init__(self, max_tries: int = 3, timeout: float = 30.0):
        self.max_tries = max_tries
        self.timeout = timeout

    def should_retry(self, error: str, attempt: int) -> RetryDecision:
        """Determine if error should trigger retry."""
        error_lower = error.lower()

        # Check against patterns
        for pattern in self.NON_RETRIABLE_ERRORS:
            if pattern in error_lower:
                return RetryDecision(retry=False, reason="permanent_error")

        for pattern in self.RETRIABLE_ERRORS:
            if pattern in error_lower:
                if attempt < self.max_tries:
                    return RetryDecision(retry=True, delay=2 ** attempt)
                return RetryDecision(retry=False, reason="max_attempts_exceeded")

        # Unknown errors: retry once
        return RetryDecision(retry=(attempt < 1), reason="unknown_error_type")
```

---

### 6. Metrics Export (Prometheus Format)

**NGINX Pattern**: `/metrics` endpoint in Prometheus exposition format

```
# NGINX Ingress Controller metrics
nginx_ingress_controller_requests_total{...} 12345
nginx_ingress_controller_upstream_latency_seconds{...} 0.025
```

**Current MeshForge**: Metrics stored in SQLite, no standard export format.

**Proposed Enhancement**:

```python
# src/utils/metrics_export.py
class PrometheusExporter:
    """
    Export MeshForge metrics in Prometheus format.
    Enables Grafana dashboards, alerting, etc.
    """

    def __init__(self, metrics_history: MetricsHistory):
        self.metrics = metrics_history

    def export(self) -> str:
        """Generate Prometheus exposition format."""
        lines = [
            "# HELP meshforge_messages_total Total messages processed",
            "# TYPE meshforge_messages_total counter",
        ]

        # Message counts
        summary = self.metrics.get_summary()
        lines.append(f'meshforge_messages_total{{direction="mesh_to_rns"}} {summary.mesh_to_rns}')
        lines.append(f'meshforge_messages_total{{direction="rns_to_mesh"}} {summary.rns_to_mesh}')

        # Health score
        lines.extend([
            "# HELP meshforge_health_score Current health score (0-100)",
            "# TYPE meshforge_health_score gauge",
            f"meshforge_health_score {summary.health_score}",
        ])

        # Per-node metrics
        for node in self.metrics.get_node_summaries():
            lines.append(f'meshforge_node_snr{{node="{node.id}"}} {node.avg_snr}')
            lines.append(f'meshforge_node_last_seen{{node="{node.id}"}} {node.last_seen}')

        return "\n".join(lines)
```

**Integration**: Add to TUI as "Export Metrics" option or serve via simple HTTP.

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 sessions)

| Enhancement | File | Effort |
|-------------|------|--------|
| Active health probes | New: `src/utils/active_health_probe.py` | Low |
| Slow start recovery | Modify: `src/gateway/reconnect.py` | Low |
| Configurable retry policy | Modify: `src/gateway/message_queue.py` | Low |

### Phase 2: Infrastructure (2-3 sessions)

| Enhancement | File | Effort |
|-------------|------|--------|
| Shared health state (SQLite) | New: `src/utils/shared_health_state.py` | Medium |
| Prometheus metrics export | New: `src/utils/metrics_export.py` | Medium |

### Phase 3: Strategic (Future)

| Enhancement | File | Effort |
|-------------|------|--------|
| Configuration API | New: `src/utils/config_api.py` | High |
| Remote management agent | New: `src/agent/` | High |

---

## Sources

- [NGINX GitHub Organization](https://github.com/nginx)
- [Active or Passive Health Checks: Which Is Right for You?](https://www.f5.com/company/blog/nginx/active-or-passive-health-checks-which-is-right-for-you)
- [NGINX Unit Control API](https://unit.nginx.org/controlapi/)
- [HTTP Load Balancing | NGINX Documentation](https://docs.nginx.com/nginx/admin-guide/load-balancer/http-load-balancer/)
- [NGINX Prometheus Exporter](https://github.com/nginx/nginx-prometheus-exporter)
- [NGINX Agent](https://github.com/nginx/agent)
- [NGINX Kubernetes Ingress Controller](https://github.com/nginx/kubernetes-ingress)

---

## Session Notes

**Key Takeaways**:
1. MeshForge already has strong reliability foundations - don't over-engineer
2. Active health probes are the highest-value addition
3. Slow start recovery prevents radio overwhelm after reconnection
4. Shared state enables multi-process coordination
5. Prometheus export enables ecosystem integration

**Session Entropy Status**: Low - research complete, no implementation started

**Next Steps for Follow-up Session**:
1. Implement `ActiveHealthProbe` class
2. Add slow start recovery to reconnection logic
3. Configure retry policy per error type

---

*Research conducted for MeshForge mesh network NOC project*
