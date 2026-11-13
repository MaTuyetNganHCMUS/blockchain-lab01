"""Outbound rate limiter and peer blocking mechanism for TV3.

Implements rate limiting and misbehavior detection:
- Enforces outbound sending rate limits per node.
- Monitors peer transmission rates over a sliding window.
- Temporarily blocks peers exceeding configured rate thresholds (EVENT_BLOCK).
- Automatically unblocks peers after a configurable duration (EVENT_UNBLOCK).
- Logs all block and unblock events deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..common.interfaces import EVENT_BLOCK, EVENT_UNBLOCK

if TYPE_CHECKING:
    from ..common.interfaces import LoggerProtocol


@dataclass
class RateLimiterConfig:
    """Configuration parameters for rate limiting and peer blocking.

    Attributes:
        max_requests_per_window: Maximum inbound messages allowed from a peer
            within sliding window.
        window_seconds: Duration of the inbound sliding observation window.
        block_duration: Duration for which a violating peer remains blocked.
        max_outbound_per_window: Maximum outbound messages a node may send
            within the outbound window.
        outbound_window_seconds: Duration of the outbound rate-limiting window.
    """

    max_requests_per_window: int = 10
    window_seconds: float = 1.0
    block_duration: float = 5.0
    max_outbound_per_window: int = 20
    outbound_window_seconds: float = 1.0


class RateLimiter:
    """Manages outbound rate limiting and peer blocking."""

    def __init__(
        self,
        config: RateLimiterConfig | None = None,
        logger: LoggerProtocol | None = None,
    ) -> None:
        self.config = config if config is not None else RateLimiterConfig()
        self.logger = logger

        # Inbound tracking: peer_id -> list of arrival timestamps within window
        self._peer_timestamps: dict[str, list[float]] = {}
        # Blocked peers: peer_id -> unblock_timestamp
        self._blocked_peers: dict[str, float] = {}

        # Outbound tracking: sender_id -> list of send timestamps
        self._outbound_timestamps: dict[str, list[float]] = {}

    def is_peer_blocked(self, peer_id: str, current_time: float, receiver_id: str = "") -> bool:
        """Check whether a peer is currently blocked at `current_time`.

        If the block duration has elapsed, the peer is automatically unblocked
        and an EVENT_UNBLOCK event is logged.
        """
        key = f"{receiver_id}:{peer_id}" if receiver_id else peer_id
        unblock_time = self._blocked_peers.get(key)
        if unblock_time is None:
            return False

        if current_time >= unblock_time:
            # Block expired -> unblock
            del self._blocked_peers[key]
            # Reset history
            self._peer_timestamps.pop(key, None)
            if self.logger is not None:
                target_desc = f" by {receiver_id}" if receiver_id else ""
                self.logger.log(
                    sim_time=current_time,
                    node_id=peer_id,
                    event_type=EVENT_UNBLOCK,
                    height=0,
                    round=0,
                    detail=f"Peer {peer_id} unblocked{target_desc} after penalty duration",
                )
            return False

        return True

    def check_inbound(
        self,
        peer_id: str,
        current_time: float,
        height: int = 0,
        round: int = 0,
        receiver_id: str = "",
    ) -> bool:
        """Process an inbound message from `peer_id` at `current_time`.

        Returns:
            True if the message is accepted; False if the message is rejected
            (peer was already blocked, or exceeded rate threshold and is now blocked).
        """
        key = f"{receiver_id}:{peer_id}" if receiver_id else peer_id

        # If currently blocked (and penalty not expired), reject immediately
        if self.is_peer_blocked(peer_id, current_time, receiver_id=receiver_id):
            return False

        # Prune timestamps outside sliding window
        window_start = current_time - self.config.window_seconds
        timestamps = [t for t in self._peer_timestamps.get(key, []) if t >= window_start]
        timestamps.append(current_time)
        self._peer_timestamps[key] = timestamps

        # Check threshold
        if len(timestamps) > self.config.max_requests_per_window:
            # Exceeded rate limit -> block peer
            unblock_at = current_time + self.config.block_duration
            self._blocked_peers[key] = unblock_at
            if self.logger is not None:
                target_desc = f" at {receiver_id}" if receiver_id else ""
                self.logger.log(
                    sim_time=current_time,
                    node_id=peer_id,
                    event_type=EVENT_BLOCK,
                    height=height,
                    round=round,
                    detail=(
                        f"Peer {peer_id} rate exceeded{target_desc} ({len(timestamps)} msgs in "
                        f"{self.config.window_seconds}s), blocked until t={unblock_at:.2f}"
                    ),
                )
            return False

        return True

    def check_outbound(
        self,
        sender_id: str,
        current_time: float,
    ) -> bool:
        """Verify whether `sender_id` is within outbound rate quota.

        Returns:
            True if sender may send immediately; False if sender exceeds outbound rate.
        """
        window_start = current_time - self.config.outbound_window_seconds
        timestamps = [t for t in self._outbound_timestamps.get(sender_id, []) if t >= window_start]

        if len(timestamps) >= self.config.max_outbound_per_window:
            return False

        timestamps.append(current_time)
        self._outbound_timestamps[sender_id] = timestamps
        return True

    def unblock_expired(self, current_time: float) -> list[str]:
        """Unblock all peers whose block expiration timestamp <= current_time.

        Returns:
            List of peer IDs that were unblocked.
        """
        expired = [k for k, unblock_t in self._blocked_peers.items() if current_time >= unblock_t]
        unblocked = []
        for key in expired:
            parts = key.split(":", 1)
            if len(parts) == 2:
                rec_id, peer_id = parts
                if not self.is_peer_blocked(peer_id, current_time, receiver_id=rec_id):
                    unblocked.append(key)
            else:
                if not self.is_peer_blocked(key, current_time):
                    unblocked.append(key)
        return unblocked

    def get_blocked_peers(self) -> dict[str, float]:
        """Return a copy of currently blocked peers and their unblock times."""
        return dict(self._blocked_peers)

    def reset(self) -> None:
        """Reset all rate limiter state."""
        self._peer_timestamps.clear()
        self._blocked_peers.clear()
        self._outbound_timestamps.clear()


__all__ = ["RateLimiterConfig", "RateLimiter"]
