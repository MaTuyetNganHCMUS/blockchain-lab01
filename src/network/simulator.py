"""Deterministic Discrete-Event Network Simulator for TV3.

Implements the network layer conforming to NetworkProtocol and Section 6:
- Discrete-event simulation driven by heapq priority queue.
- Absolute determinism using independent random.Random(seed) and monotonic sequence IDs.
- Partial synchrony model:
  * Pre-GST (t < T_GST): Stochastic packet loss, latency jitter, duplication, reordering.
  * Post-GST (t >= T_GST): Guaranteed bounded delivery within Delta.
- Outbound rate limiting and peer blocking integration.
- Deterministic event logging (SEND, DROP, DELAY, BLOCK, UNBLOCK).
- Timer / Timeout callback scheduling for consensus rounds.
"""

from __future__ import annotations

import heapq
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..common.interfaces import (
    EVENT_DELAY,
    EVENT_DROP,
    EVENT_SEND,
    MSG_HEADER,
    NetPacket,
    NetworkProtocol,
)
from .rate_limiter import RateLimiter, RateLimiterConfig

if TYPE_CHECKING:
    from ..common.interfaces import LoggerProtocol


@dataclass
class NetworkConfig:
    """Network simulation configuration parameters."""

    gst: float = 100.0  # Global Stabilization Time
    delta: float = 1.0  # Upper bound on message latency after GST
    min_delay: float = 0.05  # Base minimum latency
    max_delay_pre_gst: float = 5.0  # Maximum latency before GST
    drop_rate_pre_gst: float = 0.0  # Packet drop probability before GST
    dup_rate_pre_gst: float = 0.0  # Packet duplication probability before GST
    seed: int = 42  # Random seed for deterministic simulation


class NetworkSimulator(NetworkProtocol):
    """Discrete-Event Network Simulator conforming to NetworkProtocol."""

    def __init__(
        self,
        seed: int = 42,
        config: NetworkConfig | None = None,
        nodes: Sequence[str] | None = None,
        rate_limiter: RateLimiter | None = None,
        logger: LoggerProtocol | None = None,
    ) -> None:
        self.config = config if config is not None else NetworkConfig(seed=seed)
        self.seed = self.config.seed
        self.rng = random.Random(self.seed)

        self.logger = logger
        self.rate_limiter = (
            rate_limiter
            if rate_limiter is not None
            else RateLimiter(
                RateLimiterConfig(
                    max_requests_per_window=30,
                    window_seconds=1.0,
                    block_duration=5.0,
                    max_outbound_per_window=100,
                ),
                logger=self.logger,
            )
        )

        # Simulation clock
        self.current_time: float = 0.0

        # Monotonic event sequence counter for deterministic FIFO tie-breaking
        self._seq: int = 0

        # Priority queue storing tuples: (delivery_time, seq_id, event_type, data)
        self._queue: list[tuple[float, int, str, Any]] = []

        # Registered nodes: node_id -> node_handler (object or callback)
        self._nodes: dict[str, Any] = {}
        if nodes:
            for n in nodes:
                self._nodes[n] = None

        # Statistics
        self.total_sent: int = 0
        self.total_delivered: int = 0
        self.total_dropped: int = 0
        self.total_duplicates: int = 0

    # --------------------------------------------------------------------------
    # NetworkProtocol Implementation
    # --------------------------------------------------------------------------

    def send(self, sender: str, receiver: str, raw_bytes: bytes) -> None:
        """Enqueue point-to-point packet into simulated network queue."""
        packet = NetPacket(
            msg_type="RAW",
            sender=sender,
            receiver=receiver,
            payload=raw_bytes,
        )
        self._enqueue_packet(packet)

    def broadcast_header(self, sender: str, header_bytes: bytes) -> None:
        """Broadcast block header to all connected peers in network."""
        for peer_id in self._nodes:
            if peer_id == sender:
                continue
            packet = NetPacket(
                msg_type=MSG_HEADER,
                sender=sender,
                receiver=peer_id,
                payload=header_bytes,
            )
            self._enqueue_packet(packet)

    def schedule_timeout(self, node_id: str, delay: float | int, callback_tag: str) -> None:
        """Register discrete-event timer callback on the simulation timeline."""
        trigger_time = self.current_time + float(delay)
        self._seq += 1
        heapq.heappush(
            self._queue,
            (trigger_time, self._seq, "TIMEOUT", (node_id, callback_tag)),
        )

    # --------------------------------------------------------------------------
    # Extended Network Management
    # --------------------------------------------------------------------------

    def register_node(self, node_id: str, handler: Any) -> None:
        """Register a node instance or packet handler with the simulator."""
        self._nodes[node_id] = handler

    def broadcast(self, sender: str, raw_bytes: bytes, exclude_sender: bool = True) -> None:
        """Broadcast raw bytes to all registered nodes."""
        for peer_id in self._nodes:
            if exclude_sender and peer_id == sender:
                continue
            self.send(sender, peer_id, raw_bytes)

    def _enqueue_packet(self, packet: NetPacket) -> None:
        """Apply network simulation model (partial synchrony) and push to queue."""
        self.total_sent += 1

        # Check sender outbound rate limit
        if self.rate_limiter is not None:
            if not self.rate_limiter.check_outbound(packet.sender, self.current_time):
                # Outbound rate throttled
                self.total_dropped += 1
                if self.logger is not None:
                    self.logger.log(
                        sim_time=self.current_time,
                        node_id=packet.sender,
                        event_type=EVENT_DROP,
                        height=0,
                        round=0,
                        detail=f"Outbound rate exceeded for {packet.sender}, packet dropped",
                    )
                return

        # Partial synchrony behavior
        is_pre_gst = self.current_time < self.config.gst

        if is_pre_gst:
            # Check packet drop probability
            if self.config.drop_rate_pre_gst > 0.0 and self.rng.random() < self.config.drop_rate_pre_gst:
                self.total_dropped += 1
                if self.logger is not None:
                    self.logger.log(
                        sim_time=self.current_time,
                        node_id=packet.sender,
                        event_type=EVENT_DROP,
                        height=0,
                        round=0,
                        detail=f"Pre-GST loss: packet from {packet.sender} to {packet.receiver} dropped",
                    )
                return

            # Latency jitter before GST
            delay = self.rng.uniform(self.config.min_delay, self.config.max_delay_pre_gst)

            # Check duplicate probability
            if self.config.dup_rate_pre_gst > 0.0 and self.rng.random() < self.config.dup_rate_pre_gst:
                self.total_duplicates += 1
                dup_delay = delay + self.rng.uniform(0.01, 0.5)
                self._schedule_packet_delivery(packet, dup_delay)
        else:
            # Post-GST: Guaranteed delivery within Delta
            max_delay = max(self.config.min_delay, self.config.delta)
            delay = self.rng.uniform(self.config.min_delay, max_delay)

        self._schedule_packet_delivery(packet, delay)

    def _schedule_packet_delivery(self, packet: NetPacket, delay: float) -> None:
        """Schedule a packet delivery event at current_time + delay."""
        delivery_time = self.current_time + delay
        self._seq += 1

        if self.logger is not None:
            self.logger.log(
                sim_time=self.current_time,
                node_id=packet.sender,
                event_type=EVENT_SEND,
                height=0,
                round=0,
                detail=f"Send {packet.msg_type} to {packet.receiver} (eta={delivery_time:.3f})",
            )
            if delay > self.config.min_delay:
                self.logger.log(
                    sim_time=self.current_time,
                    node_id=packet.receiver,
                    event_type=EVENT_DELAY,
                    height=0,
                    round=0,
                    detail=f"Latency jitter {delay:.3f}s from {packet.sender}",
                )

        heapq.heappush(
            self._queue,
            (delivery_time, self._seq, "PACKET", packet),
        )

    # --------------------------------------------------------------------------
    # Discrete-Event Loop & Execution
    # --------------------------------------------------------------------------

    def step(self) -> bool:
        """Execute the next earliest event from the discrete-event queue.

        Returns:
            True if an event was processed; False if the queue is empty.
        """
        if not self._queue:
            return False

        delivery_time, _, event_type, data = heapq.heappop(self._queue)
        # Advance simulation clock monotonically
        self.current_time = delivery_time

        # Check for any expired blocks in rate limiter
        if self.rate_limiter is not None:
            self.rate_limiter.unblock_expired(self.current_time)

        if event_type == "PACKET":
            packet: NetPacket = data
            self._dispatch_packet(packet)
        elif event_type == "TIMEOUT":
            node_id, callback_tag = data
            self._dispatch_timeout(node_id, callback_tag)

        return True

    def _dispatch_packet(self, packet: NetPacket) -> None:
        """Deliver packet to target node or handler."""
        # Verify receiver inbound rate limiter
        if self.rate_limiter is not None:
            if not self.rate_limiter.check_inbound(
                packet.sender,
                self.current_time,
                receiver_id=packet.receiver,
            ):
                self.total_dropped += 1
                if self.logger is not None:
                    self.logger.log(
                        sim_time=self.current_time,
                        node_id=packet.receiver,
                        event_type=EVENT_DROP,
                        height=0,
                        round=0,
                        detail=f"Dropped packet from blocked peer {packet.sender}",
                    )
                return

        self.total_delivered += 1
        receiver_node = self._nodes.get(packet.receiver)
        if receiver_node is None:
            return

        # Duck-typing dispatch to receiver
        if hasattr(receiver_node, "handle_packet"):
            receiver_node.handle_packet(self.current_time, packet.payload)
        elif hasattr(receiver_node, "on_packet"):
            receiver_node.on_packet(self.current_time, packet.payload)
        elif callable(receiver_node):
            receiver_node(self.current_time, packet)

    def _dispatch_timeout(self, node_id: str, callback_tag: str) -> None:
        """Deliver timeout event to destination node."""
        target_node = self._nodes.get(node_id)
        if target_node is None:
            return

        # Attempt to parse tag if formatted as "H:R:STEP"
        parts = callback_tag.split(":")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            height, round_num, step = int(parts[0]), int(parts[1]), parts[2]
            if hasattr(target_node, "handle_timeout"):
                target_node.handle_timeout(self.current_time, height, round_num, step)
                return
            elif hasattr(target_node, "on_timeout"):
                target_node.on_timeout(self.current_time, height, round_num, step)
                return

        # Fallback dispatch
        if hasattr(target_node, "handle_timeout_tag"):
            target_node.handle_timeout_tag(self.current_time, callback_tag)
        elif callable(target_node):
            target_node(self.current_time, callback_tag)

    def run_until(self, max_time: float | int) -> int:
        """Run simulation events until simulation clock reaches max_time or queue empty.

        Returns:
            Number of events processed.
        """
        target_time = float(max_time)
        events_processed = 0
        while self._queue:
            next_time = self._queue[0][0]
            if next_time > target_time:
                break
            self.step()
            events_processed += 1

        if self.current_time < target_time:
            self.current_time = target_time
            if self.rate_limiter is not None:
                self.rate_limiter.unblock_expired(self.current_time)

        return events_processed

    def run_steps(self, max_steps: int) -> int:
        """Execute up to max_steps events.

        Returns:
            Number of events processed.
        """
        count = 0
        while count < max_steps and self.step():
            count += 1
        return count

    @property
    def queue_size(self) -> int:
        """Return the count of pending scheduled events."""
        return len(self._queue)

    def peek_next_time(self) -> float | None:
        """Return timestamp of next scheduled event without popping."""
        if not self._queue:
            return None
        return self._queue[0][0]

    def clear(self) -> None:
        """Clear all pending events and reset clock."""
        self._queue.clear()
        self.current_time = 0.0
        self._seq = 0
        if self.rate_limiter is not None:
            self.rate_limiter.reset()


__all__ = ["NetworkConfig", "NetworkSimulator"]
