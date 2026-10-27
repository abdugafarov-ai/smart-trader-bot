import unittest
from trading.execution_bridge import calculate_gold_swing_lot, ExecutionBridge

class TestRiskAndLotSizing(unittest.TestCase):
    def test_gold_risk_budget_025_percent(self):
        """
        On a $5,000 account, 0.25% risk = $12.50.
        1 standard lot of XAUUSD = 100 oz ($100 per $1.00 move).
        A 0.01 lot moves $1.00 per $1.00 price move.
        """
        balance = 5000.0
        risk_pct = 0.25  # 0.25%

        # Case 1: Stop Loss = $20.00
        # Minimum lot (0.01) dollar risk = 0.01 * 20.0 * 100 = $20.00.
        # $20.00 > $12.50 budget -> Must strictly return 0.0 (NO TRADE / REJECT)
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=20.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.0, f"Expected 0.0 for exceeding risk budget, got {lot}")

        # Case 2: Stop Loss = $5.00
        # Risk budget = $12.50.
        # Raw lot = $12.50 / ($5.00 * 100) = 0.025 lot -> floored to 0.02 lot.
        # Actual risk = 0.02 * $5.00 * 100 = $10.00 <= $12.50.
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=5.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.02, f"Expected 0.02 lot, got {lot}")

        # Case 3: Stop Loss = $10.00
        # Raw lot = $12.50 / ($10.00 * 100) = 0.0125 -> 0.01 lot.
        # Actual risk = 0.01 * $10.00 * 100 = $10.00 <= $12.50.
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=10.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.01, f"Expected 0.01 lot, got {lot}")

    def test_gold_risk_budget_050_percent(self):
        """
        On a $5,000 account, 0.50% risk = $25.00.
        """
        balance = 5000.0
        risk_pct = 0.50

        # Case 1: Stop Loss = $20.00
        # Min lot (0.01) risk = $20.00 <= $25.00 -> returns 0.01 lot.
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=20.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.01, f"Expected 0.01 lot, got {lot}")

        # Case 2: Stop Loss = $30.00
        # Min lot (0.01) risk = $30.00 > $25.00 -> Must return 0.0 (REJECT).
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=30.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.0, f"Expected 0.0 lot, got {lot}")

        # Case 3: Stop Loss = $5.00
        # Raw lot = $25.00 / ($5.00 * 100) = 0.05 lot.
        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=5.0, risk_pct=risk_pct)
        self.assertEqual(lot, 0.05, f"Expected 0.05 lot, got {lot}")

    def test_bridge_calculate_lot_rejection(self):
        """Tests ExecutionBridge.calculate_lot unified method."""
        bridge = ExecutionBridge()
        bridge.default_risk = 0.25

        # Gold trade with 250 pips ($25.00) SL -> exceeds $12.50 budget -> 0.0
        lot_gold = bridge.calculate_lot(symbol="XAUUSD", sl_pips=250.0, balance=5000.0, risk_pct=0.25)
        self.assertEqual(lot_gold, 0.0)

        # Forex trade with 50 pips SL ($500 / lot -> 0.01 lot = $5.00) <= $12.50 budget
        lot_forex = bridge.calculate_lot(symbol="EURUSD", sl_pips=50.0, balance=5000.0, risk_pct=0.25)
        self.assertEqual(lot_forex, 0.02)

if __name__ == "__main__":
    unittest.main()
