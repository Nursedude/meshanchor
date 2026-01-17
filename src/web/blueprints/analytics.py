"""
Analytics and Webhooks REST API Blueprint

Provides API endpoints for:
- Coverage analytics
- Link budget history and trends
- Webhook management
"""

import json
import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# Import analytics and webhooks
try:
    from utils.analytics import (
        get_analytics_store,
        get_coverage_analyzer,
        LinkBudgetSample,
        NetworkHealthMetrics,
    )
    from utils.webhooks import (
        get_webhook_manager,
        WebhookEndpoint,
        EventType,
    )
    ANALYTICS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Analytics modules not available: {e}")
    ANALYTICS_AVAILABLE = False


bp = Blueprint('analytics', __name__, url_prefix='/api/analytics')


# ============================================================================
# Coverage Analytics Endpoints
# ============================================================================

@bp.route('/coverage', methods=['GET'])
def get_coverage():
    """
    Get current coverage statistics.

    Query params:
        nodes: JSON array of node objects with lat/lon (optional)

    Returns:
        Coverage statistics including area, radius, node count
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        analyzer = get_coverage_analyzer()

        # Check if nodes provided in request
        nodes_param = request.args.get('nodes')
        if nodes_param:
            nodes = json.loads(nodes_param)
        else:
            # Try to get nodes from node tracker
            try:
                from gateway.node_tracker import get_node_tracker
                tracker = get_node_tracker()
                nodes = [
                    {'lat': n.position.latitude, 'lon': n.position.longitude}
                    for n in tracker.get_all_nodes()
                    if n.position and n.position.is_valid()
                ]
            except Exception:
                nodes = []

        stats = analyzer.analyze_coverage(nodes)

        # Safely extract center point with bounds checking
        center_lat = stats.center_point[0] if stats.center_point and len(stats.center_point) >= 1 else 0.0
        center_lon = stats.center_point[1] if stats.center_point and len(stats.center_point) >= 2 else 0.0

        return jsonify({
            'total_nodes': stats.total_nodes,
            'nodes_with_position': stats.nodes_with_position,
            'bounding_box': stats.bounding_box,
            'center_point': {
                'latitude': center_lat,
                'longitude': center_lon,
            },
            'estimated_area_km2': stats.estimated_area_km2,
            'average_node_spacing_km': stats.average_node_spacing_km,
            'coverage_radius_km': stats.coverage_radius_km,
        })

    except Exception as e:
        logger.error(f"Coverage analytics error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/coverage/history', methods=['GET'])
def get_coverage_history():
    """
    Get coverage history over time.

    Query params:
        days: Number of days of history (default: 7)

    Returns:
        Array of coverage snapshots
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        days = request.args.get('days', 7, type=int)
        analyzer = get_coverage_analyzer()
        history = analyzer.get_coverage_history(days)

        return jsonify({
            'period_days': days,
            'snapshots': history,
        })

    except Exception as e:
        logger.error(f"Coverage history error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Link Budget Endpoints
# ============================================================================

@bp.route('/link-budget/history', methods=['GET'])
def get_link_budget_history():
    """
    Get link budget measurement history.

    Query params:
        source: Source node ID (optional)
        dest: Destination node ID (optional)
        hours: Hours of history (default: 24)

    Returns:
        Array of link budget samples
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        store = get_analytics_store()
        source = request.args.get('source')
        dest = request.args.get('dest')
        hours = request.args.get('hours', 24, type=int)

        history = store.get_link_budget_history(source, dest, hours)

        return jsonify({
            'period_hours': hours,
            'sample_count': len(history),
            'samples': [
                {
                    'timestamp': s.timestamp,
                    'source_node': s.source_node,
                    'dest_node': s.dest_node,
                    'rssi_dbm': s.rssi_dbm,
                    'snr_db': s.snr_db,
                    'distance_km': s.distance_km,
                    'packet_loss_pct': s.packet_loss_pct,
                    'link_quality': s.link_quality,
                }
                for s in history
            ],
        })

    except Exception as e:
        logger.error(f"Link budget history error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/link-budget/trends', methods=['GET'])
def get_link_budget_trends():
    """
    Get link budget trend analysis.

    Query params:
        source: Source node ID (required)
        dest: Destination node ID (required)
        hours: Hours of data to analyze (default: 168 = 1 week)

    Returns:
        Trend analysis with averages, min/max, and trend direction
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        source = request.args.get('source')
        dest = request.args.get('dest')

        if not source or not dest:
            return jsonify({
                'error': 'Both source and dest parameters required'
            }), 400

        hours = request.args.get('hours', 168, type=int)
        store = get_analytics_store()
        trends = store.get_link_budget_trends(source, dest, hours)

        return jsonify(trends)

    except Exception as e:
        logger.error(f"Link budget trends error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/link-budget', methods=['POST'])
def record_link_budget():
    """
    Record a link budget measurement.

    Body (JSON):
        source_node: Source node ID
        dest_node: Destination node ID
        rssi_dbm: RSSI in dBm
        snr_db: SNR in dB
        distance_km: Distance in km (optional)
        packet_loss_pct: Packet loss percentage
        link_quality: Quality level (excellent/good/fair/bad)

    Returns:
        Success status
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body required'}), 400

        from datetime import datetime
        sample = LinkBudgetSample(
            timestamp=datetime.now().isoformat(),
            source_node=data['source_node'],
            dest_node=data['dest_node'],
            rssi_dbm=data.get('rssi_dbm', 0),
            snr_db=data.get('snr_db', 0),
            distance_km=data.get('distance_km'),
            packet_loss_pct=data.get('packet_loss_pct', 0),
            link_quality=data.get('link_quality', 'unknown'),
        )

        store = get_analytics_store()
        store.record_link_budget(sample)

        return jsonify({'success': True, 'timestamp': sample.timestamp})

    except KeyError as e:
        return jsonify({'error': f'Missing required field: {e}'}), 400
    except Exception as e:
        logger.error(f"Record link budget error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Network Health Endpoints
# ============================================================================

@bp.route('/health/history', methods=['GET'])
def get_health_history():
    """
    Get network health history.

    Query params:
        hours: Hours of history (default: 24)

    Returns:
        Array of network health snapshots
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Analytics not available'}), 503

    try:
        hours = request.args.get('hours', 24, type=int)
        store = get_analytics_store()
        history = store.get_network_health_history(hours)

        return jsonify({
            'period_hours': hours,
            'snapshots': [
                {
                    'timestamp': m.timestamp,
                    'online_nodes': m.online_nodes,
                    'offline_nodes': m.offline_nodes,
                    'avg_rssi_dbm': m.avg_rssi_dbm,
                    'avg_snr_db': m.avg_snr_db,
                    'avg_link_quality_pct': m.avg_link_quality_pct,
                    'packet_success_rate': m.packet_success_rate,
                }
                for m in history
            ],
        })

    except Exception as e:
        logger.error(f"Health history error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Webhook Management Endpoints
# ============================================================================

@bp.route('/webhooks', methods=['GET'])
def list_webhooks():
    """
    List all configured webhook endpoints.

    Returns:
        Array of webhook configurations
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Webhooks not available'}), 503

    try:
        manager = get_webhook_manager()
        return jsonify({
            'endpoints': manager.list_endpoints(),
            'event_types': [e.value for e in EventType],
        })

    except Exception as e:
        logger.error(f"List webhooks error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/webhooks', methods=['POST'])
def add_webhook():
    """
    Add a new webhook endpoint.

    Body (JSON):
        url: Webhook URL (required)
        name: Display name (required)
        events: Array of event types to subscribe to (optional, empty = all)
        secret: HMAC secret for signing (optional)
        timeout_seconds: Request timeout (default: 10)
        retry_count: Number of retries (default: 3)
        headers: Additional headers dict (optional)

    Returns:
        Success status and endpoint config
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Webhooks not available'}), 503

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body required'}), 400

        if not data.get('url') or not data.get('name'):
            return jsonify({'error': 'url and name are required'}), 400

        endpoint = WebhookEndpoint(
            url=data['url'],
            name=data['name'],
            enabled=data.get('enabled', True),
            events=data.get('events', []),
            secret=data.get('secret'),
            timeout_seconds=data.get('timeout_seconds', 10),
            retry_count=data.get('retry_count', 3),
            headers=data.get('headers', {}),
        )

        manager = get_webhook_manager()
        success = manager.add_endpoint(endpoint)

        if success:
            return jsonify({
                'success': True,
                'endpoint': endpoint.to_dict(),
            })
        else:
            return jsonify({
                'error': 'Endpoint already exists with this URL'
            }), 409

    except Exception as e:
        logger.error(f"Add webhook error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/webhooks', methods=['DELETE'])
def remove_webhook():
    """
    Remove a webhook endpoint.

    Query params:
        url: Webhook URL to remove

    Returns:
        Success status
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Webhooks not available'}), 503

    try:
        url = request.args.get('url')
        if not url:
            return jsonify({'error': 'url parameter required'}), 400

        manager = get_webhook_manager()
        success = manager.remove_endpoint(url)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Endpoint not found'}), 404

    except Exception as e:
        logger.error(f"Remove webhook error: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/webhooks/test', methods=['POST'])
def test_webhook():
    """
    Send a test event to a webhook URL.

    Body (JSON):
        url: Webhook URL to test

    Returns:
        Test result
    """
    if not ANALYTICS_AVAILABLE:
        return jsonify({'error': 'Webhooks not available'}), 503

    try:
        data = request.get_json()
        if not data or not data.get('url'):
            return jsonify({'error': 'url required in body'}), 400

        from utils.webhooks import WebhookEvent, EventType
        from datetime import datetime

        # Create test event
        event = WebhookEvent(
            event_type=EventType.CUSTOM.value,
            timestamp=datetime.now().isoformat(),
            data={'message': 'Test webhook from MeshForge', 'test': True},
        )

        # Create temp endpoint for test
        endpoint = WebhookEndpoint(
            url=data['url'],
            name='test',
            timeout_seconds=10,
            retry_count=1,
        )

        manager = get_webhook_manager()
        success = manager._deliver_to_endpoint(endpoint, event)

        return jsonify({
            'success': success,
            'message': 'Test event delivered' if success else 'Delivery failed',
        })

    except Exception as e:
        logger.error(f"Test webhook error: {e}")
        return jsonify({'error': str(e)}), 500
