"""
Message routing and classification for the RNS-Meshtastic bridge.

Extracted from rns_bridge.py to keep file sizes manageable.
Handles routing rule compilation, confidence-scored classification,
and legacy regex-based routing logic.
"""

import re
import logging
from typing import Optional, Dict, Any

from utils.safe_import import safe_import

from .bridge_health import MessageOrigin

# Import routing classifier with confidence scoring
(_RoutingClassifier, _RoutingCategory, _create_routing_system,
 _ClassificationResult, CLASSIFIER_AVAILABLE) = safe_import(
    'utils.classifier',
    'RoutingClassifier', 'RoutingCategory',
    'create_routing_system', 'ClassificationResult',
)

if CLASSIFIER_AVAILABLE:
    RoutingClassifier = _RoutingClassifier
    RoutingCategory = _RoutingCategory
    create_routing_system = _create_routing_system
    ClassificationResult = _ClassificationResult

# Import centralized path utility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


class MessageRouter:
    """
    Routes messages between RNS and Meshtastic based on rules and classification.

    Supports two modes:
    1. Confidence-scored classifier (when utils.classifier is available)
    2. Legacy regex-based routing rules (fallback)
    """

    # Maximum input length for regex matching to bound execution time
    _REGEX_INPUT_LIMIT = 512

    def __init__(self, config, stats: dict, stats_lock):
        """
        Args:
            config: GatewayConfig instance with routing_rules and settings.
            stats: Shared stats dict (bridge owns it, router updates 'bounced').
            stats_lock: Threading lock for stats updates.
        """
        self.config = config
        self.stats = stats
        self._stats_lock = stats_lock

        # Pre-compile routing rule regexes to avoid re-compilation per message
        # and catch invalid patterns at startup rather than at runtime
        self._compiled_rules = self._compile_routing_rules()

        # Routing classifier with confidence scoring
        self._classifier = None
        self._last_classification: Optional[ClassificationResult] = None
        if CLASSIFIER_AVAILABLE:
            fixes_path = get_real_user_home() / '.config' / 'meshforge' / 'routing_fixes.json'
            rules = [
                {
                    'name': rule.name,
                    'enabled': rule.enabled,
                    'direction': rule.direction,
                    'source_filter': rule.source_filter,
                    'dest_filter': rule.dest_filter,
                    'message_filter': rule.message_filter,
                    'priority': rule.priority
                }
                for rule in self.config.routing_rules
            ]
            self._classifier = create_routing_system(
                rules=rules,
                bounce_threshold=0.3,
                fixes_path=fixes_path
            )
            logger.info("Routing classifier initialized with confidence scoring")

    def should_bridge(self, msg) -> bool:
        """
        Check if message should be bridged based on routing rules.

        Uses confidence-scored classifier when available:
        - High confidence (>0.7): Route immediately
        - Low confidence (<0.3): Bounce to queue for review
        - Medium confidence: Route with logging
        """
        if not self.config.enabled:
            return False

        # Use classifier if available
        if self._classifier:
            return self._classify_message(msg)

        # Fallback to legacy logic
        return self._should_bridge_legacy(msg)

    def _classify_message(self, msg) -> bool:
        """Classify message using confidence-scored routing."""
        msg_id = f"{msg.source_network}:{msg.source_id}:{msg.timestamp.isoformat()}"

        result = self._classifier.classify(msg_id, {
            'source_network': msg.source_network,
            'source_id': msg.source_id,
            'destination_id': msg.destination_id,
            'content': msg.content,
            'is_broadcast': msg.is_broadcast,
            'metadata': msg.metadata
        })

        self._last_classification = result

        # Handle bounced messages
        if result.bounced:
            with self._stats_lock:
                self.stats['bounced'] += 1
            logger.info(
                f"Message bounced (confidence {result.confidence:.2f}): "
                f"{msg.source_id[:8]}... -> {result.bounce_reason}"
            )
            # Bounced messages go to queue category, don't bridge immediately
            return result.category == RoutingCategory.QUEUE.value

        # Log classification decision
        if result.confidence < 0.7:
            logger.debug(
                f"Routing decision (confidence {result.confidence:.2f}): "
                f"{result.category} - {result.reason}"
            )

        # Determine if we should bridge based on category
        if result.category == RoutingCategory.DROP.value:
            return False
        elif result.category in (RoutingCategory.BRIDGE_RNS.value, RoutingCategory.BRIDGE_MESH.value):
            return True
        elif result.category == RoutingCategory.QUEUE.value:
            # Queued items need manual review
            return False

        return False

    def _compile_routing_rules(self) -> dict:
        """Pre-compile regex patterns from routing rules at init time.

        Returns a dict mapping rule name to compiled filter patterns.
        Invalid patterns are logged and skipped — the rule will never match.
        """
        compiled = {}
        for rule in self.config.routing_rules:
            filters = {}
            for field in ('source_filter', 'dest_filter', 'message_filter'):
                pattern = getattr(rule, field, '')
                if pattern:
                    try:
                        filters[field] = re.compile(pattern)
                    except re.error as e:
                        logger.warning(
                            f"Invalid regex in rule '{rule.name}' "
                            f"field '{field}': {e} — rule will be skipped"
                        )
                        filters[field] = None  # Mark as broken
            compiled[rule.name] = filters
        return compiled

    def _should_bridge_legacy(self, msg) -> bool:
        """Legacy routing logic (fallback when classifier unavailable)."""
        # Re-compile if routing rules changed since last compile
        current_names = {r.name for r in self.config.routing_rules}
        if current_names != set(self._compiled_rules.keys()):
            self._compiled_rules = self._compile_routing_rules()

        for rule in self.config.routing_rules:
            if not rule.enabled:
                continue

            # Check direction
            if msg.source_network == "meshtastic" and rule.direction == "rns_to_mesh":
                continue
            if msg.source_network == "rns" and rule.direction == "mesh_to_rns":
                continue

            # Get pre-compiled filters for this rule
            filters = self._compiled_rules.get(rule.name, {})

            # Skip rule entirely if any of its patterns failed to compile
            if any(v is None for v in filters.values()):
                continue

            # Apply pre-compiled regex filters with bounded input
            # Source filter
            if rule.source_filter:
                compiled = filters.get('source_filter')
                if not compiled or not msg.source_id:
                    continue
                if not compiled.search(msg.source_id[:self._REGEX_INPUT_LIMIT]):
                    continue

            # Destination filter
            if rule.dest_filter:
                compiled = filters.get('dest_filter')
                if not compiled:
                    continue
                dest = (msg.destination_id or "")[:self._REGEX_INPUT_LIMIT]
                if not compiled.search(dest):
                    continue

            # Message content filter
            if rule.message_filter:
                compiled = filters.get('message_filter')
                if not compiled or not msg.content:
                    continue
                if not compiled.search(msg.content[:self._REGEX_INPUT_LIMIT]):
                    continue

            # All filters passed - this rule matches
            return True

        return self.config.default_route in ("bidirectional", f"{msg.source_network}_to_*")

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing classifier statistics."""
        stats = dict(self.stats)
        if self._classifier:
            classifier_stats = self._classifier.get_stats()
            stats['classifier'] = classifier_stats
            stats['bouncer_queue'] = len(self._classifier.bouncer.get_queue())
        return stats

    def get_last_classification(self) -> Optional[Dict]:
        """Get the last classification result for debugging."""
        if self._last_classification:
            return self._last_classification.to_dict()
        return None

    def fix_routing(self, msg_id: str, correct_category: str) -> bool:
        """
        Record a user correction for routing decisions.

        This is the 'fix button' - allows users to correct mistakes
        and improve the system over time.
        """
        if not self._classifier or not self._classifier.fix_registry:
            return False

        # Create a dummy result for the fix
        result = ClassificationResult(
            input_id=msg_id,
            category="unknown",
            confidence=0.5
        )
        self._classifier.fix_registry.add_fix(result, correct_category)
        logger.info(f"Routing fix recorded: {msg_id} -> {correct_category}")
        return True
