"""
MeshChat HTTP Client

Provides Python interface to MeshChat's REST + WebSocket API (v1).
All network operations have timeouts and proper error handling.

API reference: github.com/liamcottle/reticulum-meshchat
Endpoints use /api/v1/ prefix.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

from utils.safe_import import safe_import
from utils.timeouts import HTTP_CONNECT as _MESHCHAT_DEFAULT_TIMEOUT

# Module-level safe imports for optional dependencies
_requests, _HAS_REQUESTS = safe_import('requests')

logger = logging.getLogger(__name__)


class MeshChatError(Exception):
    """Base exception for MeshChat client errors."""
    pass


class MeshChatConnectionError(MeshChatError):
    """Failed to connect to MeshChat service."""
    pass


class MeshChatAPIError(MeshChatError):
    """MeshChat API returned an error."""
    pass


@dataclass
class MeshChatPeer:
    """Represents a peer discovered by MeshChat (from /api/v1/announces)."""
    destination_hash: str
    display_name: Optional[str] = None
    last_announce: Optional[datetime] = None
    is_online: bool = False
    app_data: Optional[Dict[str, Any]] = None
    identity_hash: Optional[str] = None
    aspect: Optional[str] = None
    snr: Optional[float] = None
    rssi: Optional[float] = None
    quality: Optional[float] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> 'MeshChatPeer':
        """Create peer from MeshChat announce API response.

        MeshChat announces contain: destination_hash, identity_hash,
        aspect, app_data, snr, rssi, quality, created_at, updated_at.
        """
        # Parse timestamp from updated_at/created_at (Unix timestamp or ISO string)
        last_announce = None
        ts_field = (data.get('updated_at') or data.get('created_at')
                    or data.get('last_announce'))
        if ts_field is not None:
            try:
                if isinstance(ts_field, (int, float)):
                    last_announce = datetime.fromtimestamp(ts_field)
                else:
                    last_announce = datetime.fromisoformat(str(ts_field))
            except (ValueError, TypeError, OSError):
                pass

        # Extract display_name: explicit field, or app_data string (LXMF convention)
        app_data_raw = data.get('app_data')
        display_name = data.get('display_name', data.get('name'))
        if display_name is None and isinstance(app_data_raw, str) and app_data_raw:
            display_name = app_data_raw

        # Derive is_online heuristically from announce recency
        is_online = False
        if last_announce:
            age = (datetime.now() - last_announce).total_seconds()
            is_online = age < 900  # 15 minutes

        return cls(
            destination_hash=data.get('destination_hash', data.get('hash', '')),
            display_name=display_name,
            last_announce=last_announce,
            is_online=data.get('is_online', is_online),
            app_data=app_data_raw if isinstance(app_data_raw, dict) else None,
            identity_hash=data.get('identity_hash'),
            aspect=data.get('aspect'),
            snr=data.get('snr'),
            rssi=data.get('rssi'),
            quality=data.get('quality'),
        )


@dataclass
class MeshChatMessage:
    """Represents an LXMF message."""
    message_id: str
    source_hash: str
    destination_hash: str
    content: str
    timestamp: datetime
    is_incoming: bool
    delivered: bool = False
    read: bool = False

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> 'MeshChatMessage':
        """Create message from API response."""
        timestamp = datetime.now()
        if data.get('timestamp'):
            try:
                ts = data['timestamp']
                if isinstance(ts, (int, float)):
                    timestamp = datetime.fromtimestamp(ts)
                else:
                    timestamp = datetime.fromisoformat(str(ts))
            except (ValueError, TypeError, OSError):
                pass

        return cls(
            message_id=data.get('id', data.get('hash', '')),
            source_hash=data.get('source_hash', data.get('from', '')),
            destination_hash=data.get('destination_hash', data.get('to', '')),
            content=data.get('content', data.get('message', '')),
            timestamp=timestamp,
            is_incoming=data.get('is_incoming', data.get('incoming', False)),
            delivered=data.get('delivered', False),
            read=data.get('read', False)
        )


@dataclass
class MeshChatStatus:
    """MeshChat service status (assembled from /api/v1/app/info + /api/v1/config)."""
    version: Optional[str] = None
    identity_hash: Optional[str] = None
    display_name: Optional[str] = None
    peer_count: int = 0
    message_count: int = 0
    propagation_node: bool = False
    rns_connected: bool = False
    uptime_seconds: int = 0


class MeshChatClient:
    """
    HTTP client for MeshChat API (v1).

    MeshChat exposes a REST API at /api/v1/ and WebSocket for real-time updates.
    This client provides read/write access to announces, messages, and status.

    Usage:
        client = MeshChatClient()
        if client.is_available():
            peers = client.get_peers()
            client.send_message(destination_hash, "Hello from MeshForge!")
    """

    DEFAULT_HOST = '127.0.0.1'
    DEFAULT_PORT = 8000
    DEFAULT_TIMEOUT = _MESHCHAT_DEFAULT_TIMEOUT

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._base_url = f"http://{host}:{port}"
        self._session = None

    def _get_session(self):
        """Get or create requests session (lazy import)."""
        if self._session is None:
            if not _HAS_REQUESTS:
                raise MeshChatError("requests library not installed")
            self._session = _requests.Session()
            self._session.headers.update({
                'User-Agent': 'MeshForge/1.0',
                'Accept': 'application/json'
            })
        return self._session

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to MeshChat API."""
        url = urljoin(self._base_url, endpoint)
        session = self._get_session()

        try:
            if method.upper() == 'GET':
                response = session.get(url, params=params, timeout=self.timeout)
            elif method.upper() == 'POST':
                response = session.post(url, json=data, timeout=self.timeout)
            elif method.upper() == 'DELETE':
                response = session.delete(url, timeout=self.timeout)
            else:
                raise MeshChatError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()

            if response.content:
                return response.json()
            return {}

        except Exception as e:
            if 'ConnectionError' in type(e).__name__ or 'ConnectTimeout' in type(e).__name__:
                raise MeshChatConnectionError(
                    f"Cannot connect to MeshChat at {self._base_url}: {e}"
                )
            elif 'HTTPError' in type(e).__name__:
                raise MeshChatAPIError(f"MeshChat API error: {e}")
            elif 'Timeout' in type(e).__name__:
                raise MeshChatConnectionError(f"MeshChat request timed out: {e}")
            else:
                raise MeshChatError(f"MeshChat request failed: {e}")

    def is_available(self) -> bool:
        """Check if MeshChat service is reachable."""
        try:
            self._request('GET', '/api/v1/status')
            return True
        except MeshChatError:
            return False

    def get_status(self) -> MeshChatStatus:
        """Get MeshChat service status from /api/v1/app/info."""
        try:
            data = self._request('GET', '/api/v1/app/info')
            app_info = data.get('app_info', data)

            # Fetch announce count for peer_count
            peer_count = 0
            try:
                announces_data = self._request(
                    'GET', '/api/v1/announces', params={'limit': 1000}
                )
                announces = announces_data.get('announces', [])
                peer_count = len(announces)
            except MeshChatError:
                pass

            return MeshChatStatus(
                version=app_info.get('version'),
                identity_hash=app_info.get('identity_hash', app_info.get('identity')),
                display_name=app_info.get('display_name', app_info.get('name')),
                peer_count=peer_count,
                rns_connected=app_info.get(
                    'is_connected_to_shared_instance',
                    app_info.get('rns_connected', True)
                ),
            )
        except MeshChatConnectionError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get MeshChat status: {e}")
            return MeshChatStatus()

    def get_peers(self) -> List[MeshChatPeer]:
        """Get list of discovered peers (announces) from /api/v1/announces."""
        try:
            data = self._request('GET', '/api/v1/announces')
            announces = data.get('announces', []) if isinstance(data, dict) else data
            return [MeshChatPeer.from_api(a) for a in announces]
        except MeshChatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get peers: {e}")
            return []

    def get_conversations(self) -> List[Dict[str, Any]]:
        """Get list of LXMF conversations from /api/v1/lxmf/conversations."""
        try:
            data = self._request('GET', '/api/v1/lxmf/conversations')
            if isinstance(data, list):
                return data
            return data.get('conversations', [])
        except MeshChatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get conversations: {e}")
            return []

    def get_messages(
        self,
        destination_hash: Optional[str] = None,
        limit: int = 50
    ) -> List[MeshChatMessage]:
        """Get messages from MeshChat.

        If destination_hash is provided, fetches messages for that conversation.
        Otherwise, fetches recent messages across all conversations.
        """
        try:
            if destination_hash:
                endpoint = f'/api/v1/lxmf-messages/conversation/{destination_hash}'
                data = self._request('GET', endpoint)
            else:
                # Fetch conversations and aggregate recent messages
                conversations = self.get_conversations()
                all_messages = []
                for conv in conversations[:10]:  # Limit to 10 most recent convos
                    conv_hash = conv.get('destination_hash', conv.get('hash', ''))
                    if not conv_hash:
                        continue
                    try:
                        endpoint = f'/api/v1/lxmf-messages/conversation/{conv_hash}'
                        conv_data = self._request('GET', endpoint)
                        msgs = (conv_data.get('messages', conv_data)
                                if isinstance(conv_data, dict) else conv_data)
                        if isinstance(msgs, list):
                            all_messages.extend(msgs)
                    except MeshChatError:
                        continue
                # Sort by timestamp descending and limit
                all_messages.sort(
                    key=lambda m: m.get('timestamp', 0), reverse=True
                )
                return [
                    MeshChatMessage.from_api(m) for m in all_messages[:limit]
                ]

            messages = data.get('messages', data) if isinstance(data, dict) else data
            if not isinstance(messages, list):
                messages = []
            return [MeshChatMessage.from_api(m) for m in messages[:limit]]
        except MeshChatError:
            raise
        except Exception as e:
            logger.debug(f"Failed to get messages: {e}")
            return []

    def send_message(self, destination_hash: str, content: str) -> bool:
        """
        Send LXMF message via MeshChat.

        Args:
            destination_hash: Target peer's RNS destination hash
            content: Message text content

        Returns:
            True if message was queued for delivery
        """
        try:
            self._request('POST', '/api/v1/lxmf-messages/send', data={
                'lxmf_message': {
                    'destination_hash': destination_hash,
                    'content': content
                }
            })
            logger.info(f"Message queued to {destination_hash[:16]}...")
            return True
        except MeshChatError as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def send_announce(self) -> bool:
        """Send LXMF announce to network via GET /api/v1/announce."""
        try:
            self._request('GET', '/api/v1/announce')
            logger.info("Announce sent via MeshChat")
            return True
        except MeshChatError as e:
            logger.error(f"Failed to send announce: {e}")
            return False

    def get_identity(self) -> Optional[str]:
        """Get MeshChat's RNS identity hash."""
        try:
            status = self.get_status()
            return status.identity_hash
        except MeshChatError:
            return None

    def close(self):
        """Close the HTTP session."""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __repr__(self):
        return f"MeshChatClient({self._base_url})"
