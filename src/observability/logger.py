"""Byte-stable structured event logging for deterministic simulator runs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.common.interfaces import (
    EVENT_BLOCK,
    EVENT_DELAY,
    EVENT_DROP,
    EVENT_FINALIZE,
    EVENT_LOCK,
    EVENT_PRECOMMIT,
    EVENT_PREVOTE,
    EVENT_PROPOSE,
    EVENT_SEND,
    EVENT_UNBLOCK,
)

EVENT_TYPES = frozenset(
    {
        EVENT_SEND,
        EVENT_DROP,
        EVENT_DELAY,
        EVENT_BLOCK,
        EVENT_UNBLOCK,
        EVENT_PROPOSE,
        EVENT_PREVOTE,
        EVENT_PRECOMMIT,
        EVENT_LOCK,
        EVENT_FINALIZE,
    }
)


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    sim_time: Decimal
    node_id: str
    event_type: str
    height: int
    round: int
    detail: str


class DeterministicLogger:
    """Collect events in call order and serialize them identically on every run."""

    def __init__(self, path: str | Path | None = None, *, strict_event_types: bool = True) -> None:
        self.path = Path(path) if path is not None else None
        self.strict_event_types = strict_event_types
        self._records: list[EventRecord] = []

    @property
    def records(self) -> tuple[EventRecord, ...]:
        return tuple(self._records)

    def log(
        self,
        sim_time: float,
        node_id: str,
        event_type: str,
        height: int,
        round: int,
        detail: Any,
    ) -> None:
        if not node_id:
            raise ValueError("node_id must not be empty")
        if self.strict_event_types and event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {event_type}")
        if height < 0 or round < 0:
            raise ValueError("height and round must be non-negative")
        normalized_time = _normalize_time(sim_time)
        self._records.append(
            EventRecord(
                sequence=len(self._records),
                sim_time=normalized_time,
                node_id=node_id,
                event_type=event_type,
                height=height,
                round=round,
                detail=_normalize_detail(detail),
            )
        )

    def formatted_lines(self) -> list[str]:
        return [
            f"[{record.sim_time:08.2f}] node={record.node_id} event={record.event_type} "
            f"H={record.height} R={record.round} detail={record.detail}"
            for record in self._records
        ]

    def to_bytes(self) -> bytes:
        """Use UTF-8 and LF, including one final LF for stable file hashing."""

        lines = self.formatted_lines()
        return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def write(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise ValueError("a log path is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.to_bytes())
        return destination

    def clear(self) -> None:
        self._records.clear()


def _normalize_time(value: float) -> Decimal:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("sim_time must be finite")
    try:
        normalized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("sim_time must be numeric") from error
    if normalized < 0:
        raise ValueError("sim_time must be non-negative")
    return normalized


def _normalize_detail(detail: Any) -> str:
    if is_dataclass(detail) and not isinstance(detail, type):
        detail = asdict(detail)
    if isinstance(detail, str):
        return detail.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
    return json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = ["EVENT_TYPES", "DeterministicLogger", "EventRecord"]
