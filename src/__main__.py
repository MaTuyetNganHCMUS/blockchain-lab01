"""Command-line entry point for the project skeleton."""

from .network import NetworkSimulator


def main() -> None:
    simulator = NetworkSimulator()
    print(f"Blockchain Lab 01 skeleton is ready (seed={simulator.seed}).")


if __name__ == "__main__":
    main()
