"""MeshForge Remote Management Agent.

Based on NGINX Agent architecture - provides remote management capabilities
for MeshForge NOC instances.

This module provides:
- AgentDaemon: Main agent process with metrics collection and command handling
- AgentProtocol: Communication protocol for management plane
- Command handlers for configuration, health, and service control

Example Usage:
    from agent import AgentDaemon, AgentConfig

    # Create and start agent
    config = AgentConfig(
        instance_id="meshforge-001",
        management_host="mgmt.example.com",
        management_port=9443,
    )
    agent = AgentDaemon(config)
    agent.start()

    # Agent runs in background, collecting metrics and handling commands

See also:
- src/utils/config_api.py - Configuration API (used by agent)
- src/utils/shared_health_state.py - Shared health state (integrated)
- src/utils/metrics_export.py - Prometheus metrics (integrated)

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

from agent.agent import AgentDaemon, AgentConfig, AgentState
from agent.protocol import (
    AgentProtocol,
    Message,
    MessageType,
    AuthToken,
)
from agent.commands import (
    CommandHandler,
    CommandResult,
    CommandRegistry,
)

__all__ = [
    # Core agent
    "AgentDaemon",
    "AgentConfig",
    "AgentState",
    # Protocol
    "AgentProtocol",
    "Message",
    "MessageType",
    "AuthToken",
    # Commands
    "CommandHandler",
    "CommandResult",
    "CommandRegistry",
]
