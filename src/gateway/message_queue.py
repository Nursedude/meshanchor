"""
Persistent Message Queue for MeshForge Gateway.

Ensures reliable message delivery across network boundaries:
- SQLite-backed persistence (survives restarts)
- Automatic retry with exponential backoff
- Deduplication to prevent message loops
- Priority queuing
- Dead letter queue for failed messages

Usage:
    queue = PersistentMessageQueue()
    queue.enqueue(message, destination="meshtastic")

    # Process queue
    queue.process(send_callback)
"""

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from contextlib import contextmanager

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    """Message priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class MessageStatus(Enum):
    """Message delivery status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class QueuedMessage:
    """Message in the queue."""
    id: str
    payload: Dict[str, Any]
    destination: str  # "meshtastic", "rns", "mqtt"
    priority: MessagePriority = MessagePriority.NORMAL
    status: MessageStatus = MessageStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    retry_count: int = 0
    max_retries: int = 3
    retry_after: Optional[datetime] = None
    error_message: str = ""
    content_hash: str = ""  # For deduplication

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "payload": json.dumps(self.payload),
            "destination": self.destination,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "retry_after": self.retry_after.isoformat() if self.retry_after else None,
            "error_message": self.error_message,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'QueuedMessage':
        """Create from dictionary."""
        return cls(
            id=data["id"],
            payload=json.loads(data["payload"]) if isinstance(data["payload"], str) else data["payload"],
            destination=data["destination"],
            priority=MessagePriority(data["priority"]),
            status=MessageStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            retry_count=data["retry_count"],
            max_retries=data["max_retries"],
            retry_after=datetime.fromisoformat(data["retry_after"]) if data.get("retry_after") else None,
            error_message=data.get("error_message", ""),
            content_hash=data.get("content_hash", ""),
        )


class PersistentMessageQueue:
    """
    SQLite-backed persistent message queue.

    Features:
    - Survives application restarts
    - ACID transactions
    - Automatic retry with backoff
    - Deduplication within time window
    - Priority ordering
    """

    # Retry backoff: 5s, 15s, 45s
    RETRY_DELAYS = [5, 15, 45]

    # Deduplication window (seconds)
    DEDUP_WINDOW = 60

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the message queue.

        Args:
            db_path: Path to SQLite database. Default: ~/.config/meshforge/message_queue.db
        """
        if db_path is None:
            config_dir = get_real_user_home() / ".config" / "meshforge"
            config_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(config_dir / "message_queue.db")

        self._db_path = db_path
        self._lock = threading.Lock()
        self._processing = False
        self._process_thread = None
        self._stop_event = threading.Event()

        # Callbacks
        self._send_callbacks: Dict[str, Callable] = {}  # destination -> send_fn
        self._success_callbacks: List[Callable] = []
        self._failure_callbacks: List[Callable] = []

        # Initialize database
        self._init_db()

        # Stats
        self._stats = {
            "enqueued": 0,
            "delivered": 0,
            "failed": 0,
            "retried": 0,
            "deduplicated": 0,
        }

    @contextmanager
    def _get_connection(self):
        """Get database connection with context management."""
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise  # Re-raise after rollback - exception is handled by caller
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    priority INTEGER DEFAULT 2,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    retry_after TEXT,
                    error_message TEXT DEFAULT '',
                    content_hash TEXT DEFAULT ''
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON messages(status)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_priority_created
                ON messages(priority DESC, created_at ASC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash
                ON messages(content_hash)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retry_after
                ON messages(retry_after)
            """)

    def _compute_hash(self, payload: Dict) -> str:
        """Compute content hash for deduplication."""
        # Hash key fields that identify a unique message
        key_data = json.dumps({
            "from": payload.get("from"),
            "to": payload.get("to"),
            "text": payload.get("text"),
            "type": payload.get("type"),
        }, sort_keys=True)
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def _is_duplicate(self, content_hash: str, destination: str) -> bool:
        """Check if message is a recent duplicate."""
        cutoff = (datetime.now() - timedelta(seconds=self.DEDUP_WINDOW)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM messages
                WHERE content_hash = ? AND destination = ?
                AND created_at > ?
                AND status IN ('pending', 'in_progress', 'delivered')
            """, (content_hash, destination, cutoff))

            count = cursor.fetchone()[0]
            return count > 0

    def enqueue(self, payload: Dict[str, Any], destination: str,
                priority: MessagePriority = MessagePriority.NORMAL,
                max_retries: int = 3, deduplicate: bool = True) -> Optional[str]:
        """
        Add a message to the queue.

        Args:
            payload: Message payload dictionary
            destination: Target system ("meshtastic", "rns", "mqtt")
            priority: Message priority
            max_retries: Maximum retry attempts
            deduplicate: Check for duplicates

        Returns:
            Message ID if enqueued, None if duplicate
        """
        content_hash = self._compute_hash(payload)

        # Check for duplicates
        if deduplicate and self._is_duplicate(content_hash, destination):
            self._stats["deduplicated"] += 1
            logger.debug(f"Duplicate message suppressed: {content_hash}")
            return None

        # Generate unique ID
        msg_id = f"{int(time.time() * 1000)}-{content_hash[:8]}"

        message = QueuedMessage(
            id=msg_id,
            payload=payload,
            destination=destination,
            priority=priority,
            max_retries=max_retries,
            content_hash=content_hash,
        )

        with self._get_connection() as conn:
            data = message.to_dict()
            conn.execute("""
                INSERT INTO messages
                (id, payload, destination, priority, status, created_at, updated_at,
                 retry_count, max_retries, retry_after, error_message, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["id"], data["payload"], data["destination"],
                data["priority"], data["status"], data["created_at"],
                data["updated_at"], data["retry_count"], data["max_retries"],
                data["retry_after"], data["error_message"], data["content_hash"]
            ))

        self._stats["enqueued"] += 1
        logger.debug(f"Message enqueued: {msg_id} -> {destination}")

        return msg_id

    def get_pending(self, destination: Optional[str] = None,
                    limit: int = 100) -> List[QueuedMessage]:
        """Get pending messages ready for delivery."""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            if destination:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND destination = ?
                    AND (retry_after IS NULL OR retry_after <= ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (destination, now, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM messages
                    WHERE status = 'pending'
                    AND (retry_after IS NULL OR retry_after <= ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (now, limit))

            return [QueuedMessage.from_dict(dict(row)) for row in cursor.fetchall()]

    def mark_in_progress(self, msg_id: str) -> bool:
        """Mark message as in progress."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'in_progress', updated_at = ?
                WHERE id = ? AND status = 'pending'
            """, (datetime.now().isoformat(), msg_id))
            return cursor.rowcount > 0

    def mark_delivered(self, msg_id: str) -> bool:
        """Mark message as successfully delivered."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'delivered', updated_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), msg_id))

            if cursor.rowcount > 0:
                self._stats["delivered"] += 1
                return True
            return False

    def mark_failed(self, msg_id: str, error: str = "") -> bool:
        """
        Mark message as failed and schedule retry or move to dead letter.
        """
        with self._get_connection() as conn:
            # Get current message
            cursor = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (msg_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False

            message = QueuedMessage.from_dict(dict(row))
            message.retry_count += 1
            message.error_message = error
            message.updated_at = datetime.now()

            if message.retry_count >= message.max_retries:
                # Move to dead letter
                message.status = MessageStatus.DEAD_LETTER
                self._stats["failed"] += 1
                logger.warning(f"Message {msg_id} moved to dead letter after {message.retry_count} retries")
            else:
                # Schedule retry with backoff
                delay_idx = min(message.retry_count - 1, len(self.RETRY_DELAYS) - 1)
                delay = self.RETRY_DELAYS[delay_idx]
                message.retry_after = datetime.now() + timedelta(seconds=delay)
                message.status = MessageStatus.PENDING
                self._stats["retried"] += 1
                logger.debug(f"Message {msg_id} scheduled for retry in {delay}s")

            # Update in database
            data = message.to_dict()
            conn.execute("""
                UPDATE messages
                SET status = ?, updated_at = ?, retry_count = ?,
                    retry_after = ?, error_message = ?
                WHERE id = ?
            """, (
                data["status"], data["updated_at"], data["retry_count"],
                data["retry_after"], data["error_message"], msg_id
            ))

            return True

    def register_sender(self, destination: str,
                        send_fn: Callable[[Dict], bool]) -> None:
        """
        Register a send function for a destination.

        Args:
            destination: Target system name
            send_fn: Function that takes payload dict, returns True if sent
        """
        self._send_callbacks[destination] = send_fn

    def register_success_callback(self, callback: Callable[[QueuedMessage], None]) -> None:
        """Register callback for successful delivery."""
        self._success_callbacks.append(callback)

    def register_failure_callback(self, callback: Callable[[QueuedMessage, str], None]) -> None:
        """Register callback for failed delivery."""
        self._failure_callbacks.append(callback)

    def process_once(self, batch_size: int = 10) -> int:
        """
        Process one batch of pending messages.

        Returns:
            Number of messages processed
        """
        processed = 0

        for destination, send_fn in self._send_callbacks.items():
            messages = self.get_pending(destination=destination, limit=batch_size)

            for message in messages:
                if not self.mark_in_progress(message.id):
                    continue

                try:
                    success = send_fn(message.payload)

                    if success:
                        self.mark_delivered(message.id)
                        for callback in self._success_callbacks:
                            try:
                                callback(message)
                            except Exception as e:
                                logger.debug(f"Success callback error: {e}")
                    else:
                        self.mark_failed(message.id, "Send returned False")

                    processed += 1

                except Exception as e:
                    self.mark_failed(message.id, str(e))
                    for callback in self._failure_callbacks:
                        try:
                            callback(message, str(e))
                        except Exception as e2:
                            logger.debug(f"Failure callback error: {e2}")
                    processed += 1

        return processed

    def start_processing(self, interval: float = 1.0) -> None:
        """Start background processing thread."""
        if self._processing:
            return

        self._processing = True
        self._stop_event.clear()

        def process_loop():
            while not self._stop_event.is_set():
                try:
                    self.process_once()
                except Exception as e:
                    logger.error(f"Queue processing error: {e}")

                self._stop_event.wait(interval)

            self._processing = False

        self._process_thread = threading.Thread(target=process_loop, daemon=True)
        self._process_thread.start()
        logger.info("Message queue processing started")

    def stop_processing(self) -> None:
        """Stop background processing."""
        self._stop_event.set()
        if self._process_thread:
            self._process_thread.join(timeout=5)
        logger.info("Message queue processing stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM messages
                GROUP BY status
            """)
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

        return {
            **self._stats,
            "pending": status_counts.get("pending", 0),
            "in_progress": status_counts.get("in_progress", 0),
            "delivered": status_counts.get("delivered", 0),
            "failed": status_counts.get("failed", 0),
            "dead_letter": status_counts.get("dead_letter", 0),
        }

    def get_dead_letters(self, limit: int = 100) -> List[QueuedMessage]:
        """Get messages in dead letter queue."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM messages
                WHERE status = 'dead_letter'
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))

            return [QueuedMessage.from_dict(dict(row)) for row in cursor.fetchall()]

    def retry_dead_letter(self, msg_id: str) -> bool:
        """Retry a dead letter message."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE messages
                SET status = 'pending', retry_count = 0,
                    retry_after = NULL, updated_at = ?
                WHERE id = ? AND status = 'dead_letter'
            """, (datetime.now().isoformat(), msg_id))
            return cursor.rowcount > 0

    def purge_old(self, days: int = 7) -> int:
        """
        Purge delivered and dead letter messages older than N days.

        Returns:
            Number of messages purged
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM messages
                WHERE status IN ('delivered', 'dead_letter')
                AND updated_at < ?
            """, (cutoff,))

            count = cursor.rowcount
            if count > 0:
                logger.info(f"Purged {count} old messages")
            return count

    def clear_all(self) -> int:
        """Clear all messages (use with caution)."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM messages")
            return cursor.rowcount
