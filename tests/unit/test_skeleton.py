from src.interfaces import Transaction


def test_shared_transaction_contract() -> None:
    transaction = Transaction("Alice", "Alice/name", "Lab01", 0)
    assert transaction.sender == "Alice"
    assert transaction.nonce == 0
