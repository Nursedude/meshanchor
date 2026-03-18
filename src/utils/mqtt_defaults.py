"""
Centralized Meshtastic public broker defaults.

Single source of truth for mqtt.meshtastic.org connection parameters.
Per security rule MF-SEC: these constants must NOT be duplicated as string
literals elsewhere in src/. Import from this module instead.

These are *public* credentials — the Meshtastic project intentionally
publishes them so anyone can observe the mesh without radio hardware.
"""

# ── Public broker connection ──────────────────────────────────────────
MESHTASTIC_PUBLIC_BROKER = "mqtt.meshtastic.org"
MESHTASTIC_PUBLIC_PORT = 8883          # TLS port (preferred)
MESHTASTIC_PUBLIC_PORT_PLAIN = 1883    # Non-TLS port
MESHTASTIC_PUBLIC_USERNAME = "meshdev"
MESHTASTIC_PUBLIC_PASSWORD = "large4cats"
MESHTASTIC_PUBLIC_ROOT_TOPIC = "msh/US/2/e"
MESHTASTIC_PUBLIC_CHANNEL = "LongFast"
MESHTASTIC_PUBLIC_KEY = "AQ=="         # Default Meshtastic encryption key
