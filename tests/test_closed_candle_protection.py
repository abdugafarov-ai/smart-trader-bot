import unittest
import os
import asyncio
import pandas as pd
from unittest.mock import patch, MagicMock
from market.data_fetcher import DataFetcher

class TestClosedCandleProtection(unittest.TestCase):
    def setUp(self):
        self.fetcher = DataFetcher()

    def test_read_mt5_bars_drops_unclosed_candle(self):
        """
        In MT5 export, the last row is the active unclosed forming bar (rates[0]).
        When closed_only=True, it must be dropped so strategies only see 100% closed bars.
        """
        mock_data = {
            'timestamp': ['2026.10.27 10:00', '2026.10.27 10:05', '2026.10.27 10:10', '2026.10.27 10:15', '2026.10.27 10:20'],
            'open': [2000.0, 2002.0, 2001.0, 2003.0, 2005.0],
            'high': [2005.0, 2004.0, 2005.0, 2007.0, 2010.0],
            'low': [1998.0, 2000.0, 1999.0, 2001.0, 2004.0],
            'close': [2002.0, 2001.0, 2003.0, 2005.0, 2008.0],
            'volume': [100, 150, 120, 180, 50]
        }
        df_raw = pd.DataFrame(mock_data)

        with patch('os.path.exists', return_value=True), patch('pandas.read_csv', return_value=df_raw.copy()):
            df_closed = self.fetcher._read_mt5_bars("XAUUSD", "M5", limit=100, closed_only=True)
            self.assertIsNotNone(df_closed)
            # Must drop the last unclosed bar at 10:20
            self.assertEqual(len(df_closed), 4)
            self.assertEqual(df_closed.iloc[-1]['timestamp'], '2026.10.27 10:15')
            self.assertEqual(df_closed.iloc[-1]['close'], 2005.0)

            # When closed_only=False, it should retain all 5 bars
            df_all = self.fetcher._read_mt5_bars("XAUUSD", "M5", limit=100, closed_only=False)
            self.assertEqual(len(df_all), 5)
            self.assertEqual(df_all.iloc[-1]['timestamp'], '2026.10.27 10:20')

    def test_xauusd_refuses_paxg_fallback(self):
        """
        When MT5 broker data for XAUUSD is unavailable or empty,
        fetch_ohlcv must strictly return an empty DataFrame (NO TRADE),
        and NEVER fall back to Binance PAXG/USDT.
        """
        async def run_test():
            with patch.object(self.fetcher, '_read_mt5_bars', return_value=None):
                df = await self.fetcher.fetch_ohlcv("XAUUSD", "M15")
                self.assertTrue(df.empty, "Expected empty DataFrame when MT5 broker data is missing for XAUUSD")

        asyncio.run(run_test())

    def test_historical_bars_immutable_by_live_price(self):
        """
        Live price ticks must not mutate historical candle OHLC.
        """
        async def run_test():
            # Create 25 mock bars
            mock_data = {
                'timestamp': [f'2026.10.27 10:{i:02d}' for i in range(25)],
                'open': [2000.0 + i for i in range(25)],
                'high': [2005.0 + i for i in range(25)],
                'low': [1995.0 + i for i in range(25)],
                'close': [2002.0 + i for i in range(25)],
                'volume': [100 for _ in range(25)]
            }
            df_mock = pd.DataFrame(mock_data)

            with patch.object(self.fetcher, '_read_mt5_bars', return_value=df_mock.copy()), \
                 patch.object(self.fetcher, 'get_live_price', return_value=3500.0): # Live tick is wildly different ($3500)
                df = await self.fetcher.fetch_ohlcv("EURUSD", "M5")
                # The last closed bar's close must remain 2002.0 + 24 = 2026.0, NOT mutated to 3500.0!
                self.assertEqual(df.iloc[-1]['close'], 2026.0)
                self.assertEqual(df.iloc[-1]['high'], 2029.0)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
