"""Tests for MeshForge Remote Management Agent (src/agent/).

Tests:
- Protocol message handling
- Command registration and execution
- Agent daemon lifecycle
- Authentication tokens

Author: Claude Code (Opus 4.5)
Session: claude/config-api-restful-ETusS
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.protocol import (
    AgentProtocol,
    Message,
    MessageType,
    AuthToken,
    ConnectionState,
)
from agent.commands import (
    CommandHandler,
    CommandResult,
    CommandStatus,
    CommandContext,
    CommandRegistry,
    command_handler,
    create_default_registry,
)
from agent.agent import AgentDaemon, AgentConfig, AgentState


# =============================================================================
# AuthToken Tests
# =============================================================================


class TestAuthToken:
    """Tests for authentication tokens."""

    def test_generate_token(self):
        """Test token generation."""
        token = AuthToken.generate(scopes=["config:read", "config:write"])
        assert token.token_id
        assert token.secret
        assert len(token.secret) == 64  # 32 bytes hex
        assert "config:read" in token.scopes

    def test_token_is_valid(self):
        """Test token validity check."""
        token = AuthToken.generate(ttl_hours=1)
        assert token.is_valid()

    def test_token_expired(self):
        """Test expired token."""
        token = AuthToken.generate(ttl_hours=0)
        token.expires_at = time.time() - 1  # Expired 1 second ago
        assert not token.is_valid()

    def test_token_sign_verify(self):
        """Test message signing and verification."""
        token = AuthToken.generate()
        data = b"test message data"

        signature = token.sign(data)
        assert signature
        assert token.verify(data, signature)

    def test_token_verify_wrong_signature(self):
        """Test verification with wrong signature."""
        token = AuthToken.generate()
        data = b"test message data"
        assert not token.verify(data, "wrong_signature")

    def test_token_to_dict_excludes_secret(self):
        """Test that to_dict excludes secret."""
        token = AuthToken.generate()
        data = token.to_dict()
        assert "token_id" in data
        assert "secret" not in data


# =============================================================================
# Message Tests
# =============================================================================


class TestMessage:
    """Tests for protocol messages."""

    def test_message_creation(self):
        """Test message creation."""
        msg = Message(
            msg_type=MessageType.COMMAND,
            payload={"command": "test"}
        )
        assert msg.msg_type == MessageType.COMMAND
        assert msg.payload == {"command": "test"}
        assert msg.msg_id
        assert msg.timestamp > 0

    def test_message_serialization(self):
        """Test message to bytes serialization."""
        msg = Message(
            msg_type=MessageType.HEARTBEAT,
            payload={"timestamp": 12345}
        )
        data = msg.to_bytes()
        assert isinstance(data, bytes)
        assert b"HEARTBEAT" in data

    def test_message_deserialization(self):
        """Test message from bytes deserialization."""
        original = Message(
            msg_type=MessageType.COMMAND,
            payload={"command": "test", "args": {"key": "value"}}
        )
        data = original.to_bytes()
        restored = Message.from_bytes(data)

        assert restored.msg_type == original.msg_type
        assert restored.payload == original.payload
        assert restored.msg_id == original.msg_id

    def test_message_create_response(self):
        """Test creating response to message."""
        request = Message(
            msg_type=MessageType.COMMAND,
            payload={"command": "test"}
        )
        response = request.create_response(
            MessageType.COMMAND_RESULT,
            {"result": "success"}
        )

        assert response.msg_type == MessageType.COMMAND_RESULT
        assert response.reply_to == request.msg_id

    def test_message_sign(self):
        """Test message signing."""
        token = AuthToken.generate()
        msg = Message(
            msg_type=MessageType.HELLO,
            payload={}
        )
        msg.sign(token)
        assert msg.signature

    def test_message_verify(self):
        """Test message signature verification."""
        token = AuthToken.generate()
        msg = Message(
            msg_type=MessageType.HELLO,
            payload={}
        )
        msg.sign(token)
        assert msg.verify(token)


# =============================================================================
# ConnectionState Tests
# =============================================================================


class TestConnectionState:
    """Tests for connection state tracking."""

    def test_initial_state(self):
        """Test initial connection state."""
        state = ConnectionState()
        assert not state.connected
        assert not state.authenticated
        assert state.bytes_sent == 0

    def test_state_to_dict(self):
        """Test state serialization."""
        state = ConnectionState(
            connected=True,
            authenticated=True,
            bytes_sent=1000
        )
        data = state.to_dict()
        assert data["connected"] is True
        assert data["bytes_sent"] == 1000


# =============================================================================
# Command Handler Tests
# =============================================================================


class TestCommandHandler:
    """Tests for command handlers."""

    def test_command_handler_decorator(self):
        """Test command_handler decorator."""
        @command_handler("test.command", required_args=["arg1"])
        def test_cmd(args, context):
            return CommandResult.success({"received": args["arg1"]})

        assert test_cmd._command_name == "test.command"
        assert "arg1" in test_cmd._required_args

    def test_command_result_success(self):
        """Test successful command result."""
        result = CommandResult.success({"key": "value"})
        assert result.status == CommandStatus.SUCCESS
        assert result.data == {"key": "value"}
        assert result.error_msg is None

    def test_command_result_error(self):
        """Test error command result."""
        result = CommandResult.error("Something went wrong")
        assert result.status == CommandStatus.ERROR
        assert result.error_msg == "Something went wrong"

    def test_command_result_to_dict(self):
        """Test command result serialization."""
        result = CommandResult(
            status=CommandStatus.SUCCESS,
            data={"key": "value"},
            execution_time_ms=10.5
        )
        data = result.to_dict()
        assert data["status"] == "success"
        assert data["data"] == {"key": "value"}
        assert data["execution_time_ms"] == 10.5


# =============================================================================
# CommandContext Tests
# =============================================================================


class TestCommandContext:
    """Tests for command context."""

    def test_context_has_scope(self):
        """Test scope checking."""
        context = CommandContext(
            instance_id="test",
            scopes={"config:read", "config:write"}
        )
        assert context.has_scope("config:read")
        assert not context.has_scope("admin")

    def test_context_wildcard_scope(self):
        """Test wildcard scope."""
        context = CommandContext(
            instance_id="test",
            scopes={"*"}
        )
        assert context.has_scope("anything")
        assert context.has_scope("admin")


# =============================================================================
# CommandRegistry Tests
# =============================================================================


class TestCommandRegistry:
    """Tests for command registry."""

    def test_register_handler(self):
        """Test registering a handler."""
        registry = CommandRegistry()

        @command_handler("test.cmd")
        def handler(args, context):
            return CommandResult.success()

        registry.register(handler)
        assert registry.get_handler("test.cmd") is not None

    def test_execute_command(self):
        """Test executing a command."""
        registry = CommandRegistry()

        @command_handler("test.echo", required_args=["message"])
        def echo_handler(args, context):
            return CommandResult.success({"echo": args["message"]})

        registry.register(echo_handler)

        context = CommandContext(instance_id="test", scopes={"*"})
        result = registry.execute("test.echo", {"message": "hello"}, context)

        assert result.status == CommandStatus.SUCCESS
        assert result.data["echo"] == "hello"

    def test_execute_unknown_command(self):
        """Test executing unknown command."""
        registry = CommandRegistry()
        context = CommandContext(instance_id="test", scopes={"*"})

        result = registry.execute("unknown.cmd", {}, context)
        assert result.status == CommandStatus.NOT_FOUND

    def test_execute_missing_args(self):
        """Test executing command with missing args."""
        registry = CommandRegistry()

        @command_handler("test.cmd", required_args=["required_arg"])
        def handler(args, context):
            return CommandResult.success()

        registry.register(handler)
        context = CommandContext(instance_id="test", scopes={"*"})

        result = registry.execute("test.cmd", {}, context)
        assert result.status == CommandStatus.INVALID_ARGS

    def test_execute_unauthorized(self):
        """Test executing command without required scope."""
        registry = CommandRegistry()

        @command_handler("test.admin", required_scopes=["admin"])
        def admin_handler(args, context):
            return CommandResult.success()

        registry.register(admin_handler)
        context = CommandContext(instance_id="test", scopes={"user"})

        result = registry.execute("test.admin", {}, context)
        assert result.status == CommandStatus.UNAUTHORIZED

    def test_list_commands(self):
        """Test listing registered commands."""
        registry = CommandRegistry()

        @command_handler("test.cmd1", description="First command")
        def cmd1(args, context):
            return CommandResult.success()

        @command_handler("test.cmd2", description="Second command")
        def cmd2(args, context):
            return CommandResult.success()

        registry.register(cmd1)
        registry.register(cmd2)

        commands = registry.list_commands()
        assert len(commands) == 2
        names = [c["name"] for c in commands]
        assert "test.cmd1" in names
        assert "test.cmd2" in names

    def test_unregister_handler(self):
        """Test unregistering a handler."""
        registry = CommandRegistry()

        @command_handler("test.cmd")
        def handler(args, context):
            return CommandResult.success()

        registry.register(handler)
        assert registry.get_handler("test.cmd") is not None

        registry.unregister("test.cmd")
        assert registry.get_handler("test.cmd") is None


# =============================================================================
# Built-in Command Handler Tests
# =============================================================================


class TestBuiltinHandlers:
    """Tests for built-in command handlers."""

    @pytest.fixture
    def context(self):
        """Create a test context."""
        return CommandContext(
            instance_id="test-instance",
            scopes={"*"}
        )

    def test_agent_ping(self, context):
        """Test agent.ping command."""
        result = CommandHandler.agent_ping({}, context)
        assert result.status == CommandStatus.SUCCESS
        assert result.data["pong"] is True
        assert result.data["instance_id"] == "test-instance"

    def test_agent_status(self, context):
        """Test agent.status command."""
        result = CommandHandler.agent_status({}, context)
        assert result.status == CommandStatus.SUCCESS
        assert result.data["instance_id"] == "test-instance"
        assert "components" in result.data

    def test_system_info(self, context):
        """Test system.info command."""
        result = CommandHandler.system_info({}, context)
        assert result.status == CommandStatus.SUCCESS
        assert "python_version" in result.data
        assert "platform" in result.data
        assert "hostname" in result.data


# =============================================================================
# Default Registry Tests
# =============================================================================


class TestDefaultRegistry:
    """Tests for default registry creation."""

    def test_create_default_registry(self):
        """Test default registry has all handlers."""
        registry = create_default_registry()
        commands = registry.list_commands()

        # Check for key commands
        names = [c["name"] for c in commands]
        assert "config.get" in names
        assert "config.set" in names
        assert "health.status" in names
        assert "metrics.get" in names
        assert "agent.ping" in names
        assert "system.info" in names


# =============================================================================
# AgentConfig Tests
# =============================================================================


class TestAgentConfig:
    """Tests for agent configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AgentConfig()
        assert config.instance_id  # Should be auto-generated
        assert config.standalone is True
        assert config.metrics_interval == 60.0

    def test_config_with_values(self):
        """Test configuration with explicit values."""
        config = AgentConfig(
            instance_id="test-001",
            management_host="mgmt.example.com",
            management_port=9443,
            standalone=False
        )
        assert config.instance_id == "test-001"
        assert config.management_host == "mgmt.example.com"
        assert config.standalone is False

    def test_config_to_file(self):
        """Test saving configuration to file."""
        config = AgentConfig(instance_id="test-001")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config.to_file(str(config_path))

            assert config_path.exists()
            with open(config_path) as f:
                data = json.load(f)
            assert data["instance_id"] == "test-001"

    def test_config_from_file(self):
        """Test loading configuration from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            with open(config_path, "w") as f:
                json.dump({
                    "instance_id": "loaded-001",
                    "management_host": "example.com"
                }, f)

            config = AgentConfig.from_file(str(config_path))
            assert config.instance_id == "loaded-001"
            assert config.management_host == "example.com"


# =============================================================================
# AgentDaemon Tests
# =============================================================================


class TestAgentDaemon:
    """Tests for agent daemon."""

    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return AgentConfig(
            instance_id="test-agent",
            standalone=True,
            data_dir=tempfile.mkdtemp()
        )

    def test_agent_creation(self, config):
        """Test agent creation."""
        agent = AgentDaemon(config)
        assert agent.state == AgentState.STOPPED
        assert not agent.is_running

    def test_agent_start_stop(self, config):
        """Test agent start and stop."""
        agent = AgentDaemon(config)

        # Start
        assert agent.start()
        assert agent.state == AgentState.RUNNING
        assert agent.is_running

        # Stop
        agent.stop()
        assert agent.state == AgentState.STOPPED
        assert not agent.is_running

    def test_agent_status(self, config):
        """Test agent status reporting."""
        agent = AgentDaemon(config)
        agent.start()

        try:
            status = agent.get_status()
            assert status["instance_id"] == "test-agent"
            assert status["state"] == "RUNNING"
            assert status["standalone"] is True
            assert "components" in status
        finally:
            agent.stop()

    def test_agent_execute_command(self, config):
        """Test executing command through agent."""
        agent = AgentDaemon(config)
        agent.start()

        try:
            result = agent.execute_command("agent.ping", {})
            assert result.status == CommandStatus.SUCCESS
            assert result.data["pong"] is True
        finally:
            agent.stop()

    def test_agent_state_callback(self, config):
        """Test agent state change callback."""
        states = []
        agent = AgentDaemon(config)
        agent.on_state_change(lambda s: states.append(s))

        agent.start()
        agent.stop()

        assert AgentState.STARTING in states
        assert AgentState.RUNNING in states
        assert AgentState.STOPPING in states
        assert AgentState.STOPPED in states

    def test_agent_pid_file(self, config):
        """Test PID file creation and removal."""
        agent = AgentDaemon(config)
        agent.start()

        try:
            assert Path(config.pid_file).exists()
            with open(config.pid_file) as f:
                pid = int(f.read())
            assert pid == os.getpid()
        finally:
            agent.stop()

        assert not Path(config.pid_file).exists()


# =============================================================================
# Integration Tests
# =============================================================================


class TestAgentIntegration:
    """Integration tests for agent components."""

    def test_full_command_flow(self):
        """Test complete command execution flow."""
        # Create config
        config = AgentConfig(
            instance_id="integration-test",
            standalone=True,
            data_dir=tempfile.mkdtemp()
        )

        # Create and start agent
        agent = AgentDaemon(config)
        assert agent.start()

        try:
            # Execute various commands
            ping = agent.execute_command("agent.ping", {})
            assert ping.status == CommandStatus.SUCCESS

            status = agent.execute_command("agent.status", {})
            assert status.status == CommandStatus.SUCCESS

            info = agent.execute_command("system.info", {})
            assert info.status == CommandStatus.SUCCESS

        finally:
            agent.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
