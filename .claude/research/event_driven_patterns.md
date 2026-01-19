# Event-Driven Architecture Research for MeshForge

**Date:** 2026-01-19
**Context:** Zapier workflow patterns applied to mesh NOC software

---

## Key Patterns from Research

### 1. Polling vs Event-Driven (Zapier Triggers)

**Current MeshForge Pattern (Polling):**
```python
# Status timer fires every 5-10 seconds
GLib.timeout_add(5000, self._update_status)

# Checks services, node count, etc.
def _update_status(self):
    status = check_service('meshtasticd')
    node_count = get_node_count()
    # Update UI
```

**Better Pattern (Event-Driven):**
```python
# Service emits events when state changes
event_bus.subscribe("service.meshtasticd.changed", self._on_service_changed)

# Only updates UI when something actually changes
def _on_service_changed(self, event):
    GLib.idle_add(self._update_status_display, event.new_state)
```

**Benefits:**
- No wasted CPU cycles on unchanged state
- Instant response to changes
- Reduced subprocess calls
- Better battery life (important for portable mesh deployments)

---

### 2. Event Bus Architecture

**Reference:** [Cosmic Python Event Bus](https://www.cosmicpython.com/book/chapter_08_events_and_message_bus.html)

**Proposed MeshForge Event Bus:**

```python
# src/utils/event_bus.py

from dataclasses import dataclass
from typing import Callable, Dict, List, Any
from collections import defaultdict
import threading
import logging

logger = logging.getLogger(__name__)

@dataclass
class Event:
    """Base event class."""
    name: str
    data: Dict[str, Any]
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            import time
            self.timestamp = time.time()


class EventBus:
    """
    Simple pub/sub event bus for MeshForge.

    Thread-safe, supports GTK main thread callbacks via GLib.idle_add.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # Singleton pattern
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._subscribers = defaultdict(list)
        return cls._instance

    def subscribe(self, event_name: str, callback: Callable, gtk_thread: bool = False):
        """
        Subscribe to an event.

        Args:
            event_name: Event to subscribe to (supports wildcards: "message.*")
            callback: Function to call when event fires
            gtk_thread: If True, callback will be scheduled via GLib.idle_add
        """
        self._subscribers[event_name].append({
            'callback': callback,
            'gtk_thread': gtk_thread
        })
        logger.debug(f"Subscribed to {event_name}: {callback.__name__}")

    def unsubscribe(self, event_name: str, callback: Callable):
        """Unsubscribe from an event."""
        self._subscribers[event_name] = [
            s for s in self._subscribers[event_name]
            if s['callback'] != callback
        ]

    def publish(self, event: Event):
        """
        Publish an event to all subscribers.

        Thread-safe. GTK callbacks will be scheduled to main thread.
        """
        event_name = event.name
        logger.debug(f"Publishing event: {event_name}")

        # Direct subscribers
        for subscriber in self._subscribers[event_name]:
            self._dispatch(subscriber, event)

        # Wildcard subscribers (e.g., "message.*" matches "message.rx")
        for pattern, subscribers in self._subscribers.items():
            if pattern.endswith('.*'):
                prefix = pattern[:-2]
                if event_name.startswith(prefix):
                    for subscriber in subscribers:
                        self._dispatch(subscriber, event)

    def _dispatch(self, subscriber: dict, event: Event):
        """Dispatch event to subscriber."""
        try:
            if subscriber['gtk_thread']:
                from gi.repository import GLib
                GLib.idle_add(subscriber['callback'], event)
            else:
                subscriber['callback'](event)
        except Exception as e:
            logger.error(f"Event handler error: {e}")


# Singleton accessor
def get_event_bus() -> EventBus:
    return EventBus()
```

---

### 3. Standard Event Types for MeshForge

```python
# Event naming convention: category.subcategory.action

# Service events
"service.meshtasticd.started"
"service.meshtasticd.stopped"
"service.rnsd.connected"
"service.rnsd.disconnected"

# Message events (Issue #20 Phase 3)
"message.rx"          # Received from mesh
"message.tx"          # Sent to mesh
"message.tx.ack"      # Delivery acknowledged
"message.tx.failed"   # Delivery failed

# Node events
"node.discovered"     # New node found
"node.updated"        # Node telemetry updated
"node.lost"           # Node went offline

# Gateway events
"gateway.connected"
"gateway.disconnected"
"gateway.bridge.active"
```

---

### 4. Application to Current Issues

#### Issue #20 Phase 3: RX Message Display

**Current (broken):**
```
gateway receives packet → log only → UI never updated
```

**Proposed (event-driven):**
```python
# In gateway/rns_bridge.py
def _on_packet_received(self, packet):
    # Process packet...

    # Emit event
    event_bus.publish(Event(
        name="message.rx",
        data={
            "source": packet.source,
            "content": packet.content,
            "timestamp": time.time()
        }
    ))

# In gtk_ui/panels/messaging.py
def __init__(self, main_window):
    super().__init__(main_window)
    # Subscribe with gtk_thread=True for safe UI update
    event_bus.subscribe("message.rx", self._on_message_received, gtk_thread=True)

def _on_message_received(self, event):
    # Already on GTK main thread via GLib.idle_add
    self._add_message_to_list(
        direction="rx",
        source=event.data["source"],
        content=event.data["content"]
    )
```

#### Status Timer Optimization

**Current:** 6+ timers polling at 5-10 second intervals

**Proposed:** Event-driven status updates
- Services emit events when state changes
- UI subscribes to events instead of polling
- Reduces subprocess calls from ~100/minute to ~5/minute

---

### 5. Implementation Roadmap

| Phase | Effort | Impact | Priority |
|-------|--------|--------|----------|
| Create event_bus.py | LOW | HIGH | 1 |
| Message events (Issue #20) | MEDIUM | HIGH | 2 |
| Service state events | MEDIUM | MEDIUM | 3 |
| Node discovery events | HIGH | MEDIUM | 4 |
| Replace polling timers | HIGH | LOW | 5 |

---

### 6. Trade-offs

**Event-Driven Pros:**
- Reduced CPU usage
- Instant UI updates
- Cleaner component separation
- Easier testing (mock events)

**Event-Driven Cons:**
- Debugging harder (trace event flow)
- Requires careful unsubscribe (memory leaks)
- Learning curve for new patterns

**Recommendation:** Start with message events (Phase 3 of Issue #20) as proof of concept. If successful, expand to service events.

---

## Sources

- [Zapier Trigger Architecture](https://help.zapier.com/hc/en-us/articles/8496244568589-How-Zap-triggers-work)
- [Webhooks by Zapier](https://help.zapier.com/hc/en-us/articles/8496288690317-Trigger-Zaps-from-webhooks)
- [Cosmic Python - Events and Message Bus](https://www.cosmicpython.com/book/chapter_08_events_and_message_bus.html)
- [Event Bus Implementation](https://python.plainenglish.io/simple-yet-powerful-building-an-in-memory-async-event-bus-in-python-f87e3d505bdd)
- [bubus - Production Event Bus](https://github.com/browser-use/bubus)
- [Publish-Subscribe Pattern](https://arjancodes.com/blog/publish-subscribe-pattern-in-python/)

---

*Research compiled for MeshForge workflow improvements*
