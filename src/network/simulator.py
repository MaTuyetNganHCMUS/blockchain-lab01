"""Discrete-event network simulator extension point for TV3."""


class NetworkSimulator:
    """Placeholder for deterministic packet scheduling."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
