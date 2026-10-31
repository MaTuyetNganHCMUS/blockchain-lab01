"""Ledger extension point for TV2."""


class Ledger:
    """Placeholder for pending and finalized block views."""

    def __init__(self) -> None:
        self.finalized: list[object] = []
