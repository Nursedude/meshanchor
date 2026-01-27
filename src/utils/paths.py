"""
MeshForge Path Constants

Centralized path definitions to reduce hardcoding across the codebase.

IMPORTANT: Always use get_real_user_home() instead of Path.home() when
the path should be in the user's home directory. This handles the case
where MeshForge is run with sudo but needs to access the real user's
config files, not root's.
"""

from pathlib import Path
import os
import tempfile


# ============================================================================
# Core utility functions - use these instead of Path.home()
# ============================================================================

def get_real_user_home() -> Path:
    """
    Get the real user's home directory, even when running as root via sudo.

    IMPORTANT: Use this instead of Path.home() for user config files.
    When MeshForge is run with 'sudo python3 src/launcher.py', Path.home()
    returns /root, but we want /home/<actual_user>.

    Returns:
        Path to the real user's home directory
    """
    # Check SUDO_USER first (with path traversal protection)
    sudo_user = os.environ.get('SUDO_USER', '')
    if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
        return Path(f'/home/{sudo_user}')

    # Try LOGNAME as secondary
    logname = os.environ.get('LOGNAME', '')
    if logname and logname != 'root' and '/' not in logname and '..' not in logname:
        return Path(f'/home/{logname}')

    # Fallback to current user (may be /root under sudo)
    return Path.home()


def get_real_username() -> str:
    """
    Get the real username, even when running as root via sudo.

    Returns:
        The real username string
    """
    sudo_user = os.environ.get('SUDO_USER', '')
    if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
        return sudo_user

    logname = os.environ.get('LOGNAME', '')
    if logname and logname != 'root' and '/' not in logname and '..' not in logname:
        return logname

    return os.environ.get('USER', 'unknown')


# ============================================================================
# Path classes
# ============================================================================

class MeshtasticPaths:
    """Paths related to meshtasticd configuration"""

    ETC_BASE = Path('/etc/meshtasticd')
    CONFIG_FILE = ETC_BASE / 'config.yaml'
    CONFIG_D = ETC_BASE / 'config.d'
    AVAILABLE_D = ETC_BASE / 'available.d'

    @classmethod
    def ensure_config_dirs(cls) -> bool:
        """Create configuration directories if they don't exist. Returns True on success."""
        try:
            cls.CONFIG_D.mkdir(parents=True, exist_ok=True)
            cls.AVAILABLE_D.mkdir(parents=True, exist_ok=True)
            return True
        except PermissionError:
            return False


class ReticulumPaths:
    """Paths related to Reticulum/RNS configuration.

    Uses get_real_user_home() so that .reticulum resolves to the real
    user's home (e.g. /home/user/.reticulum) even when running under sudo.

    Resolution order (mirrors RNS.Reticulum.__init__):
      1. /etc/reticulum/config (system-wide)
      2. ~/.config/reticulum/config (XDG-style)
      3. ~/.reticulum/config (traditional fallback)
    """

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get Reticulum config directory.

        Checks locations in the same order as RNS.Reticulum.__init__:
          1. /etc/reticulum/ (system-wide)
          2. ~/.config/reticulum/ (XDG-style)
          3. ~/.reticulum/ (traditional, default)
        """
        # System-wide config
        if Path('/etc/reticulum').is_dir() and Path('/etc/reticulum/config').is_file():
            return Path('/etc/reticulum')

        # XDG-style user config
        user_home = get_real_user_home()
        xdg_dir = user_home / '.config' / 'reticulum'
        if xdg_dir.is_dir() and (xdg_dir / 'config').is_file():
            return xdg_dir

        # Traditional fallback
        return user_home / '.reticulum'

    @classmethod
    def get_config_file(cls) -> Path:
        """Get main RNS config file"""
        return cls.get_config_dir() / 'config'

    @classmethod
    def get_interfaces_dir(cls) -> Path:
        """Get RNS custom interfaces directory (for plugins like Meshtastic_Interface)"""
        return cls.get_config_dir() / 'interfaces'


class MeshForgePaths:
    """Paths related to MeshForge application"""

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get MeshForge config directory"""
        return get_real_user_home() / '.config' / 'meshforge'

    @classmethod
    def get_data_dir(cls) -> Path:
        """Get MeshForge data directory"""
        return get_real_user_home() / '.local' / 'share' / 'meshforge'

    @classmethod
    def get_cache_dir(cls) -> Path:
        """Get MeshForge cache directory"""
        return get_real_user_home() / '.cache' / 'meshforge'

    @classmethod
    def get_plugins_dir(cls) -> Path:
        """Get user plugins directory"""
        return cls.get_config_dir() / 'plugins'

    @classmethod
    def ensure_user_dirs(cls) -> None:
        """Create user directories if they don't exist.

        When running under sudo, chown created dirs to the real user
        so they remain accessible without sudo later.
        """
        dirs = [
            cls.get_config_dir(),
            cls.get_data_dir(),
            cls.get_cache_dir(),
            cls.get_plugins_dir(),
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Fix ownership if running under sudo
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            try:
                import pwd
                pw = pwd.getpwnam(sudo_user)
                uid, gid = pw.pw_uid, pw.pw_gid
                for d in dirs:
                    # Only chown if currently root-owned
                    if d.stat().st_uid == 0:
                        os.chown(str(d), uid, gid)
            except (KeyError, OSError):
                pass  # Non-critical: dirs still usable by root


class SystemPaths:
    """System-level paths"""

    # Boot configuration
    BOOT_CONFIG = Path('/boot/firmware/config.txt')
    BOOT_CONFIG_LEGACY = Path('/boot/config.txt')

    # Device paths
    SERIAL_DEVICES = Path('/dev')
    THERMAL_ZONE = Path('/sys/class/thermal/thermal_zone0/temp')

    # System files
    PROC_STAT = Path('/proc/stat')
    PROC_UPTIME = Path('/proc/uptime')
    PROC_MEMINFO = Path('/proc/meminfo')

    @classmethod
    def get_boot_config(cls) -> Path:
        """Get the appropriate boot config path"""
        if cls.BOOT_CONFIG.exists():
            return cls.BOOT_CONFIG
        return cls.BOOT_CONFIG_LEGACY

    @classmethod
    def get_serial_ports(cls) -> list:
        """Get list of serial port paths"""
        ports = []
        for pattern in ['ttyUSB*', 'ttyACM*', 'ttyAMA*']:
            ports.extend(cls.SERIAL_DEVICES.glob(pattern))
        return sorted(ports)


# ============================================================================
# Atomic file operations
# ============================================================================

def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically using temp-file-then-rename.

    On POSIX systems, os.replace() is atomic, so either the old file
    remains intact or the new content is fully written. No partial writes.

    Uses a unique temp file (via tempfile) to avoid collisions between
    concurrent writers targeting the same path.

    Args:
        path: Target file path.
        content: Text content to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f'.{path.name}.',
            suffix='.tmp'
        )
        tmp_path = Path(tmp_name)
        os.write(fd, content.encode('utf-8'))
        os.fsync(fd)
        os.close(fd)
        fd = None
        tmp_path.replace(path)  # Atomic on POSIX
    except Exception:
        if fd is not None:
            os.close(fd)
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
