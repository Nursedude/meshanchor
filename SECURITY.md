# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.5.x   | :white_check_mark: |
| < 0.5   | :x:                |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security issues by emailing the maintainers directly. You should receive a response within 48 hours. If the issue is confirmed, we will release a patch as soon as possible depending on complexity.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Security Measures

MeshAnchor implements the following security measures as of v0.5.4-beta:

### Coding Standards (Linter-Enforced)

MeshAnchor's custom linter (`scripts/lint.py`) enforces ten security rules (MF001-MF010):

| Rule | Description | Severity |
|------|-------------|----------|
| **MF001** | No `Path.home()` — use `get_real_user_home()` for sudo compatibility | Error |
| **MF002** | No `shell=True` in subprocess calls — use argument lists | Error |
| **MF003** | No bare `except:` — always specify exception type | Warning |
| **MF004** | All `subprocess.run()`/`call()` must include `timeout` | Warning |
| **MF005** | *(Removed)* — was GTK4 thread dispatch (GTK4 removed in v0.5.x) | Retired |
| **MF006** | No `safe_import()` for first-party modules — use direct imports | Error |
| **MF007** | No direct `TCPInterface()` — use connection manager or acquire lock | Error |
| **MF008** | No raw `systemctl` for service state — use `service_check` helpers | Warning |
| **MF009** | `RNS.Reticulum()` must include `configdir=` parameter | Error |
| **MF010** | No `time.sleep()` in daemon loops — use `_stop_event.wait()` | Warning |

### Command Injection Prevention

- No `os.system()` calls — all commands use `subprocess.run()` with argument lists
- No `shell=True` in subprocess calls
- `shlex.split()` for parsing user-provided commands where needed
- All subprocess calls include timeout parameters

### Input Validation

All user input is validated before use:

- **Message content**: 230-byte limit, UTF-8 validation
- **Node IDs**: Hexadecimal format validation (8-16 characters)
- **File paths**: Path traversal prevention
- **YAML parsing**: All uses of `yaml.safe_load()` (no unsafe `yaml.load()`)
- **Journalctl time parameters**: Whitelist validation with safe patterns only

### Privilege Separation

- **Viewer Mode** (default, no sudo): Monitoring, RF calculations, API data
- **Admin Mode** (sudo): Service control, `/etc/` configuration, hardware
- `get_real_user_home()` ensures config files target the real user's home under sudo
- Service operations use `utils/service_check.py` as single source of truth

### Network Security

- Default binding to `127.0.0.1` (localhost only)
- HTTPS for external API calls (NOAA SWPC, GitHub)
- Local services (Prometheus, Grafana, HamClock) use HTTP on localhost only
- SSL certificate verification enabled by default in agent protocol

### Secure Defaults

- Services bind to localhost by default
- Sensitive operations require confirmation via TUI dialogs
- No secrets stored in code or committed config files

### Unsafe Deserialization Prevention

- No `pickle.loads()` usage
- No `eval()` or `exec()` in production code
- All SQL uses parameterized queries with `?` placeholders

## Security Audit History

### v0.5.4-beta (2026-03-02) - Routine Security Review

Full codebase audit (285 Python files) using automated linter (`scripts/lint.py --all`), test suite, and manual analysis.

| Category | Result |
|----------|--------|
| Linter errors | 0 |
| Linter warnings | 7 (all MF010 — `time.sleep()` in daemon loops) |
| Tests passing | 2,459 of 2,474 (17 skipped) across 67 files |
| `shell=True` / `eval()` / `exec()` | None |
| Hardcoded secrets | None |
| SQL injection vectors | None (parameterized queries throughout) |
| `Path.home()` violations | None |
| Installation paths | All 6 deployment profiles verified |

**MF010 locations** (non-critical, graceful shutdown improvement):
- `src/launcher_tui/handlers/rns_diagnostics.py` (4 instances)
- `src/launcher_tui/handlers/rns_monitor.py` (1 instance)
- `src/utils/message_listener.py` (1 instance)
- `src/utils/telemetry_poller.py` (1 instance)

### v0.5.4-beta (2026-02-21) - Comprehensive Security Review

Full codebase audit (274 Python files, 153K lines) using automated linter, auto-review system, and manual grep analysis across all OWASP categories.

| Finding | Severity | Status |
|---------|----------|--------|
| stderr file handle not context-managed (`main.py`) | Low | Fixed |
| `webbrowser.open()` with f-string file paths | Low | Fixed |
| SECURITY.md version/feature drift | High (docs) | Fixed |

**Clean audit results:**
- 0 linter violations (MF001-MF006; MF007-MF010 not yet implemented at time of audit)
- No `shell=True`, `os.system()`, `eval()`, `exec()`, `pickle.loads()`
- No hardcoded secrets or API keys
- All YAML uses `safe_load`, all SQL uses parameterized queries
- SSL verification defaults to `True`

### v0.4.2 (2026-01-03) - Initial Security Audit

| Issue | Severity | Status |
|-------|----------|--------|
| Journalctl time parameter injection | Critical | Fixed |
| Insecure default binding (0.0.0.0) | High | Fixed |
| Missing security headers | High | Fixed |
| TUI command injection via split() | High | Fixed |
| Missing message validation | Medium | Fixed |

## Development Security Guidelines

When contributing to MeshAnchor, follow these security practices:

### Do

- Use `subprocess.run()` with argument lists and `timeout`
- Validate all user input before use
- Use `get_real_user_home()` from `utils/paths.py` instead of `Path.home()`
- Use `check_service()` from `utils/service_check.py` for service operations
- Use `yaml.safe_load()` for YAML parsing
- Use parameterized queries for SQL
- Bind to `127.0.0.1` by default

### Don't

- Use `os.system()` or `os.popen()`
- Use `shell=True` in subprocess
- Use `Path.home()` directly (breaks under sudo)
- Trust user input without validation
- Store secrets in code or config files
- Use `eval()`, `exec()`, or `pickle.loads()`
- Use `safe_import()` for first-party modules

### Code Examples

**Safe subprocess usage:**
```python
# Good
subprocess.run(["journalctl", "-u", "meshtasticd", "--since", validated_time], timeout=30)

# Bad - never do this
os.system(f"journalctl --since {user_input}")
subprocess.run(f"journalctl --since {user_input}", shell=True)
```

**Safe path handling under sudo:**
```python
# Good - works correctly with sudo
from utils.paths import get_real_user_home
config = get_real_user_home() / ".config" / "meshanchor" / "settings.json"

# Bad - returns /root when running with sudo
config = Path.home() / ".config" / "meshanchor" / "settings.json"
```

**Input validation:**
```python
# Good
def validate_node_id(node_id):
    if not node_id:
        return False
    if not re.match(r'^[a-fA-F0-9]{8,16}$', node_id):
        return False
    return True

# Bad - trusting user input
node_id = request.form.get('node_id')
send_message(node_id, message)  # No validation!
```

## Security Contact

For security concerns, contact the maintainers through GitHub.

## Acknowledgments

Thanks to security researchers and contributors who help keep MeshAnchor secure.

---
*Made with aloha for the mesh community*
