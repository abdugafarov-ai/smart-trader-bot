import unittest
import pandas as pd
import numpy as np
from strategies.ict_smc import ICTSMCStrategy

class TestSwingsAndStructure(unittest.TestCase):
    def setUp(self):
        self.strategy = ICTSMCStrategy()

    def test_canonical_5_bar_swing_points(self):
        """
        Tests that _find_swing_points detects true 5-bar fractal swings
        (2 bars left, 1 peak, 2 bars right) and does not look ahead.
        """
        highs = [10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10, 11, 12, 13, 14]
        lows  = [ 8,  9, 10, 11, 12, 15, 12, 11,  8,  5,  8,  9, 10, 11, 12]
        closes= [ 9, 10, 11, 12, 13, 18, 13, 12, 10,  7,  9, 10, 11, 12, 13]
        opens = [ 9, 10, 11, 12, 13, 16, 13, 12, 10,  7,  9, 10, 11, 12, 13]

        df = pd.DataFrame({
            'timestamp': [f'2026.10.27 10:{i:02d}' for i in range(len(highs))],
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': [100] * len(highs)
        })

        swing_highs, swing_lows = self.strategy._find_swing_points(df, lookback=2)

        # Index 5 must be detected as a swing high (high=20)
        high_indices = [h[0] for h in swing_highs]
        self.assertIn(5, high_indices)
        self.assertEqual(swing_highs[0][1], 20.0)

        # Index 9 must be detected as a swing low (low=5)
        low_indices = [l[0] for l in swing_lows]
        self.assertIn(9, low_indices)
        self.assertEqual(swing_lows[0][1], 5.0)

    def test_insufficient_swings_returns_neutral_no_fake_swings(self):
        """
        When canonical swings are fewer than 2, strategy must return NEUTRAL (NO TRADE)
        and NOT fabricate artificial swings.
        """
        df = pd.DataFrame({
            'timestamp': [f'2026.10.27 10:{i:02d}' for i in range(15)],
            'open': [100.0 + i for i in range(15)],
            'high': [101.0 + i for i in range(15)],
            'low': [99.0 + i for i in range(15)],
            'close': [100.5 + i for i in range(15)],
            'volume': [100] * 15
        })

        result = self.strategy.analyze(df, symbol="XAUUSD", timeframe="M15")
        self.assertEqual(result.signal.direction, "NEUTRAL")

    def test_bos_body_close_vs_wick_sweep(self):
        """
        Tests validate_structure_break:
        - True BOS requires body close beyond swing level.
        - Piercing with wick only is classified as high_wick_sweep, NOT bullish_bos.
        """
        swing_high = 2000.0
        swing_low = 1950.0

        # Case 1: Candle pierces 2000.0 with high=2005.0 but closes at 1998.0 (Wick Sweep)
        df_sweep = pd.DataFrame({
            'timestamp': ['2026.10.27 10:00', '2026.10.27 10:05'],
            'open': [1990.0, 1995.0],
            'high': [1995.0, 2005.0],  # Pierces 2000.0
            'low': [1985.0, 1990.0],
            'close': [1992.0, 1998.0], # Closes below 2000.0!
            'volume': [100, 150]
        })

        res_sweep = self.strategy.validate_structure_break(df_sweep, swing_high=swing_high, swing_low=swing_low, symbol="XAUUSD")
        self.assertFalse(res_sweep["bullish_bos"], "Expected bullish_bos to be False on wick sweep")
        self.assertTrue(res_sweep["high_wick_sweep"], "Expected high_wick_sweep to be True on wick sweep")

        # Case 2: Candle closes at 2003.0 (True Bullish BOS)
        df_bos = pd.DataFrame({
            'timestamp': ['2026.10.27 10:00', '2026.10.27 10:05'],
            'open': [1990.0, 1995.0],
            'high': [1995.0, 2005.0],
            'low': [1985.0, 1990.0],
            'close': [1992.0, 2003.0], # Closes above 2000.0!
            'volume': [100, 150]
        })

        res_bos = self.strategy.validate_structure_break(df_bos, swing_high=swing_high, swing_low=swing_low, symbol="XAUUSD")
        self.assertTrue(res_bos["bullish_bos"], "Expected bullish_bos to be True on body close above swing high")

if __name__ == "__main__":
    unittest.main()
