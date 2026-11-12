#!/usr/bin/env python3
"""Runner for TV2 Unit Tests and E2E Simulation Scenarios (T1 & T2).
Owner: TV2 (Execution Engine, State Commitment & Ledger View)
"""
import sys
import unittest

from tests.e2e.test_t1_t2_simulation import TestE2EScenarios
from tests.unit.test_state_ledger import TestLedgerValidation, TestStateEngine


def main() -> int:
    print("=" * 70)
    print("      BLOCKCHAIN LAB 01 - TV2 TEST SUITE EXECUTION")
    print("      (State Engine, State Commitment, Ledger View & E2E T1-T2)")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestStateEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestLedgerValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestE2EScenarios))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    if result.wasSuccessful():
        total_tests = result.testsRun
        print("=" * 70)
        print(f"✅ ALL {total_tests} TESTS PASSED SUCCESSFULLY! (100% SUCCESS RATE)")
        print("   - Protocol Compliance: StateProtocol & LedgerProtocol OK")
        print("   - Unit Tests: State isolation, Merkle root, Ownership & Replay: OK")
        print("   - E2E Test T1: 8+ Nodes State Convergence: OK")
        print("   - E2E Test T2: Duplicate/Reordered Packet Resilience: OK")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("❌ SOME TESTS FAILED!")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
