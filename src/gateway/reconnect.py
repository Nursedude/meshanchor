"""
Exponential backoff reconnection strategy for gateway services.

Provides reliable reconnection with exponential backoff and jitter
for both Meshtastic and RNS connections.
"""

import random
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, TypeVar, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class ReconnectConfig:
    """Configuration for reconnection behavior."""

    initial_delay: float = 1.0
    """Initial delay in seconds before first retry."""

    max_delay: float = 60.0
    """Maximum delay in seconds between retries."""

    multiplier: float = 2.0
    """Multiplier for exponential backoff."""

    jitter: float = 0.1
    """Random jitter factor (0.0-0.5) to prevent thundering herd."""

    max_attempts: int = 10
    """Maximum number of retry attempts before giving up."""


@dataclass
class ReconnectStrategy:
    """
    Implements exponential backoff with jitter for reconnection.

    Usage:
        strategy = ReconnectStrategy.for_meshtastic()

        while strategy.should_retry():
            try:
                connect()
                strategy.record_success()
                break
            except ConnectionError:
                strategy.record_failure()
                strategy.wait()
    """

    config: ReconnectConfig = field(default_factory=ReconnectConfig)
    attempts: int = field(default=0, init=False)

    def get_delay(self, attempt: Optional[int] = None) -> float:
        """
        Calculate delay for a given attempt number.

        Args:
            attempt: Attempt number (0-based). If None, uses current attempts.

        Returns:
            Delay in seconds with jitter applied.
        """
        if attempt is None:
            attempt = self.attempts

        # Calculate base delay with exponential backoff
        base_delay = self.config.initial_delay * (self.config.multiplier ** attempt)

        # Cap at max_delay
        base_delay = min(base_delay, self.config.max_delay)

        # Apply jitter
        jitter_range = base_delay * self.config.jitter
        jitter = random.uniform(-jitter_range, jitter_range)

        return max(0.0, base_delay + jitter)

    def wait(self, stop_event: Optional[threading.Event] = None) -> float:
        """
        Wait for the current backoff delay, interruptible via stop_event.

        Args:
            stop_event: If provided, wait is interrupted when event is set,
                        allowing fast shutdown instead of sleeping up to max_delay.

        Returns:
            The actual delay slept (may be shorter if interrupted).
        """
        delay = self.get_delay()
        logger.debug(f"Reconnect backoff: waiting {delay:.2f}s (attempt {self.attempts})")
        if stop_event:
            stop_event.wait(delay)  # Returns immediately if event is set
        else:
            time.sleep(delay)
        return delay

    def record_failure(self) -> None:
        """Record a connection failure, incrementing attempt counter."""
        self.attempts += 1
        logger.debug(f"Connection failure recorded, attempts: {self.attempts}")

    def record_success(self) -> None:
        """Record a successful connection, resetting attempt counter."""
        if self.attempts > 0:
            logger.info(f"Connection succeeded after {self.attempts} attempts")
        self.attempts = 0

    def reset(self) -> None:
        """Reset the attempt counter."""
        self.attempts = 0

    def should_retry(self) -> bool:
        """
        Check if another retry attempt should be made.

        Returns:
            True if attempts < max_attempts, False otherwise.
        """
        return self.attempts < self.config.max_attempts

    def execute_with_retry(
        self,
        func: Callable[[], T],
        on_failure: Optional[Callable[[Exception], None]] = None,
        stop_event: Optional[threading.Event] = None
    ) -> T:
        """
        Execute a function with automatic retry on failure.

        Args:
            func: The function to execute.
            on_failure: Optional callback for each failure.
            stop_event: If provided, wait is interruptible for clean shutdown.

        Returns:
            The result of the function.

        Raises:
            The last exception if all retries are exhausted.
        """
        last_exception = None

        while self.should_retry():
            if stop_event and stop_event.is_set():
                break
            try:
                result = func()
                self.record_success()
                return result
            except Exception as e:
                last_exception = e
                self.record_failure()

                if on_failure:
                    on_failure(e)

                if self.should_retry():
                    logger.warning(
                        f"Retry {self.attempts}/{self.config.max_attempts} "
                        f"after error: {e}"
                    )
                    self.wait(stop_event)

        # All retries exhausted or interrupted
        if last_exception is None:
            # Interrupted before first attempt (stop_event set early)
            raise ConnectionError("Connection interrupted before first attempt")
        logger.error(
            f"All {self.config.max_attempts} retry attempts exhausted"
        )
        raise last_exception

    @classmethod
    def for_meshtastic(cls) -> 'ReconnectStrategy':
        """
        Create a strategy optimized for Meshtastic connections.

        Meshtastic devices may need quick reconnection after
        brief disconnects, but should back off for longer issues.
        """
        config = ReconnectConfig(
            initial_delay=1.0,
            max_delay=30.0,
            multiplier=2.0,
            jitter=0.1,
            max_attempts=10
        )
        return cls(config=config)

    @classmethod
    def for_rns(cls) -> 'ReconnectStrategy':
        """
        Create a strategy optimized for RNS connections.

        RNS may need longer delays for transport initialization
        and network stabilization.
        """
        config = ReconnectConfig(
            initial_delay=2.0,
            max_delay=60.0,
            multiplier=2.0,
            jitter=0.15,
            max_attempts=15
        )
        return cls(config=config)
