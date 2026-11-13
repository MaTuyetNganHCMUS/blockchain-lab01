"""Header-before-body gossip protocol implementation for TV3.

Implements the two-phase block propagation rule required by Section 6:
- Headers are broadcast before bodies.
- A body may be sent only after the receiver accepts the matching header.
- A receiver receiving an unrequested or unaccepted block body rejects it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..common.interfaces import (
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    BlockBody,
    BlockHeader,
    NetPacket,
)

if TYPE_CHECKING:
    from ..common.interfaces import CryptoProtocol, LoggerProtocol, NetworkProtocol


class GossipManager:
    """Manages two-phase Header-before-Body gossip protocol for a node."""

    def __init__(
        self,
        node_id: str,
        network: NetworkProtocol | None = None,
        crypto: CryptoProtocol | None = None,
        logger: LoggerProtocol | None = None,
        header_validator: Callable[[BlockHeader], bool] | None = None,
    ) -> None:
        self.node_id = node_id
        self.network = network
        self.crypto = crypto
        self.logger = logger
        self.header_validator = header_validator

        # Local storage of accepted headers: set of header_hash
        self.accepted_headers: set[bytes] = set()

        # Cached headers: header_hash -> BlockHeader
        self.headers_by_hash: dict[bytes, BlockHeader] = {}

        # Cached bodies locally held or proposed: header_hash -> BlockBody
        self.stored_bodies: dict[bytes, BlockBody] = {}

        # Accepted bodies received from peers: header_hash -> BlockBody
        self.accepted_bodies: dict[bytes, BlockBody] = {}

        # Pending body requests: header_hash -> peer_id who sent the header
        self.pending_body_requests: dict[bytes, str] = {}

    def broadcast_header(
        self,
        header: BlockHeader,
        body: BlockBody | None = None,
    ) -> bytes:
        """Broadcast block header and locally stash matching body if provided.

        Returns:
            The header hash commitment.
        """
        # Determine header hash
        header_hash = self._compute_header_hash(header)
        self.accepted_headers.add(header_hash)
        self.headers_by_hash[header_hash] = header

        if body is not None:
            self.stored_bodies[header_hash] = body

        if self.network is not None:
            # Serialize header
            payload = self._serialize_header(header)
            self.network.broadcast_header(self.node_id, payload)

        return header_hash

    def handle_header_packet(
        self,
        sender: str,
        header: BlockHeader,
    ) -> bool:
        """Process an incoming BlockHeader from `sender`.

        Validates header. If accepted, records it and requests matching BlockBody.
        Returns:
            True if header was accepted and body requested; False if rejected.
        """
        header_hash = self._compute_header_hash(header)

        # Validate header if validator function configured
        if self.header_validator is not None:
            if not self.header_validator(header):
                return False

        self.accepted_headers.add(header_hash)
        self.headers_by_hash[header_hash] = header
        self.pending_body_requests[header_hash] = sender

        # Request body from sender
        if self.network is not None:
            request_packet = NetPacket(
                msg_type=MSG_BODY_REQUEST,
                sender=self.node_id,
                receiver=sender,
                payload=header_hash,
            )
            self.network.send(
                self.node_id,
                sender,
                self._serialize_packet(request_packet),
            )

        return True

    def handle_body_request(
        self,
        requester: str,
        header_hash: bytes,
    ) -> bool:
        """Process a request from `requester` for the body of `header_hash`.

        Returns:
            True if body was found and sent; False otherwise.
        """
        body = self.stored_bodies.get(header_hash)
        if body is None:
            return False

        if self.network is not None:
            body_packet = NetPacket(
                msg_type=MSG_BODY,
                sender=self.node_id,
                receiver=requester,
                payload=self._serialize_body(body),
            )
            self.network.send(
                self.node_id,
                requester,
                self._serialize_packet(body_packet),
            )
        return True

    def handle_body_packet(
        self,
        sender: str,
        body: BlockBody,
    ) -> bool:
        """Process an incoming BlockBody from `sender`.

        Enforces the protocol rule: A body may only be accepted if its
        matching header was already accepted.
        Returns:
            True if body is accepted; False if rejected (header not accepted).
        """
        if body.header_hash not in self.accepted_headers:
            # Reject: Header was never received or accepted!
            return False

        self.accepted_bodies[body.header_hash] = body
        self.pending_body_requests.pop(body.header_hash, None)
        return True

    def has_full_block(self, header_hash: bytes) -> bool:
        """Check whether both header and body are accepted for this block."""
        return header_hash in self.accepted_headers and (
            header_hash in self.accepted_bodies or header_hash in self.stored_bodies
        )

    def get_block(self, header_hash: bytes) -> tuple[BlockHeader, BlockBody] | None:
        """Retrieve full block tuple (header, body) if available."""
        header = self.headers_by_hash.get(header_hash)
        body = self.accepted_bodies.get(header_hash) or self.stored_bodies.get(header_hash)
        if header is not None and body is not None:
            return header, body
        return None

    # Internal serialization helpers
    def _compute_header_hash(self, header: BlockHeader) -> bytes:
        if self.crypto is not None:
            return self.crypto.hash_bytes(self.crypto.encode_canonical(header))
        import hashlib

        raw = f"{header.height}:{header.round}:{header.parent_hash.hex()}:{header.state_hash.hex()}"
        return hashlib.sha256(raw.encode("utf-8")).digest()

    def _serialize_header(self, header: BlockHeader) -> bytes:
        if self.crypto is not None:
            return self.crypto.encode_canonical(header)
        import pickle

        return pickle.dumps(header)

    def _serialize_body(self, body: BlockBody) -> bytes:
        if self.crypto is not None:
            return self.crypto.encode_canonical(body)
        import pickle

        return pickle.dumps(body)

    def _serialize_packet(self, packet: NetPacket) -> bytes:
        if self.crypto is not None:
            return self.crypto.encode_canonical(packet)
        import pickle

        return pickle.dumps(packet)


__all__ = ["GossipManager"]
