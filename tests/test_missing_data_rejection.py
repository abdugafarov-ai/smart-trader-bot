import unittest
import asyncio
import pandas as pd
from strategies.ict_smc import ICTSMCStrategy
from market.data_fetcher import DataFetcher

class TestMissingDataRejection(unittest.TestCase):
    def setUp(self):
        self.strategy = ICTSMCStrategy()
        self.fetcher = DataFetcher()

    def test_missing_ltf_returns_neutral(self):
        """
        Tests that analyze returns NEUTRAL / NO TRADE if LTF timeframe (M5) is missing
        or empty, rather than faking confirmation.
        """
        # Multi-timeframe dict missing 'M5'
        mtf_data = {
            "D1": pd.DataFrame({"close": [2600.0, 2610.0]}),
            "H1": pd.DataFrame({"close": [2605.0, 2615.0]}),
            "M15": pd.DataFrame({"close": [2610.0, 2612.0]})
        }
        res = self.strategy.analyze(mtf_data, symbol="XAUUSD", timeframe="M15")
        self.assertEqual(res.signal.direction, "NEUTRAL")

    def test_empty_dataframe_returns_neutral(self):
        """
        Tests that analyze returns NEUTRAL when provided with an empty DataFrame.
        """
        empty_df = pd.DataFrame()
        res = self.strategy.analyze(empty_df, symbol="XAUUSD", timeframe="M15")
        self.assertEqual(res.signal.direction, "NEUTRAL")

    def test_gold_paxg_fallback_strictly_prohibited(self):
        """
        Tests that DataFetcher.fetch_ohlcv refuses Binance PAXG fallback for XAUUSD
        when MT5 broker data is unavailable.
        """
        async def run_test():
            # Call with an invalid timeframe or offline broker
            df = await self.fetcher.fetch_ohlcv("XAUUSD", "M99")  # Non-existent timeframe in MT5
            self.assertTrue(df.empty, "Expected empty DataFrame for invalid MT5 timeframe instead of PAXG fallback")

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
