"""Execution and state commitment extension point for TV2."""


class State:
    """Minimal state container used while implementation branches are independent."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

