"""Configuration diagnostic rules for MeshAnchor Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
)


def load_configuration_rules(engine: "DiagnosticEngine") -> None:
    """Load configuration diagnostic rules."""

    engine.add_rule(DiagnosticRule(
        name="alsa_udev_broken_goto",
        pattern=r"(?i)(alsa|udev).*(goto|label).*(missing|broken|no matching|ignoring)",
        category=Category.CONFIGURATION,
        cause_template=(
            "ALSA udev rules have GOTO references to non-existent labels. "
            "This is a known alsa-utils packaging bug on Raspberry Pi OS. "
            "Harmless but produces udev errors on every boot."
        ),
        suggestions=[
            "This is cosmetic — does not affect audio or MeshAnchor functionality",
            "Fix: sudo sed -i '/^GOTO=/d' /lib/udev/rules.d/90-alsa-restore.rules",
            "Or suppress: add the missing LABEL lines to the udev rule file",
            "Will be fixed in a future alsa-utils package update",
        ],
        confidence_base=0.95,
        expertise_level="advanced",
    ))

    engine.add_rule(DiagnosticRule(
        name="config_file_missing",
        pattern=r"(?i)(config|configuration|settings).*(missing|not found|does not exist)",
        category=Category.CONFIGURATION,
        cause_template="Configuration file is missing or not found at expected path",
        suggestions=[
            "Run MeshAnchor setup wizard to generate config",
            "Check config search paths in documentation",
            "Copy example config as starting point",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="invalid_config",
        pattern=r"(?i)(config|yaml|ini|json).*(invalid|parse error|syntax error|malformed)",
        category=Category.CONFIGURATION,
        cause_template="Configuration file has syntax errors or invalid values",
        suggestions=[
            "Validate YAML: python3 -c \"import yaml; yaml.safe_load(open('config.yaml'))\"",
            "Check for tab/space mixing in YAML files",
            "Compare against example config for correct format",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="permission_denied",
        pattern=r"(?i)(permission|access).*(denied|forbidden|EACCES|not allowed)",
        category=Category.CONFIGURATION,
        cause_template="Permission denied — insufficient access rights",
        suggestions=[
            "Check file ownership: ls -la <file>",
            "Fix permissions: sudo chown $USER:$USER <file>",
            "Add user to required group (dialout, gpio, etc.)",
            "Check if running with correct user/sudo",
        ],
        confidence_base=0.85,
    ))

    engine.add_rule(DiagnosticRule(
        name="port_conflict",
        pattern=r"(?i)(port|address).*(in use|conflict|EADDRINUSE|already bound|occupied)",
        category=Category.CONFIGURATION,
        cause_template="Port is already in use by another process",
        suggestions=[
            "Find process using port: sudo ss -tlnp | grep <port>",
            "Kill conflicting process or use different port",
            "Check for stale PID files",
            "Wait for TIME_WAIT to expire after recent restart",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="invalid_frequency_setting",
        pattern=r"(?i)(frequency|region|band).*(invalid|out of range|illegal|not permitted)",
        category=Category.CONFIGURATION,
        cause_template="RF frequency setting is invalid or not permitted for region",
        suggestions=[
            "Check region setting: meshtastic --get lora.region",
            "Set correct region: meshtastic --set lora.region US (or EU_868, etc.)",
            "Verify frequency plan matches your legal jurisdiction",
            "See https://meshtastic.org/docs/overview/radio-settings",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="duplicate_node_id",
        pattern=r"(?i)(node|device).*(id|identifier).*(duplicate|conflict|already exists|collision)",
        category=Category.CONFIGURATION,
        cause_template="Two devices have the same node ID — causing routing confusion",
        suggestions=[
            "Factory reset one device to generate new ID",
            "Check for cloned firmware images sharing identity",
            "meshtastic --factory-reset on the conflicting node",
            "Verify each device has unique long_name and short_name",
        ],
        confidence_base=0.9,
    ))

    engine.add_rule(DiagnosticRule(
        name="wrong_modem_preset",
        pattern=r"(?i)(modem|preset|lora).*(wrong|mismatch|incompatible|different)",
        category=Category.CONFIGURATION,
        cause_template="LoRa modem preset doesn't match other nodes — cannot communicate",
        suggestions=[
            "All nodes in mesh must use same modem preset",
            "Check current preset: meshtastic --get lora.modem_preset",
            "Set matching preset: meshtastic --set lora.modem_preset LONG_FAST",
            "Available: LONG_FAST, LONG_SLOW, MEDIUM_FAST, SHORT_FAST, etc.",
        ],
        confidence_base=0.9,
    ))
