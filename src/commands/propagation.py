"""
MeshForge Propagation Commands — Standalone Space Weather & HF Propagation

MeshForge-owned module for space weather and HF propagation data.
Uses NOAA SWPC as the PRIMARY data source (no external dependencies).
Optionally enhances with HamClock or OpenHamClock when available.

Data Sources (priority order):
    1. NOAA SWPC (primary, always available)
    2. OpenHamClock (optional, REST API on port 3000)
    3. HamClock (optional/legacy, REST API on port 8080/8082)

Usage:
    from commands import propagation

    # Get space weather (always works - uses NOAA)
    result = propagation.get_space_weather()

    # Get band conditions (derived from NOAA data)
    result = propagation.get_band_conditions()

    # Get propagation summary
    result = propagation.get_propagation_summary()

    # Configure optional enhanced sources
    propagation.configure_source(DataSource.OPENHAMCLOCK, host="localhost", port=3000)
"""

import logging
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from .base import CommandResult

logger = logging.getLogger(__name__)


class DataSource(Enum):
    """Available propagation data sources."""
    NOAA = "noaa"                  # NOAA SWPC (primary, always available)
    OPENHAMCLOCK = "openhamclock"  # OpenHamClock (optional REST API)
    HAMCLOCK = "hamclock"          # Original HamClock (legacy, optional)


@dataclass
class SourceConfig:
    """Configuration for a data source."""
    source: DataSource
    host: str = "localhost"
    port: int = 0
    enabled: bool = False
    timeout: int = 10

    @property
    def base_url(self) -> str:
        if self.source == DataSource.OPENHAMCLOCK:
            return f"http://{self.host}:{self.port}"
        elif self.source == DataSource.HAMCLOCK:
            return f"http://{self.host}:{self.port}"
        return ""


# Module-level source configuration
_sources: Dict[DataSource, SourceConfig] = {
    DataSource.NOAA: SourceConfig(
        source=DataSource.NOAA, enabled=True
    ),
    DataSource.OPENHAMCLOCK: SourceConfig(
        source=DataSource.OPENHAMCLOCK, port=3000, enabled=False
    ),
    DataSource.HAMCLOCK: SourceConfig(
        source=DataSource.HAMCLOCK, port=8080, enabled=False
    ),
}


def configure_source(
    source: DataSource,
    host: str = "localhost",
    port: int = 0,
    enabled: bool = True,
    timeout: int = 10,
) -> CommandResult:
    """Configure an optional data source.

    NOAA is always enabled. This configures HamClock/OpenHamClock as
    supplementary sources.

    Args:
        source: Which data source to configure
        host: Hostname or IP
        port: Port number (0 = use default)
        enabled: Whether to use this source
        timeout: Request timeout in seconds
    """
    if source == DataSource.NOAA:
        return CommandResult.ok("NOAA is always enabled as primary source")

    if not host:
        return CommandResult.fail("Host cannot be empty")

    default_ports = {
        DataSource.OPENHAMCLOCK: 3000,
        DataSource.HAMCLOCK: 8080,
    }
    actual_port = port or default_ports.get(source, 8080)

    if not (1 <= actual_port <= 65535):
        return CommandResult.fail(f"Invalid port: {actual_port}")

    _sources[source] = SourceConfig(
        source=source,
        host=host.strip(),
        port=actual_port,
        enabled=enabled,
        timeout=timeout,
    )

    return CommandResult.ok(
        f"{source.value} configured: {host}:{actual_port} (enabled={enabled})",
        data={
            'source': source.value,
            'host': host,
            'port': actual_port,
            'enabled': enabled,
        }
    )


def get_sources() -> CommandResult:
    """Get status of all configured data sources."""
    sources_info = {}
    for src, cfg in _sources.items():
        sources_info[src.value] = {
            'enabled': cfg.enabled,
            'host': cfg.host,
            'port': cfg.port,
        }

    return CommandResult.ok(
        "Data source configuration",
        data={'sources': sources_info}
    )


# ==================== Space Weather (NOAA Primary) ====================

def get_space_weather() -> CommandResult:
    """Get current space weather conditions.

    Uses NOAA SWPC as primary source. This always works without
    any external service dependencies.

    Returns:
        CommandResult with:
        - solar_flux: Solar Flux Index (SFU)
        - k_index: Kp index (0-9)
        - a_index: A index (daily average)
        - xray_flux: X-ray flux class (A/B/C/M/X)
        - geomag_storm: Geomagnetic storm level
        - band_conditions: Per-band HF condition assessment
        - source: Data source used
    """
    try:
        from utils.space_weather import SpaceWeatherAPI
    except ImportError:
        return CommandResult.fail(
            "Space weather module not available",
            error="utils.space_weather not found"
        )

    api = SpaceWeatherAPI(timeout=10)
    data = api.get_current_conditions()

    result_data = {
        'solar_flux': data.solar_flux,
        'sunspot_number': data.sunspot_number,
        'k_index': data.k_index,
        'a_index': data.a_index,
        'xray_flux': data.xray_flux,
        'xray_class': data.xray_class,
        'geomag_storm': data.geomag_storm.value,
        'band_conditions': {k: v.value for k, v in data.band_conditions.items()},
        'source': 'NOAA SWPC',
        'updated': data.updated.isoformat() if data.updated else None,
    }

    # Build summary
    parts = []
    if data.solar_flux:
        parts.append(f"SFI={int(data.solar_flux)}")
    if data.k_index is not None:
        parts.append(f"Kp={data.k_index}")
    if data.a_index is not None:
        parts.append(f"A={data.a_index}")

    summary = ", ".join(parts) if parts else "No data"

    return CommandResult.ok(
        f"Space weather: {summary} ({data.geomag_storm.value})",
        data=result_data
    )


def get_band_conditions() -> CommandResult:
    """Get HF band propagation conditions.

    Derived from NOAA SWPC data (SFI, Kp, A-index). No external
    service required.

    Returns:
        CommandResult with per-band condition assessments
    """
    try:
        from utils.space_weather import SpaceWeatherAPI
    except ImportError:
        return CommandResult.fail(
            "Space weather module not available",
            error="utils.space_weather not found"
        )

    api = SpaceWeatherAPI(timeout=10)
    data = api.get_current_conditions()

    bands = {k: v.value for k, v in data.band_conditions.items()}

    # Determine overall condition
    if data.k_index is not None and data.k_index >= 5:
        overall = "Disturbed"
    elif data.solar_flux and data.solar_flux >= 120:
        overall = "Good"
    elif data.solar_flux and data.solar_flux >= 90:
        overall = "Fair"
    elif data.solar_flux:
        overall = "Poor"
    else:
        overall = "Unknown"

    return CommandResult.ok(
        f"Band conditions: {overall} ({len(bands)} bands assessed)",
        data={
            'bands': bands,
            'overall': overall,
            'solar_flux': data.solar_flux,
            'k_index': data.k_index,
            'a_index': data.a_index,
            'source': 'NOAA SWPC',
        }
    )


def get_alerts() -> CommandResult:
    """Get active space weather alerts from NOAA.

    Returns:
        CommandResult with list of active alerts
    """
    import urllib.request
    import json

    url = "https://services.swpc.noaa.gov/products/alerts.json"

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        alerts = []
        for alert in data[:10]:
            alerts.append({
                'message': alert.get('message', ''),
                'issue_datetime': alert.get('issue_datetime', ''),
            })

        return CommandResult.ok(
            f"{len(alerts)} space weather alerts",
            data={'alerts': alerts, 'count': len(alerts), 'source': 'NOAA SWPC'}
        )
    except Exception as e:
        return CommandResult.fail(f"Alerts fetch failed: {e}")


def get_propagation_summary() -> CommandResult:
    """Get a one-line propagation summary.

    Works entirely from NOAA data - no external dependencies.

    Returns:
        CommandResult with summary string and overall assessment
    """
    try:
        from utils.space_weather import SpaceWeatherAPI
    except ImportError:
        return CommandResult.fail("Space weather module not available")

    api = SpaceWeatherAPI(timeout=10)
    summary_str = api.get_quick_summary()
    data = api.get_current_conditions()

    # Overall assessment
    overall = "Unknown"
    try:
        sfi = data.solar_flux or 0
        kp = data.k_index or 0

        if kp >= 5:
            overall = "Disturbed"
        elif sfi >= 120 and kp < 3:
            overall = "Excellent"
        elif sfi >= 90:
            overall = "Good"
        elif sfi >= 70:
            overall = "Fair"
        else:
            overall = "Poor"
    except (ValueError, TypeError):
        pass

    return CommandResult.ok(
        summary_str,
        data={
            'summary': summary_str,
            'overall': overall,
            'solar_flux': data.solar_flux,
            'k_index': data.k_index,
            'a_index': data.a_index,
            'geomag_storm': data.geomag_storm.value,
            'source': 'NOAA SWPC',
        }
    )


# ==================== Enhanced Data (Optional Sources) ====================

def get_enhanced_data() -> CommandResult:
    """Get enhanced propagation data from optional sources.

    Tries OpenHamClock first, then HamClock, then returns NOAA-only.
    Optional sources provide:
    - VOACAP propagation predictions
    - DX cluster spots
    - Satellite tracking

    Returns:
        CommandResult with enhanced data (or NOAA-only if no sources configured)
    """
    # Start with NOAA base data
    weather_result = get_space_weather()
    base_data = weather_result.data if weather_result.success else {}

    enhanced = {
        'space_weather': base_data,
        'voacap': None,
        'dx_spots': None,
        'source': base_data.get('source', 'NOAA SWPC'),
        'enhanced_source': None,
    }

    # Try OpenHamClock
    ohc_cfg = _sources.get(DataSource.OPENHAMCLOCK)
    if ohc_cfg and ohc_cfg.enabled:
        result = _fetch_openhamclock_data(ohc_cfg)
        if result:
            enhanced.update(result)
            enhanced['enhanced_source'] = 'OpenHamClock'
            return CommandResult.ok(
                f"Enhanced data from OpenHamClock + NOAA",
                data=enhanced
            )

    # Try HamClock (legacy)
    hc_cfg = _sources.get(DataSource.HAMCLOCK)
    if hc_cfg and hc_cfg.enabled:
        result = _fetch_hamclock_enhanced(hc_cfg)
        if result:
            enhanced.update(result)
            enhanced['enhanced_source'] = 'HamClock'
            return CommandResult.ok(
                f"Enhanced data from HamClock + NOAA",
                data=enhanced
            )

    return CommandResult.ok(
        "Space weather from NOAA (no enhanced sources configured)",
        data=enhanced
    )


def check_source(source: DataSource) -> CommandResult:
    """Test connectivity to an optional data source.

    Args:
        source: Which source to test

    Returns:
        CommandResult indicating connectivity status
    """
    if source == DataSource.NOAA:
        return _test_noaa()
    elif source == DataSource.OPENHAMCLOCK:
        return _test_openhamclock()
    elif source == DataSource.HAMCLOCK:
        return _test_hamclock()
    else:
        return CommandResult.fail(f"Unknown source: {source}")


# ==================== Internal: NOAA ====================

def _test_noaa() -> CommandResult:
    """Test NOAA SWPC connectivity."""
    import urllib.request

    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return CommandResult.ok(
                    "NOAA SWPC is reachable",
                    data={'status': 'connected', 'source': 'NOAA SWPC'}
                )
    except Exception as e:
        return CommandResult.fail(
            f"NOAA SWPC unreachable: {e}",
            error=str(e),
            data={'hint': 'Check internet connectivity'}
        )
    return CommandResult.fail("NOAA SWPC returned unexpected response")


# ==================== Internal: OpenHamClock ====================

def _test_openhamclock() -> CommandResult:
    """Test OpenHamClock connectivity."""
    import urllib.request

    cfg = _sources.get(DataSource.OPENHAMCLOCK)
    if not cfg or not cfg.enabled:
        return CommandResult.fail(
            "OpenHamClock not configured",
            data={'hint': 'Configure with: propagation.configure_source(DataSource.OPENHAMCLOCK, host="...", port=3000)'}
        )

    url = f"{cfg.base_url}/api/dxcluster/spots"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            if response.status == 200:
                return CommandResult.ok(
                    f"OpenHamClock connected at {cfg.host}:{cfg.port}",
                    data={'status': 'connected', 'source': 'OpenHamClock',
                          'host': cfg.host, 'port': cfg.port}
                )
    except Exception as e:
        return CommandResult.fail(
            f"Cannot reach OpenHamClock at {cfg.host}:{cfg.port}: {e}",
            error=str(e),
            data={'hint': 'Ensure OpenHamClock is running (docker compose up)'}
        )
    return CommandResult.fail("OpenHamClock returned unexpected response")


def _fetch_openhamclock_data(cfg: SourceConfig) -> Optional[Dict[str, Any]]:
    """Fetch enhanced data from OpenHamClock.

    OpenHamClock provides JSON REST API on port 3000:
    - /api/dxcluster/spots - DX cluster spots
    - Space weather proxied from NOAA

    Args:
        cfg: OpenHamClock source configuration

    Returns:
        Dict with enhanced data or None on failure
    """
    import urllib.request
    import json

    enhanced = {}

    # Fetch DX spots
    try:
        url = f"{cfg.base_url}/api/dxcluster/spots"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        req.add_header('Accept', 'application/json')

        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        if isinstance(data, list):
            enhanced['dx_spots'] = {
                'spots': data[:20],
                'count': len(data),
                'source': 'OpenHamClock',
            }
    except Exception as e:
        logger.debug(f"OpenHamClock DX spots fetch failed: {e}")

    return enhanced if enhanced else None


# ==================== Internal: HamClock (Legacy) ====================

def _test_hamclock() -> CommandResult:
    """Test HamClock connectivity."""
    import urllib.request

    cfg = _sources.get(DataSource.HAMCLOCK)
    if not cfg or not cfg.enabled:
        return CommandResult.fail(
            "HamClock not configured",
            data={'hint': 'Configure with: propagation.configure_source(DataSource.HAMCLOCK, host="...", port=8080)'}
        )

    url = f"{cfg.base_url}/get_sys.txt"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')
        with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
            data = response.read().decode('utf-8')
            if data:
                return CommandResult.ok(
                    f"HamClock connected at {cfg.host}:{cfg.port}",
                    data={'status': 'connected', 'source': 'HamClock',
                          'host': cfg.host, 'port': cfg.port, 'info': data[:200]}
                )
    except Exception as e:
        return CommandResult.fail(
            f"Cannot reach HamClock at {cfg.host}:{cfg.port}: {e}",
            error=str(e),
            data={'hint': 'Ensure HamClock is running'}
        )
    return CommandResult.fail("HamClock returned unexpected response")


def _fetch_hamclock_enhanced(cfg: SourceConfig) -> Optional[Dict[str, Any]]:
    """Fetch enhanced data from HamClock (legacy).

    HamClock REST API returns key=value text format.

    Args:
        cfg: HamClock source configuration

    Returns:
        Dict with enhanced data or None on failure
    """
    import urllib.request

    enhanced = {}

    def fetch_endpoint(endpoint: str) -> Optional[str]:
        try:
            url = f"{cfg.base_url}/{endpoint}"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'MeshForge/1.0')
            with urllib.request.urlopen(req, timeout=cfg.timeout) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            logger.debug(f"HamClock {endpoint} fetch failed: {e}")
            return None

    def parse_kv(data: str) -> Dict[str, str]:
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result

    # VOACAP propagation predictions
    raw = fetch_endpoint('get_voacap.txt')
    if raw:
        voacap = {'path': '', 'utc': '', 'bands': {}}
        for line in raw.strip().split('\n'):
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip().lower()
            value = value.strip()
            if key == 'path':
                voacap['path'] = value
            elif key == 'utc':
                voacap['utc'] = value
            elif 'm' in key:
                try:
                    if ',' in value:
                        rel, snr = value.split(',', 1)
                        reliability = int(rel.strip())
                    else:
                        reliability = int(value)
                        snr = "0"
                    voacap['bands'][key] = {
                        'reliability': reliability,
                        'snr': int(snr.strip()) if ',' in value else 0,
                    }
                except ValueError:
                    pass

        if voacap['bands']:
            enhanced['voacap'] = voacap

    # DX spots
    raw = fetch_endpoint('get_dxspots.txt')
    if raw:
        lines = [l.strip() for l in raw.strip().split('\n') if l.strip()]
        if lines:
            enhanced['dx_spots'] = {
                'spots': lines,
                'count': len(lines),
                'source': 'HamClock',
            }

    return enhanced if enhanced else None
