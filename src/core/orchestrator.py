"""
MeshForge Service Orchestrator

Manages the complete NOC stack: meshtasticd, rnsd, and MeshForge services.
MeshForge IS the node - this orchestrator ensures all services start, run, and recover.

Supports:
    - Native meshtasticd (for SPI radios like Meshtoad)
    - Python meshtastic CLI (for USB serial radios)
    - Automatic config detection from /etc/meshforge/noc.yaml

Usage:
    # As module
    from core.orchestrator import ServiceOrchestrator
    orch = ServiceOrchestrator()
    orch.startup()

    # As standalone
    python -m core.orchestrator [--stop|--status|--install|--config]
"""

import os
import sys
import time
import socket
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Setup logging
logger = logging.getLogger(__name__)

# Configuration paths
NOC_CONFIG_PATH = Path("/etc/meshforge/noc.yaml")
MESHTASTICD_CONFIG_DIR = Path("/etc/meshtasticd")


class ServiceState(Enum):
    """Service states."""
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    NOT_INSTALLED = "not_installed"


@dataclass
class ServiceConfig:
    """Configuration for a managed service."""
    name: str
    systemd_name: str
    check_port: Optional[int] = None
    check_command: Optional[List[str]] = None
    startup_delay: int = 3  # seconds to wait after starting
    required: bool = True
    install_command: Optional[List[str]] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ServiceStatus:
    """Current status of a service."""
    name: str
    state: ServiceState
    pid: Optional[int] = None
    uptime: Optional[str] = None
    message: str = ""


class ServiceOrchestrator:
    """
    Orchestrates MeshForge NOC services.

    Manages meshtasticd, rnsd, and ensures they start in the correct order
    with proper health verification (double-tap).
    """

    # Service configurations
    SERVICES: Dict[str, ServiceConfig] = {
        'meshtasticd': ServiceConfig(
            name='meshtasticd',
            systemd_name='meshtasticd',
            check_port=4403,
            startup_delay=5,  # Device init takes time
            required=True,
            install_command=['pip3', 'install', 'meshtastic'],
        ),
        'rnsd': ServiceConfig(
            name='rnsd',
            systemd_name='rnsd',
            check_command=['rnstatus', '-s'],
            startup_delay=3,
            required=True,
            install_command=['pip3', 'install', 'rns'],
            dependencies=['meshtasticd'],  # Start after meshtasticd
        ),
        'mosquitto': ServiceConfig(
            name='mosquitto',
            systemd_name='mosquitto',
            check_port=1883,
            startup_delay=2,
            required=False,
            install_command=['apt-get', 'install', '-y', 'mosquitto'],
        ),
    }

    # Startup order (services started in this sequence)
    STARTUP_ORDER = ['meshtasticd', 'rnsd']

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize orchestrator."""
        self._config_path = config_path or NOC_CONFIG_PATH
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable]] = {
            'service_started': [],
            'service_stopped': [],
            'service_failed': [],
            'all_ready': [],
        }

        # Load configuration
        self._load_config()

        # Adjust meshtasticd service based on daemon type
        self._configure_meshtasticd()

    def _load_config(self):
        """Load NOC configuration from /etc/meshforge/noc.yaml."""
        # Default config
        self.config = {
            'mode': 'local',  # local | client | remote-only
            'auto_start': True,
            'health_check_interval': 30,
            'restart_on_failure': True,
            'max_restart_attempts': 3,
            'radio': {
                'type': 'unknown',
                'daemon': 'python',
                'device': '',
            },
            'services': {
                'meshtasticd': {'managed': True, 'auto_start': True},
                'rnsd': {'managed': True, 'auto_start': True},
            },
        }

        # Load from config file
        if self._config_path and self._config_path.exists():
            if HAS_YAML:
                try:
                    with open(self._config_path) as f:
                        file_config = yaml.safe_load(f)
                        if file_config and 'noc' in file_config:
                            noc_config = file_config['noc']
                            # Merge configs
                            self._merge_config(noc_config)
                            logger.info(f"Loaded config from {self._config_path}")
                except Exception as e:
                    logger.warning(f"Failed to load config: {e}")
            else:
                logger.warning("PyYAML not installed, using defaults")
        else:
            logger.info("No config file found, using defaults")

    def _merge_config(self, noc_config: Dict[str, Any]):
        """Merge loaded config with defaults."""
        if 'mode' in noc_config:
            self.config['mode'] = noc_config['mode']

        if 'radio' in noc_config:
            self.config['radio'].update(noc_config['radio'])

        if 'services' in noc_config:
            for service, svc_config in noc_config['services'].items():
                if service not in self.config['services']:
                    self.config['services'][service] = {}
                self.config['services'][service].update(svc_config)

        if 'startup' in noc_config:
            startup = noc_config['startup']
            if 'health_check_interval' in startup:
                self.config['health_check_interval'] = startup['health_check_interval']
            if 'restart_on_failure' in startup:
                self.config['restart_on_failure'] = startup['restart_on_failure']
            if 'max_restart_attempts' in startup:
                self.config['max_restart_attempts'] = startup['max_restart_attempts']
            if 'auto_start_services' in startup:
                self.config['auto_start'] = startup['auto_start_services']

    def _configure_meshtasticd(self):
        """Configure meshtasticd service based on daemon type."""
        daemon_type = self.config['radio'].get('daemon', 'python')

        if daemon_type == 'native':
            # Native meshtasticd binary (for SPI radios)
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_port=4403,
                startup_delay=5,
                required=True,
                # No install command - requires .deb
            )
            logger.info("Configured for native meshtasticd (SPI radio)")
        else:
            # Python CLI (for USB serial radios)
            self.SERVICES['meshtasticd'] = ServiceConfig(
                name='meshtasticd',
                systemd_name='meshtasticd',
                check_port=4403,
                startup_delay=5,
                required=True,
                install_command=['pip3', 'install', 'meshtastic'],
            )
            logger.info("Configured for Python meshtastic CLI (USB radio)")

    def get_config_info(self) -> Dict[str, Any]:
        """Get current configuration information."""
        return {
            'config_file': str(self._config_path),
            'config_exists': self._config_path.exists() if self._config_path else False,
            'mode': self.config['mode'],
            'radio_type': self.config['radio'].get('type', 'unknown'),
            'daemon_type': self.config['radio'].get('daemon', 'python'),
            'device': self.config['radio'].get('device', ''),
            'meshtasticd_config_dir': str(MESHTASTICD_CONFIG_DIR),
            'meshtasticd_config_exists': MESHTASTICD_CONFIG_DIR.exists(),
            'health_check_interval': self.config['health_check_interval'],
            'restart_on_failure': self.config['restart_on_failure'],
            'max_restart_attempts': self.config['max_restart_attempts'],
        }

    # ─────────────────────────────────────────────────────────────
    # Service State Checks
    # ─────────────────────────────────────────────────────────────

    def is_installed(self, service_name: str) -> bool:
        """Check if service is installed."""
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        # Check systemd unit exists
        result = subprocess.run(
            ['systemctl', 'list-unit-files', f'{config.systemd_name}.service'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return config.systemd_name in result.stdout

    def is_running(self, service_name: str) -> bool:
        """Check if service is running (systemctl is-active)."""
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        result = subprocess.run(
            ['systemctl', 'is-active', config.systemd_name],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0

    def is_healthy(self, service_name: str) -> bool:
        """
        Double-tap health check.

        First check: systemctl is-active
        Second check: functional verification (port or command)
        """
        config = self.SERVICES.get(service_name)
        if not config:
            return False

        # First tap: systemctl
        if not self.is_running(service_name):
            return False

        # Second tap: functional check
        if config.check_port:
            return self._check_port(config.check_port)
        elif config.check_command:
            return self._check_command(config.check_command)

        # No functional check defined, trust systemctl
        return True

    def _check_port(self, port: int, host: str = 'localhost', timeout: float = 2.0) -> bool:
        """Check if port is accepting connections."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except (socket.error, OSError):
            return False

    def _check_command(self, command: List[str], timeout: int = 10) -> bool:
        """Run command and check for success."""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def get_status(self, service_name: str) -> ServiceStatus:
        """Get detailed status of a service."""
        config = self.SERVICES.get(service_name)
        if not config:
            return ServiceStatus(
                name=service_name,
                state=ServiceState.UNKNOWN,
                message=f"Unknown service: {service_name}"
            )

        if not self.is_installed(service_name):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.NOT_INSTALLED,
                message=f"{service_name} is not installed"
            )

        if not self.is_running(service_name):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.STOPPED,
                message=f"{service_name} is stopped"
            )

        if not self.is_healthy(service_name):
            return ServiceStatus(
                name=service_name,
                state=ServiceState.FAILED,
                message=f"{service_name} running but not responding"
            )

        # Get PID
        pid = self._get_pid(service_name)

        return ServiceStatus(
            name=service_name,
            state=ServiceState.RUNNING,
            pid=pid,
            message=f"{service_name} is running"
        )

    def _get_pid(self, service_name: str) -> Optional[int]:
        """Get PID of service main process."""
        config = self.SERVICES.get(service_name)
        if not config:
            return None

        try:
            result = subprocess.run(
                ['systemctl', 'show', '-p', 'MainPID', config.systemd_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                pid_str = result.stdout.strip().replace('MainPID=', '')
                return int(pid_str) if pid_str and pid_str != '0' else None
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def get_all_status(self) -> Dict[str, ServiceStatus]:
        """Get status of all managed services."""
        return {name: self.get_status(name) for name in self.SERVICES}

    # ─────────────────────────────────────────────────────────────
    # Service Control
    # ─────────────────────────────────────────────────────────────

    def start_service(self, service_name: str, wait: bool = True) -> bool:
        """
        Start a service with health verification.

        Args:
            service_name: Name of service to start
            wait: If True, wait for service to be healthy

        Returns:
            True if service is running and healthy
        """
        config = self.SERVICES.get(service_name)
        if not config:
            logger.error(f"Unknown service: {service_name}")
            return False

        if not self.is_installed(service_name):
            logger.error(f"{service_name} is not installed")
            return False

        if self.is_healthy(service_name):
            logger.info(f"{service_name} is already running and healthy")
            return True

        logger.info(f"Starting {service_name}...")
        result = subprocess.run(
            ['systemctl', 'start', config.systemd_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Failed to start {service_name}: {result.stderr}")
            return False

        if wait:
            # Wait for startup delay
            time.sleep(config.startup_delay)

            # Verify health (double-tap)
            if not self.is_healthy(service_name):
                logger.warning(f"{service_name} started but not healthy, retrying check...")
                time.sleep(2)  # One more try
                if not self.is_healthy(service_name):
                    logger.error(f"{service_name} failed health check")
                    self._emit('service_failed', service_name)
                    return False

        logger.info(f"{service_name} started successfully")
        self._emit('service_started', service_name)
        return True

    def stop_service(self, service_name: str) -> bool:
        """Stop a service."""
        config = self.SERVICES.get(service_name)
        if not config:
            logger.error(f"Unknown service: {service_name}")
            return False

        if not self.is_running(service_name):
            logger.info(f"{service_name} is already stopped")
            return True

        logger.info(f"Stopping {service_name}...")
        result = subprocess.run(
            ['systemctl', 'stop', config.systemd_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Failed to stop {service_name}: {result.stderr}")
            return False

        logger.info(f"{service_name} stopped")
        self._emit('service_stopped', service_name)
        return True

    def restart_service(self, service_name: str) -> bool:
        """Restart a service with health verification."""
        self.stop_service(service_name)
        time.sleep(1)
        return self.start_service(service_name)

    # ─────────────────────────────────────────────────────────────
    # Orchestration
    # ─────────────────────────────────────────────────────────────

    def startup(self, graceful: bool = False) -> bool:
        """
        Start all managed services in correct order.

        Args:
            graceful: If True, continue even when services fail (for no-radio scenarios)

        Returns:
            True if all required services are running and healthy (or graceful mode)
        """
        logger.info("═══ MeshForge NOC Startup ═══")
        if graceful:
            logger.info("Graceful mode: will continue even if services fail")

        success = True
        failed_services = []

        for service_name in self.STARTUP_ORDER:
            config = self.SERVICES.get(service_name)
            if not config:
                continue

            # Check dependencies (skip in graceful mode if dep failed)
            dep_ok = True
            for dep in config.dependencies:
                if not self.is_healthy(dep):
                    logger.error(f"Dependency {dep} not healthy for {service_name}")
                    if graceful:
                        logger.warning(f"Graceful mode: skipping {service_name} due to dependency")
                        dep_ok = False
                        break
                    elif config.required:
                        success = False
                        continue

            if not dep_ok:
                failed_services.append(service_name)
                continue

            # Start service
            if not self.start_service(service_name):
                failed_services.append(service_name)
                if config.required and not graceful:
                    logger.error(f"Required service {service_name} failed to start")
                    success = False
                elif graceful:
                    logger.warning(f"Graceful mode: {service_name} failed but continuing")
                else:
                    logger.warning(f"Optional service {service_name} failed to start")

        if success:
            logger.info("═══ All services started ═══")
            self._emit('all_ready')
        elif graceful:
            logger.warning("═══ Startup completed with failures (graceful mode) ═══")
            if failed_services:
                logger.warning(f"Failed services: {', '.join(failed_services)}")
            logger.info("MeshForge running in degraded mode - some features unavailable")
        else:
            logger.error("═══ Startup failed ═══")

        return success or graceful

    def shutdown(self) -> bool:
        """Stop all managed services in reverse order."""
        logger.info("═══ MeshForge NOC Shutdown ═══")

        self._running = False
        success = True

        # Stop in reverse order
        for service_name in reversed(self.STARTUP_ORDER):
            if not self.stop_service(service_name):
                success = False

        logger.info("═══ Shutdown complete ═══")
        return success

    # ─────────────────────────────────────────────────────────────
    # Health Monitoring
    # ─────────────────────────────────────────────────────────────

    def start_monitoring(self, interval: int = 30):
        """Start background health monitoring."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.warning("Monitoring already running")
            return

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
        logger.info(f"Health monitoring started (interval: {interval}s)")

    def stop_monitoring(self):
        """Stop health monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Health monitoring stopped")

    def _monitor_loop(self, interval: int):
        """Background health check loop."""
        restart_counts: Dict[str, int] = {name: 0 for name in self.SERVICES}

        while self._running:
            for service_name in self.STARTUP_ORDER:
                config = self.SERVICES.get(service_name)
                if not config or not config.required:
                    continue

                if not self.is_healthy(service_name):
                    logger.warning(f"{service_name} health check failed")

                    if self.config.get('restart_on_failure', True):
                        max_attempts = self.config.get('max_restart_attempts', 3)
                        if restart_counts[service_name] < max_attempts:
                            logger.info(f"Attempting restart of {service_name}")
                            if self.restart_service(service_name):
                                restart_counts[service_name] = 0
                            else:
                                restart_counts[service_name] += 1
                                self._emit('service_failed', service_name)
                        else:
                            logger.error(
                                f"{service_name} exceeded max restart attempts"
                            )

            time.sleep(interval)

    # ─────────────────────────────────────────────────────────────
    # Event System
    # ─────────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable):
        """Register callback for event."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, *args):
        """Emit event to registered callbacks."""
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    # ─────────────────────────────────────────────────────────────
    # Installation
    # ─────────────────────────────────────────────────────────────

    def check_installation(self) -> Dict[str, bool]:
        """Check what's installed."""
        return {name: self.is_installed(name) for name in self.SERVICES}

    def install_missing(self) -> bool:
        """Install missing required services."""
        success = True
        for name, config in self.SERVICES.items():
            if not config.required:
                continue

            if not self.is_installed(name):
                logger.info(f"Installing {name}...")
                if config.install_command:
                    try:
                        result = subprocess.run(
                            config.install_command,
                            capture_output=True,
                            text=True,
                            timeout=300  # 5 minute timeout for installs
                        )
                        if result.returncode != 0:
                            logger.error(f"Failed to install {name}: {result.stderr}")
                            success = False
                    except subprocess.TimeoutExpired:
                        logger.error(f"Timeout installing {name}")
                        success = False
                else:
                    logger.error(f"No install command for {name}")
                    success = False

        return success


# ─────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for orchestrator."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
    )

    parser = argparse.ArgumentParser(description='MeshForge Service Orchestrator')
    parser.add_argument('--start', action='store_true', help='Start all services')
    parser.add_argument('--stop', action='store_true', help='Stop all services')
    parser.add_argument('--restart', action='store_true', help='Restart all services')
    parser.add_argument('--status', action='store_true', help='Show service status')
    parser.add_argument('--config', action='store_true', help='Show configuration')
    parser.add_argument('--install', action='store_true', help='Install missing services')
    parser.add_argument('--monitor', action='store_true', help='Start with monitoring')
    parser.add_argument('--graceful', action='store_true',
                        help='Continue even if services fail (for no-radio scenarios)')

    args = parser.parse_args()
    orch = ServiceOrchestrator()

    if args.config:
        print("\n═══ MeshForge NOC Configuration ═══\n")
        config_info = orch.get_config_info()
        print(f"  Config File:        {config_info['config_file']}")
        print(f"  Config Exists:      {config_info['config_exists']}")
        print(f"  NOC Mode:           {config_info['mode']}")
        print(f"  Radio Type:         {config_info['radio_type']}")
        print(f"  Daemon Type:        {config_info['daemon_type']}")
        if config_info['device']:
            print(f"  USB Device:         {config_info['device']}")
        print(f"  Meshtasticd Config: {config_info['meshtasticd_config_dir']}")
        print(f"  Config Dir Exists:  {config_info['meshtasticd_config_exists']}")
        print(f"  Health Check:       {config_info['health_check_interval']}s")
        print(f"  Restart on Fail:    {config_info['restart_on_failure']}")
        print(f"  Max Restarts:       {config_info['max_restart_attempts']}")

        # Show meshtasticd configs if they exist
        if MESHTASTICD_CONFIG_DIR.exists():
            available = list((MESHTASTICD_CONFIG_DIR / "available.d").glob("*.yaml"))
            enabled = list((MESHTASTICD_CONFIG_DIR / "config.d").glob("*.yaml"))
            print(f"\n  Available Configs:  {len(available)}")
            for cfg in available:
                print(f"    - {cfg.stem}")
            print(f"  Enabled Configs:    {len(enabled)}")
            for cfg in enabled:
                if cfg.is_symlink():
                    target = cfg.resolve().stem
                    print(f"    - {cfg.stem} -> {target}")
                else:
                    print(f"    - {cfg.stem}")
        print()
        sys.exit(0)

    if args.stop:
        sys.exit(0 if orch.shutdown() else 1)

    if args.restart:
        orch.shutdown()
        time.sleep(2)
        sys.exit(0 if orch.startup() else 1)

    if args.status:
        print("\n═══ MeshForge NOC Status ═══\n")
        config_info = orch.get_config_info()
        print(f"  Mode: {config_info['mode']} | Radio: {config_info['radio_type']} | Daemon: {config_info['daemon_type']}\n")

        statuses = orch.get_all_status()
        for name, status in statuses.items():
            state_icon = {
                ServiceState.RUNNING: '✓',
                ServiceState.STOPPED: '○',
                ServiceState.FAILED: '✗',
                ServiceState.NOT_INSTALLED: '?',
            }.get(status.state, '?')
            pid_str = f" (PID: {status.pid})" if status.pid else ""
            print(f"  {state_icon} {name}: {status.state.value}{pid_str}")
            if status.message and status.state != ServiceState.RUNNING:
                print(f"      {status.message}")
        print()
        sys.exit(0)

    if args.install:
        sys.exit(0 if orch.install_missing() else 1)

    # Default: start
    if args.start or not any([args.stop, args.restart, args.status, args.install, args.config]):
        success = orch.startup(graceful=args.graceful)
        if (success or args.graceful) and args.monitor:
            orch.start_monitoring()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                orch.shutdown()
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
