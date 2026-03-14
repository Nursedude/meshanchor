"""
MQTT Decryptor Bridge — Optional AES-256-CTR packet decryption.

Wraps meshing_around's MeshPacketProcessor to decrypt Meshtastic MQTT
packets. Degrades gracefully to no-op when meshing_around or the
cryptography library is not available.
"""

import logging
import sys
import threading
from typing import Dict, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Add meshing_around to path if available
_MA_PATH = "/opt/meshing_around_meshforge"
if _MA_PATH not in sys.path:
    sys.path.insert(0, _MA_PATH)

# Import MeshPacketProcessor and DecryptedPacket
_MeshPacketProcessor, _DecryptedPacket, _HAS_CRYPTO = safe_import(
    'meshing_around_clients.core.mesh_crypto', 'MeshPacketProcessor', 'DecryptedPacket'
)


class MQTTDecryptorBridge:
    """Decrypts Meshtastic MQTT encrypted packets.

    Uses meshing_around's MeshPacketProcessor for AES-256-CTR decryption
    and protobuf decoding. Returns None if crypto is unavailable or
    decryption fails.
    """

    def __init__(self, default_key: str = "AQ=="):
        self._processor = None
        self._available = False
        self._decrypted_count = 0
        self._failed_count = 0
        self._lock = threading.Lock()

        if _HAS_CRYPTO and _MeshPacketProcessor:
            try:
                self._processor = _MeshPacketProcessor(encryption_key=default_key)
                self._available = True
                logger.info("MQTT decryptor initialized (key=%s)", default_key)
            except Exception as e:
                logger.debug("Failed to initialize MeshPacketProcessor: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def decrypted_count(self) -> int:
        return self._decrypted_count

    @property
    def failed_count(self) -> int:
        return self._failed_count

    def set_key(self, key: str) -> bool:
        """Update the encryption key. Returns True on success."""
        if not self._processor:
            return False
        try:
            return self._processor.set_channel_key(key)
        except Exception as e:
            logger.debug("Failed to set decryption key: %s", e)
            return False

    def decrypt_packet(self, raw_bytes: bytes,
                       channel_key: str = "AQ==") -> Optional[Dict]:
        """Decrypt a raw MQTT packet payload.

        Args:
            raw_bytes: Raw encrypted packet bytes
            channel_key: Base64-encoded channel encryption key

        Returns:
            Dict with decoded fields (portnum, portnum_name, text, position, etc.)
            or None if decryption fails or is unavailable.
        """
        if not self._available or not self._processor:
            return None

        with self._lock:
            try:
                # Update key if different from current
                if channel_key and channel_key != "AQ==":
                    self._processor.set_channel_key(channel_key)

                result = self._processor.process_encrypted_packet(raw_bytes)

                if result and result.success and result.decoded:
                    self._decrypted_count += 1
                    output = dict(result.decoded)
                    output["portnum"] = result.portnum
                    output["portnum_name"] = result.portnum_name
                    output["packet_id"] = result.packet_id
                    output["sender"] = result.sender
                    return output

                self._failed_count += 1
                return None

            except Exception as e:
                self._failed_count += 1
                logger.debug("Packet decryption failed: %s", e)
                return None

    def get_stats(self) -> Dict:
        """Get decryption statistics."""
        return {
            "available": self._available,
            "decrypted": self._decrypted_count,
            "failed": self._failed_count,
        }


# ── Module-level singleton ──────────────────────────────────────

_decryptor: Optional[MQTTDecryptorBridge] = None
_decryptor_lock = threading.Lock()


def get_decryptor(key: str = "AQ==") -> MQTTDecryptorBridge:
    """Get or create the singleton MQTTDecryptorBridge."""
    global _decryptor
    if _decryptor is None:
        with _decryptor_lock:
            if _decryptor is None:
                _decryptor = MQTTDecryptorBridge(default_key=key)
    return _decryptor
