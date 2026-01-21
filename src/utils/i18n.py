"""
MeshForge Internationalization (i18n) Framework.

Provides multi-language support for the global HAM community.
Inspired by meshtastic/standalone-ui which supports 18 languages.

Usage:
    from utils.i18n import _, set_language, get_available_languages

    # Set language
    set_language('ja')  # Japanese

    # Use translations
    print(_("Connected to device"))  # "デバイスに接続しました"

    # With parameters
    print(_("Found {count} nodes").format(count=5))

Design:
    - JSON-based translation files in locale/ directory
    - Fallback to English if translation missing
    - Runtime language switching
    - Placeholder support with .format()

Adding Translations:
    1. Create locale/<lang_code>.json
    2. Add translations as key-value pairs
    3. Keys are English strings, values are translations
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Current language code
_current_language: str = "en"

# Loaded translations
_translations: Dict[str, str] = {}

# Cache of available languages
_available_languages: Optional[List[Dict[str, str]]] = None


def get_locale_dir() -> Path:
    """Get the locale directory path."""
    # First check in package
    package_locale = Path(__file__).parent.parent.parent / "locale"
    if package_locale.exists():
        return package_locale

    # Fall back to user config
    user_locale = get_real_user_home() / ".config" / "meshforge" / "locale"
    user_locale.mkdir(parents=True, exist_ok=True)
    return user_locale


def get_available_languages() -> List[Dict[str, str]]:
    """
    Get list of available languages.

    Returns:
        List of dicts with 'code', 'name', 'native_name' keys
    """
    global _available_languages

    if _available_languages is not None:
        return _available_languages

    # Built-in language definitions
    languages = {
        'en': {'code': 'en', 'name': 'English', 'native_name': 'English'},
        'es': {'code': 'es', 'name': 'Spanish', 'native_name': 'Español'},
        'de': {'code': 'de', 'name': 'German', 'native_name': 'Deutsch'},
        'fr': {'code': 'fr', 'name': 'French', 'native_name': 'Français'},
        'it': {'code': 'it', 'name': 'Italian', 'native_name': 'Italiano'},
        'pt': {'code': 'pt', 'name': 'Portuguese', 'native_name': 'Português'},
        'ja': {'code': 'ja', 'name': 'Japanese', 'native_name': '日本語'},
        'ko': {'code': 'ko', 'name': 'Korean', 'native_name': '한국어'},
        'zh': {'code': 'zh', 'name': 'Chinese', 'native_name': '中文'},
        'ru': {'code': 'ru', 'name': 'Russian', 'native_name': 'Русский'},
        'pl': {'code': 'pl', 'name': 'Polish', 'native_name': 'Polski'},
        'nl': {'code': 'nl', 'name': 'Dutch', 'native_name': 'Nederlands'},
        'sv': {'code': 'sv', 'name': 'Swedish', 'native_name': 'Svenska'},
        'fi': {'code': 'fi', 'name': 'Finnish', 'native_name': 'Suomi'},
        'no': {'code': 'no', 'name': 'Norwegian', 'native_name': 'Norsk'},
        'da': {'code': 'da', 'name': 'Danish', 'native_name': 'Dansk'},
        'cs': {'code': 'cs', 'name': 'Czech', 'native_name': 'Čeština'},
        'hu': {'code': 'hu', 'name': 'Hungarian', 'native_name': 'Magyar'},
    }

    # Check which languages have translation files
    locale_dir = get_locale_dir()
    available = [languages['en']]  # English always available

    for lang_code, lang_info in languages.items():
        if lang_code == 'en':
            continue
        lang_file = locale_dir / f"{lang_code}.json"
        if lang_file.exists():
            available.append(lang_info)

    _available_languages = available
    return _available_languages


def load_language(lang_code: str) -> bool:
    """
    Load translations for a language.

    Args:
        lang_code: Language code (e.g., 'en', 'ja', 'es')

    Returns:
        True if loaded successfully
    """
    global _translations, _current_language

    if lang_code == 'en':
        _translations = {}
        _current_language = 'en'
        return True

    locale_dir = get_locale_dir()
    lang_file = locale_dir / f"{lang_code}.json"

    if not lang_file.exists():
        logger.warning(f"Language file not found: {lang_file}")
        return False

    try:
        with open(lang_file, 'r', encoding='utf-8') as f:
            _translations = json.load(f)
        _current_language = lang_code
        logger.info(f"Loaded language: {lang_code}")
        return True
    except Exception as e:
        logger.error(f"Failed to load language {lang_code}: {e}")
        return False


def set_language(lang_code: str) -> bool:
    """
    Set the current language.

    Args:
        lang_code: Language code (e.g., 'en', 'ja', 'es')

    Returns:
        True if language set successfully
    """
    return load_language(lang_code)


def get_language() -> str:
    """Get the current language code."""
    return _current_language


def translate(text: str) -> str:
    """
    Translate a string.

    Args:
        text: English string to translate

    Returns:
        Translated string, or original if no translation found
    """
    if _current_language == 'en' or not _translations:
        return text

    return _translations.get(text, text)


# Shorthand for translate
_ = translate


def create_translation_template(output_path: Optional[Path] = None) -> Path:
    """
    Create a translation template from existing strings.

    Scans source files for translatable strings and creates a template JSON.

    Args:
        output_path: Output path (default: locale/template.json)

    Returns:
        Path to created template
    """
    if output_path is None:
        output_path = get_locale_dir() / "template.json"

    # Common UI strings to translate
    strings = {
        # Status messages
        "Connected": "",
        "Disconnected": "",
        "Connecting...": "",
        "Error": "",
        "Warning": "",
        "Success": "",

        # Menu items
        "Main Menu": "",
        "Settings": "",
        "Help": "",
        "Exit": "",
        "Back": "",

        # Device status
        "Online": "",
        "Offline": "",
        "Unknown": "",
        "No devices found": "",

        # Actions
        "Start": "",
        "Stop": "",
        "Restart": "",
        "Configure": "",
        "Install": "",
        "Update": "",
        "Uninstall": "",

        # Gateway
        "Gateway Bridge": "",
        "Gateway running": "",
        "Gateway stopped": "",
        "Messages sent": "",
        "Messages received": "",

        # RNS
        "RNS Tools": "",
        "RNS connected": "",
        "RNS disconnected": "",

        # Diagnostics
        "System Diagnostics": "",
        "Hardware Detection": "",
        "Network Status": "",
        "Service Status": "",

        # Common
        "Yes": "",
        "No": "",
        "Cancel": "",
        "OK": "",
        "Apply": "",
        "Save": "",
        "Load": "",
        "Reset": "",

        # Errors
        "Connection failed": "",
        "Permission denied": "",
        "File not found": "",
        "Invalid configuration": "",

        # HAM specific
        "Callsign": "",
        "Node": "",
        "Nodes": "",
        "Channel": "",
        "Frequency": "",
        "SNR": "",
        "RSSI": "",
        "Battery": "",
        "Last seen": "",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(strings, f, indent=2, ensure_ascii=False)

    logger.info(f"Created translation template: {output_path}")
    return output_path


def init_i18n(lang_code: Optional[str] = None) -> None:
    """
    Initialize i18n system.

    Args:
        lang_code: Language code to load (default: auto-detect or 'en')
    """
    if lang_code is None:
        # Try to detect from environment
        import os
        lang_code = os.environ.get('LANG', 'en').split('.')[0].split('_')[0]

    if lang_code != 'en':
        if not load_language(lang_code):
            logger.info(f"Falling back to English (no {lang_code} translation)")
            load_language('en')
