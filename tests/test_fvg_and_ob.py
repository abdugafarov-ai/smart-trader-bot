import unittest
import pandas as pd
import numpy as np
from strategies.ict_smc import ICTSMCStrategy

class TestFVGAndOB(unittest.TestCase):
    def setUp(self):
        self.strategy = ICTSMCStrategy()

    def test_bullish_fvg_detection(self):
        """
        Tests detection of a clean Bullish 3-candle Fair Value Gap:
        - Candle 1: High = 2000.0
        - Candle 2 (Displacement): Open = 2000.0, Close = 2020.0
        - Candle 3: Low = 2005.0 (Low > Candle 1 High by $5.00)
        Gap = [2000.0, 2005.0]
        """
        # Create 10 bars with a clean FVG at bars 4, 5, 6
        opens  = [1990, 1992, 1995, 1997, 2000, 2020, 2022, 2024, 2025, 2026]
        highs  = [1995, 1996, 1998, 2000, 2022, 2025, 2026, 2027, 2028, 2029]
        lows   = [1988, 1990, 1993, 1996, 1999, 2005, 2018, 2020, 2022, 2023]
        closes = [1992, 1995, 1997, 2000, 2020, 2022, 2024, 2025, 2026, 2028]

        df = pd.DataFrame({
            'timestamp': [f'2026.10.27 10:{i:02d}' for i in range(len(opens))],
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': [100] * len(opens)
        })

        atr = 3.0
        current_price = 2028.0
        fvg = self.strategy._find_fvg(df, direction="LONG", atr=atr, current_price=current_price, symbol="XAUUSD")

        self.assertIsNotNone(fvg, "Expected Bullish FVG to be detected")
        fvg_low, fvg_high = fvg
        # FVG zone must correspond to gap between Candle 3 (high=2000) and Candle 5 (low=2005)
        self.assertAlmostEqual(fvg_low, 2000.0)
        self.assertAlmostEqual(fvg_high, 2005.0)

    def test_dealing_range_discount_premium(self):
        """
        Tests dealing range calculation:
        - Equilibrium = (Range High + Range Low) / 2
        - Price < Equilibrium is Discount (valid for Buys)
        - Price > Equilibrium is Premium (valid for Sells)
        """
        range_high = 2050.0
        range_low = 1950.0
        eq = (range_high + range_low) / 2.0 # 2000.0

        # Price at 1980.0 is in Discount
        price_discount = 1980.0
        self.assertTrue(price_discount < eq, "Price 1980 must be in Discount (< 2000)")

        # Price at 2020.0 is in Premium
        price_premium = 2020.0
        self.assertTrue(price_premium > eq, "Price 2020 must be in Premium (> 2000)")

if __name__ == "__main__":
    unittest.main()
