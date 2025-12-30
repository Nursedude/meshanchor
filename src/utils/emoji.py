"""Emoji utility with fallback support for Raspberry Pi OS terminals"""

import os
import sys


class EmojiHelper:
    """Helper class for emoji display with ASCII fallbacks"""

    def __init__(self):
        self.emoji_enabled = self._detect_emoji_support()

    def _detect_emoji_support(self):
        """Detect if terminal supports emoji"""
        # Check environment variables
        term = os.environ.get('TERM', '').lower()
        lang = os.environ.get('LANG', '').lower()

        # Disable emojis if:
        # 1. Running in basic terminals (linux, screen, tmux without proper UTF-8)
        # 2. LANG doesn't include UTF-8
        # 3. SSH without proper locale
        # 4. Explicitly disabled via env var

        if os.environ.get('DISABLE_EMOJI', '').lower() in ('1', 'true', 'yes'):
            return False

        # Basic terminals that may not render emojis well
        basic_terms = ['linux', 'dumb', 'unknown', 'cons25']
        if any(t in term for t in basic_terms):
            return False

        # Check for UTF-8 support
        if 'utf' not in lang and 'utf' not in term:
            return False

        # Default to enabled for modern terminals
        return True

    # Emoji mappings with ASCII fallbacks
    EMOJI_MAP = {
        # Status indicators
        '🔴': '[ ]',    # Stopped/Error
        '🟢': '[*]',    # Running/Success
        '🟡': '[~]',    # Warning
        '🔵': '[i]',    # Info

        # UI Elements
        '📊': '[DASH]',     # Dashboard
        '📦': '[PKG]',      # Package/Install
        '⬆️': '[UP]',       # Update/Upgrade
        '⚙️': '[CFG]',      # Configuration
        '📻': '[RADIO]',    # Radio/Channel
        '📋': '[TMPL]',     # Template/List
        '🔍': '[FIND]',     # Search/Check
        '🔌': '[HW]',       # Hardware
        '🐛': '[DEBUG]',    # Debug
        '🚪': '[EXIT]',     # Exit
        '❓': '[?]',        # Help
        '🌐': '[MESH]',     # Network/Mesh
        '📡': '[ANT]',      # Antenna/Signal
        '✓': '[OK]',        # Success
        '✗': '[X]',         # Fail
        '⚠': '[!]',         # Warning

        # Hardware
        '🔧': '[TOOL]',     # Tools/Config
        '🎛️': '[CTRL]',     # Controls
        '🌡️': '[TEMP]',     # Temperature
        '💾': '[MEM]',      # Memory/Storage

        # Network
        '🏔️': '[MTN]',      # Mountain (MtnMesh)
        '🚨': '[SOS]',      # Emergency
        '🏙️': '[CITY]',     # Urban
        '📢': '[BCST]',     # Broadcast

        # Actions
        '⬅️': '[<-]',       # Back
        '➡️': '[->]',       # Forward
        '🔄': '[SYNC]',     # Sync/Refresh
        '🔐': '[LOCK]',     # Security
        '📜': '[LOG]',      # Logs
        '📝': '[EDIT]',     # Edit
        '⚡': '[FAST]',     # Fast/Quick
        '👋': '[BYE]',      # Goodbye
        'ℹ️': '[i]',        # Information
    }

    def get(self, emoji, fallback=None):
        """Get emoji or ASCII fallback

        Args:
            emoji: The emoji character
            fallback: Optional custom fallback (uses default if None)

        Returns:
            Emoji if supported, otherwise ASCII fallback
        """
        if self.emoji_enabled:
            return emoji

        if fallback:
            return fallback

        return self.EMOJI_MAP.get(emoji, emoji)

    def enable(self):
        """Force enable emoji"""
        self.emoji_enabled = True

    def disable(self):
        """Force disable emoji"""
        self.emoji_enabled = False

    def is_enabled(self):
        """Check if emoji is enabled"""
        return self.emoji_enabled


# Global instance
_emoji = EmojiHelper()


def get(emoji, fallback=None):
    """Get emoji or fallback (convenience function)"""
    return _emoji.get(emoji, fallback)


def enable():
    """Enable emoji globally"""
    _emoji.enable()


def disable():
    """Disable emoji globally"""
    _emoji.disable()


def is_enabled():
    """Check if emoji is enabled"""
    return _emoji.is_enabled()


# Common emoji shortcuts
def status_running():
    """Running status indicator"""
    return get('🟢', '[*]')


def status_stopped():
    """Stopped status indicator"""
    return get('🔴', '[ ]')


def status_warning():
    """Warning status indicator"""
    return get('🟡', '[~]')


def status_info():
    """Info status indicator"""
    return get('🔵', '[i]')
