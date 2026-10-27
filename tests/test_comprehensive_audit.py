import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import numpy as np

import config
from market.data_fetcher import DataFetcher
from strategies.ict_smc import ICTSMCStrategy
from trading.execution_bridge import ExecutionBridge, calculate_gold_swing_lot


def _build_intraday_gold_frames():
    """
    Constructs realistic closed-bar OHLCV DataFrames for D1, H1, M15, and M5 on XAUUSD
    representing:
    - D1: Bullish trend (higher closes above EMA50)
    - M15 / H1: Clear 5-bar fractal swing low at 2600.0 and swing high at 2640.0,
      followed by a pullback into Discount (< 45% DR, around 2606.0) into an unmitigated
      Bullish Order Block [2603.0, 2607.0] with a tight structural invalidation (~$4.00-$5.00 SL).
    - M5: Touch of the M15 POI [2603.0, 2607.0] followed by a strong bullish displacement
      MSS body close above the recent M5 swing high.
    """
    # 1. D1 DataFrame (60 bars, steady uptrend from 2500 to 2620)
    d1_rows = []
    for i in range(60):
        base = 2500.0 + i * 2.0
        d1_rows.append({
            "timestamp": f"2026-08-{(i % 28) + 1:02d} 00:00",
            "open": base,
            "high": base + 8.0,
            "low": base - 3.0,
            "close": base + 5.0,
            "volume": 1000.0,
        })
    df_d1 = pd.DataFrame(d1_rows)

    # 2. M15 DataFrame (45 bars)
    # Swing 1: Low at bar 5 (2595.0), High at bar 12 (2625.0)
    # Swing 2: Higher Low at bar 20 (2602.0) -> Bullish OB at bar 20 [low=2602.5, high=2606.0]
    # followed by strong bullish impulse bar 21 (open=2603.0, close=2618.0, high=2619.0)
    # and Higher High at bar 30 (2640.0).
    # Then bars 31..44 pull back into Discount (< 45% of [2602.0, 2640.0] -> Eq=2621.0, 45%=2619.1)
    # down to 2606.5 (inside OB [2602.5, 2606.0] + buffer).
    m15_rows = []
    for i in range(45):
        m15_rows.append({
            "timestamp": f"2026-10-27 {i // 4:02d}:{(i % 4) * 15:02d}",
            "open": 2610.0,
            "high": 2613.0,
            "low": 2608.0,
            "close": 2611.0,
            "volume": 200.0,
        })

    # Fractal Swing Low 1 at idx=5 (low=2595.0)
    for idx, lo in [(3, 2605.0), (4, 2600.0), (5, 2595.0), (6, 2600.0), (7, 2604.0)]:
        m15_rows[idx].update({"open": lo + 2.0, "low": lo, "high": lo + 5.0, "close": lo + 3.0})

    # Fractal Swing High 1 at idx=12 (high=2625.0)
    for idx, hi in [(10, 2615.0), (11, 2620.0), (12, 2625.0), (13, 2619.0), (14, 2615.0)]:
        m15_rows[idx].update({"open": hi - 3.0, "low": hi - 5.0, "high": hi, "close": hi - 2.0})

    # Fractal Higher Low 2 at idx=20 (low=2602.5, bearish candle forming Bullish Order Block)
    m15_rows[18].update({"open": 2610.0, "high": 2611.0, "low": 2606.0, "close": 2607.0})
    m15_rows[19].update({"open": 2607.0, "high": 2608.0, "low": 2604.0, "close": 2605.0})
    m15_rows[20].update({"open": 2605.8, "high": 2606.0, "low": 2602.5, "close": 2603.0, "volume": 350.0})
    # Bar 21: Strong bullish impulse candle (unmitigated OB + FVG)
    m15_rows[21].update({"open": 2603.0, "high": 2616.0, "low": 2603.0, "close": 2615.0, "volume": 500.0})
    m15_rows[22].update({"open": 2615.0, "high": 2628.0, "low": 2612.0, "close": 2626.0, "volume": 450.0})

    # Fractal Higher High 2 at idx=30 (high=2640.0)
    for idx, hi in [(28, 2630.0), (29, 2635.0), (30, 2640.0), (31, 2634.0), (32, 2628.0)]:
        m15_rows[idx].update({"open": hi - 4.0, "low": hi - 6.0, "high": hi, "close": hi - 1.5})

    # Pullback from idx=33..43 stays above OB high (2606.0) so OB is unmitigated prior to idx=44,
    # and idx=44 (current closed bar) taps 2605.8 right inside the Discount OB [2602.5, 2606.0]!
    pullback_prices = [2626.0, 2622.0, 2619.0, 2616.5, 2614.0, 2612.0, 2610.5, 2609.5, 2608.5, 2607.8, 2607.2, 2605.8]
    for offset, p in enumerate(pullback_prices):
        idx = 33 + offset
        if idx < 44:
            m15_rows[idx].update({
                "open": p + 1.0,
                "high": p + 2.0,
                "low": max(2606.5, p - 0.5),
                "close": p,
                "volume": 250.0,
            })
        else:
            # Final closed M15 candle touches the OB [2602.5, 2606.0] in Discount (<45% DR)
            m15_rows[idx].update({
                "open": 2607.2,
                "high": 2607.5,
                "low": 2605.2,
                "close": 2605.8,
                "volume": 320.0,
            })

    df_m15 = pd.DataFrame(m15_rows)

    # 3. M5 DataFrame (40 bars): touches POI [2602.5, 2606.0] and prints a bullish MSS expansion candle at the end
    m5_rows = []
    for i in range(40):
        m5_rows.append({
            "timestamp": f"2026-10-27 08:{i % 60:02d}",
            "open": 2608.0,
            "high": 2609.5,
            "low": 2606.5,
            "close": 2607.5,
            "volume": 150.0,
        })
    # Create a local M5 swing high at idx=30 (high=2608.5)
    for idx, hi in [(28, 2607.5), (29, 2608.0), (30, 2608.5), (31, 2607.8), (32, 2607.2)]:
        m5_rows[idx].update({"open": hi - 1.0, "low": hi - 1.5, "high": hi, "close": hi - 0.5})
    # Touch POI at idx=36 (low=2605.2 inside [2602.5, 2606.0])
    m5_rows[36].update({"open": 2606.8, "high": 2607.0, "low": 2605.0, "close": 2606.2})
    # Bullish MSS + displacement at idx=39 (closes at 2609.2 > 2608.5 swing high with large body)
    m5_rows[39].update({"open": 2606.2, "high": 2609.5, "low": 2606.0, "close": 2609.2, "volume": 400.0})
    df_m5 = pd.DataFrame(m5_rows)

    return df_d1, df_m15.copy(), df_m15.copy(), df_m5


class TestComprehensiveAudit(unittest.TestCase):
    def setUp(self):
        self.bridge = ExecutionBridge()
        self.bridge.default_risk = 0.25
        self.bridge.default_lot = 0.01
        self.bridge.lot_mode = "risk"
        self.fetcher = DataFetcher()
        self.strategy = ICTSMCStrategy()

    def test_gold_sl_over_35_usd_bug_fixed(self):
        """
        Verifies that the critical unit-confusion bug in ExecutionBridge.calculate_lot
        (which multiplied sl_pips > 35.0 by 0.10 and falsely shrank $40.00 Gold stops to $4.00)
        is eliminated.
        """
        # 1. Explicit sl_dist_usd = $40.00 on $5,000 at 0.25% ($12.50 budget) -> MUST return 0.0
        lot_40_usd = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_pips=400.0,
            balance=5000.0,
            risk_pct=0.25,
            sl_dist_usd=40.0,
        )
        self.assertEqual(lot_40_usd, 0.0, "A $40.00 SL on XAUUSD must return 0.0 lot (NO TRADE)!")

        # 2. Entry/SL price difference = $40.00 -> MUST return 0.0
        lot_from_prices = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_pips=40.0,  # Even if caller mistakenly passed USD as sl_pips
            balance=5000.0,
            risk_pct=0.25,
            entry=2650.0,
            sl=2610.0,
        )
        self.assertEqual(lot_from_prices, 0.0, "Entry=2650, SL=2610 ($40 SL) must return 0.0 lot!")

        # 3. Tight $4.00 SL (40 pips at $0.10/pip) -> 0.03 lot ($12.00 risk <= $12.50 budget)
        lot_4_usd = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_pips=40.0,
            balance=5000.0,
            risk_pct=0.25,
            sl_dist_usd=4.0,
        )
        self.assertEqual(lot_4_usd, 0.03)

    def test_fixed_lot_mode_cannot_bypass_risk_budget(self):
        """
        Verifies that even when lot_mode == 'fixed', ExecutionBridge.calculate_lot
        refuses to return a fixed lot that exceeds the monetary risk budget.
        """
        self.bridge.lot_mode = "fixed"
        self.bridge.default_lot = 0.05  # User tries to fix 0.05 lot

        # On a $5.00 Gold SL at 0.25% risk ($12.50 budget), max safe lot is 0.02 ($10.00).
        # 0.05 lot would risk $25.00 > $12.50 -> must return 0.0 (REJECT)!
        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_pips=50.0,
            balance=5000.0,
            risk_pct=0.25,
            sl_dist_usd=5.0,
        )
        self.assertEqual(lot, 0.0, "Fixed lot 0.05 exceeding risk budget must be rejected (0.0)!")

        # If fixed lot is 0.01 on a $5.00 SL ($5.00 <= $12.50), it is allowed:
        self.bridge.default_lot = 0.01
        lot_ok = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_pips=50.0,
            balance=5000.0,
            risk_pct=0.25,
            sl_dist_usd=5.0,
        )
        self.assertEqual(lot_ok, 0.01)

    def test_commission_and_spread_buffer_in_lot_calculation(self):
        """
        Verifies that commission_per_lot and spread_buffer_usd are accounted for
        so total transaction cost never exceeds the monetary risk budget.
        """
        # $6.00 SL + $0.30 spread buffer + $7.00/lot commission -> per 0.01 lot = $6.37.
        # On $12.50 budget, 0.02 lot would cost $12.74 > $12.50 -> must floor to 0.01 lot!
        lot = calculate_gold_swing_lot(
            balance=5000.0,
            sl_dist_usd=6.0,
            risk_pct=0.25,
            commission_per_lot=7.0,
            spread_buffer_usd=0.30,
        )
        self.assertEqual(lot, 0.01)

    def test_cumulative_open_risk_and_eod_flat_trigger(self):
        """
        Tests get_cumulative_open_risk_usd and check_and_trigger_eod_flat.
        """
        self.bridge.mt5_telemetry["balance"] = 5000.0
        self.bridge.mt5_telemetry["equity"] = 5000.0
        self.bridge.mt5_telemetry["positions"] = [
            {"symbol": "XAUUSD", "type": "BUY", "lot": 0.02, "price": 2600.0, "sl": 2595.0}
        ]
        # 0.02 * $5.00 * 100 = $10.00 open risk
        self.assertAlmostEqual(self.bridge.get_cumulative_open_risk_usd(), 10.0)

        # At 20:42 UTC on a Tuesday, check_and_trigger_eod_flat must trigger panic_close_requested
        dt_eod = datetime(2026, 10, 27, 20, 42, tzinfo=timezone.utc)
        triggered, reason = self.bridge.check_and_trigger_eod_flat(dt_eod)
        self.assertTrue(triggered)
        self.assertTrue(self.bridge.panic_close_requested)
        self.assertIn("EOD", reason)

    def test_data_fetcher_symbol_normalization_and_corrupted_bar_filtering(self):
        """
        Tests broker symbol normalization (XAUUSD.pro, GOLD, XAUUSDm -> XAUUSD)
        and filtering of corrupted OHLC bars / duplicate timestamps.
        """
        self.assertEqual(DataFetcher.normalize_symbol("XAUUSD.pro"), "XAUUSD")
        self.assertEqual(DataFetcher.normalize_symbol("XAUUSDm"), "XAUUSD")
        self.assertEqual(DataFetcher.normalize_symbol("GOLD"), "XAUUSD")
        self.assertEqual(DataFetcher.normalize_symbol("EURUSD.ecn"), "EURUSD")

        raw_df = pd.DataFrame({
            "timestamp": [
                "2026.10.27 10:05",
                "2026.10.27 10:00",  # Out of order
                "2026.10.27 10:05",  # Duplicate timestamp
                "2026.10.27 10:10",  # Corrupted: high < low
                "2026.10.27 10:15",  # Corrupted: negative price
                "2026.10.27 10:20",  # Valid closed bar
                "2026.10.27 10:25",  # Forming bar (to be dropped when closed_only=True)
            ],
            "open":  [2602.0, 2600.0, 2602.0, 2605.0, -10.0, 2604.0, 2606.0],
            "high":  [2605.0, 2603.0, 2605.0, 2600.0, 2605.0, 2608.0, 2609.0],
            "low":   [2601.0, 2599.0, 2601.0, 2604.0, 2600.0, 2603.0, 2605.0],
            "close": [2604.0, 2602.0, 2604.0, 2602.0, 2602.0, 2606.0, 2607.0],
            "volume": [100, 100, 120, 100, 100, 150, 50],
        })

        with patch("os.path.exists", return_value=True), patch("pandas.read_csv", return_value=raw_df):
            cleaned = self.fetcher._read_mt5_bars("XAUUSD.pro", "M5", limit=100, closed_only=True)
            self.assertIsNotNone(cleaned)
            # Only 3 valid unique closed bars remain: 10:00, 10:05, 10:20 (10:25 dropped as unclosed)
            self.assertEqual(len(cleaned), 3)
            self.assertEqual(list(cleaned["timestamp"]), [
                "2026.10.27 10:00",
                "2026.10.27 10:05",
                "2026.10.27 10:20",
            ])

    def test_dxy_refuses_fake_constant_prices_when_pairs_missing(self):
        """
        Verifies that get_dxy_bars_sync returns an empty DataFrame when component pairs
        are missing instead of fabricating fake constant prices (1.27, 150.0, etc.).
        """
        df_eur = pd.DataFrame({
            "timestamp": ["2026.10.27 10:00", "2026.10.27 10:15"],
            "open": [1.08, 1.081],
            "high": [1.082, 1.083],
            "low": [1.079, 1.080],
            "close": [1.081, 1.082],
        })
        with patch.object(self.fetcher, "_read_mt5_bars", return_value=None):
            dxy_df = self.fetcher.get_dxy_bars_sync("M15", limit=50, df_eur_override=df_eur)
            self.assertTrue(dxy_df.empty, "DXY must be empty when GBP/JPY/CHF/CAD bars are unavailable!")

    def test_intraday_xauusd_signal_and_multi_tf_alignment(self):
        """
        End-to-end verification of ICTSMCStrategy.analyze and run_multi_tf_analysis('XAUUSD')
        for the H1 -> M15 -> M5 Intraday Gold hierarchy during London Kill Zone.
        """
        df_d1, df_h1, df_m15, df_m5 = _build_intraday_gold_frames()
        kz_time = datetime(2026, 10, 27, 8, 30, tzinfo=timezone.utc)  # London Open Kill Zone

        # 1. Direct ICTSMCStrategy.analyze on M15 with M5 LTF confirmation
        res = self.strategy.analyze(
            df_m15,
            symbol="XAUUSD",
            timeframe="M15",
            df_d1=df_d1,
            df_ltf=df_m5,
            current_time=kz_time,
        )
        self.assertEqual(res.signal.direction, "LONG", f"Expected LONG signal, got {res.signal.direction}: {res.signal.details}")
        self.assertGreaterEqual(res.signal.confidence, 4)
        self.assertGreaterEqual(res.signal.risk_reward, 2.0)
        # Structural SL must NOT be artificially inflated to $10.00!
        sl_dist = abs(res.signal.entry - res.signal.stop_loss)
        self.assertLess(sl_dist, 10.0, f"Expected tight intraday SL < $10.00, got ${sl_dist:.2f}")
        self.assertGreaterEqual(sl_dist, 2.0)
        self.assertGreater(res.signal.lot, 0.0)
        self.assertLessEqual(res.signal.risk_usd, 12.50)

        # 2. Wide structural SL (> budget) must be REJECTED (NEUTRAL), NEVER artificially shrunk!
        df_m15_wide = df_m15.copy()
        # Lower the OB low at bar 20 so structural SL distance is ~$30.00 (> $25.00 max limit and > $12.50 budget)
        df_m15_wide.loc[20, "low"] = 2575.0
        df_m15_wide.loc[5, "low"] = 2570.0
        res_wide = self.strategy.analyze(
            df_m15_wide,
            symbol="XAUUSD",
            timeframe="M15",
            df_d1=df_d1,
            df_ltf=df_m5,
            current_time=kz_time,
        )
        self.assertEqual(res_wide.signal.direction, "NEUTRAL", "Wide SL exceeding risk budget must be rejected, not shrunk!")
        self.assertEqual(res_wide.signal.lot, 0.0)

        # 3. End-to-end run_multi_tf_analysis("XAUUSD")
        import bot.handlers as handlers

        async def mock_fetch(sym, tf, limit=300):
            tf_u = str(tf).upper()
            if tf_u == "D1":
                return df_d1.copy()
            if tf_u == "H1":
                return df_h1.copy()
            if tf_u == "M15":
                return df_m15.copy()
            if tf_u == "M5":
                return df_m5.copy()
            return pd.DataFrame()

        async def run_mtf():
            with patch.object(handlers.fetcher, "fetch_ohlcv", side_effect=mock_fetch), \
                 patch("config.is_ipda_time_banned", return_value=(False, "")), \
                 patch("config.is_in_kill_zone", return_value=True), \
                 patch("config.is_in_macro_window", return_value=(True, "London Open Macro")):
                mtf_res = await handlers.run_multi_tf_analysis("XAUUSD")
                self.assertEqual(mtf_res.overall_direction, "LONG")
                self.assertGreaterEqual(mtf_res.overall_stars, 4)
                self.assertGreaterEqual(mtf_res.risk_reward_1, 2.0)
                self.assertGreater(mtf_res.lot, 0.0)
                self.assertLessEqual(mtf_res.risk_usd, 12.50)

        asyncio.run(run_mtf())

    def test_webapp_api_auth_rejects_empty_key_and_over_budget_orders(self):
        """
        Verifies that:
        1. _check_api_key and _check_public_api_auth raise HTTPForbidden for external
           requests when BRIDGE_API_KEY is empty.
        2. bridge_get_orders cancels (CANCELLED_BY_MT5) any pending signal whose SL
           exceeds the monetary risk budget, never falling back to default_lot (0.01/0.02).
        """
        from aiohttp import web
        import webapp.server as srv

        mock_req = MagicMock()
        mock_req.headers = {"X-Bridge-Key": ""}
        mock_req.query = {}
        mock_req.remote = "203.0.113.55"  # External IP

        with patch.object(config, "BRIDGE_API_KEY", ""):
            with self.assertRaises(web.HTTPForbidden):
                srv._check_api_key(mock_req)
            with self.assertRaises(web.HTTPForbidden):
                srv._check_public_api_auth(mock_req)

        # Verify bridge_get_orders cancels a signal with a $25.00 SL on $5,000 at 0.25% risk ($12.50 budget)
        async def run_bridge_orders_test():
            auth_req = MagicMock()
            auth_req.method = "GET"
            auth_req.headers = {"X-Bridge-Key": "test_secret_key"}
            auth_req.query = {}
            auth_req.remote = "127.0.0.1"

            over_budget_signal = {
                "id": 999,
                "symbol": "XAUUSD",
                "direction": "LONG",
                "order_type": "BUY_MARKET",
                "entry_price": 2650.0,
                "stop_loss": 2625.0,  # $25.00 SL -> 0.01 lot costs $25.00 > $12.50 budget!
                "take_profit_1": 2700.0,
                "take_profit_2": 2725.0,
                "stars": 5,
                "lot": 0.02,  # Even if DB stored 0.02, bridge_get_orders must reject it!
            }

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[over_budget_signal])
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)

            with patch.object(config, "BRIDGE_API_KEY", "test_secret_key"), \
                 patch("trading.execution_bridge.bridge_manager.enabled", True), \
                 patch("trading.execution_bridge.bridge_manager.default_risk", 0.25), \
                 patch("trading.execution_bridge.bridge_manager.check_equity_hard_stop", return_value=(False, "")), \
                 patch("trading.execution_bridge.bridge_manager.check_daily_loss", return_value=(False, 0.0, 0.0, 5000.0)), \
                 patch("trading.execution_bridge.bridge_manager.can_open_new_position", return_value=(True, "OK")), \
                 patch("db.database.get_today_broker_pnl", new_callable=AsyncMock, return_value={"total_pnl": 0.0, "deals_count": 0}), \
                 patch("db.database.get_bot_setting", new_callable=AsyncMock, return_value="prop"), \
                 patch("db.database.get_consecutive_sl_count", new_callable=AsyncMock, return_value=0), \
                 patch("aiosqlite.connect", return_value=mock_conn_ctx), \
                 patch.object(srv, "update_signal_status", new_callable=AsyncMock) as mock_update_status:
                resp = await srv.bridge_get_orders(auth_req)
                import json
                payload = json.loads(resp.text)
                self.assertEqual(payload["orders"], [], "Over-budget signal must NOT be sent to MT5!")
                mock_update_status.assert_called_once()
                self.assertEqual(mock_update_status.call_args[0][0], 999)
                self.assertEqual(mock_update_status.call_args[0][1], "CANCELLED_BY_MT5")

        asyncio.run(run_bridge_orders_test())

    def test_breakeven_deal_history_notification_no_name_error(self):
        """
        Verifies that when MT5 posts a closed deal with profit == 0.0 (breakeven)
        in history to bridge_get_orders, no NameError ('profit' vs 'profit_val') occurs.
        """
        import json
        import webapp.server as srv

        async def run_test():
            auth_req = MagicMock()
            auth_req.method = "POST"
            auth_req.headers = {"X-Bridge-Key": "test_secret_key"}
            auth_req.query = {}
            auth_req.remote = "127.0.0.1"
            auth_req.json = AsyncMock(return_value={
                "balance": 5000.0,
                "equity": 5000.0,
                "margin_free": 5000.0,
                "broker": "FundingPips",
                "account": "21027982",
                "positions": [],
                "orders": [],
                "history": [
                    {
                        "ticket": 777001,
                        "symbol": "XAUUSD",
                        "type": "BUY",
                        "lot": 0.01,
                        "price": 2650.0,
                        "profit": 0.0,  # Breakeven deal (profit_val == 0.0)
                        "time": 1761570000,
                        "magic": 888001,
                        "comment": "SmartTrader#101",
                    }
                ],
            })

            class _HybridCursor:
                async def fetchall(self):
                    return []

                async def fetchone(self):
                    return None

                def __await__(self):
                    async def _coro():
                        return self
                    return _coro().__await__()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return None

            mock_cursor = _HybridCursor()
            mock_db = MagicMock()
            mock_db.execute = MagicMock(return_value=mock_cursor)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)

            with patch.object(config, "BRIDGE_API_KEY", "test_secret_key"), \
                 patch("trading.execution_bridge.bridge_manager.enabled", False), \
                 patch("trading.execution_bridge.bridge_manager.check_equity_hard_stop", return_value=(False, "")), \
                 patch("trading.execution_bridge.bridge_manager.check_daily_loss", return_value=(False, 0.0, 0.0, 5000.0)), \
                 patch("db.database.sync_broker_deals", new_callable=AsyncMock, return_value=[{
                     "ticket": 777001,
                     "symbol": "XAUUSD",
                     "type": "BUY",
                     "lot": 0.01,
                     "price": 2650.0,
                     "profit": 0.0,
                     "time": 1761570000,
                     "magic": 888001,
                     "comment": "SmartTrader#101",
                 }]), \
                 patch("db.database.close_signal_by_broker", new_callable=AsyncMock, return_value={
                     "entry_price": 2650.0,
                     "stop_loss": 2645.0,
                     "take_profit_1": 2660.0,
                     "direction": "LONG",
                 }), \
                 patch("db.database.get_today_broker_pnl", new_callable=AsyncMock, return_value={"total_pnl": 0.0, "deals_count": 1}), \
                 patch("db.database.get_bot_setting", new_callable=AsyncMock, return_value="prop"), \
                 patch("market.data_fetcher.DataFetcher.fetch_ohlcv", new_callable=AsyncMock, return_value=pd.DataFrame()), \
                 patch("aiosqlite.connect", return_value=mock_conn_ctx), \
                 patch.object(srv, "_send_telegram_notification", new_callable=AsyncMock) as mock_notify:
                resp = await srv.bridge_get_orders(auth_req)
                payload = json.loads(resp.text)
                self.assertEqual(payload["status"], "ok")
                mock_notify.assert_called_once()
                sent_admin_msg = mock_notify.call_args.kwargs.get("text_admin", "")
                self.assertIn("+0.00 USD", sent_admin_msg)

        asyncio.run(run_test())

    def test_stale_unconfirmed_signal_expired_and_not_dispatched(self):
        """
        Verifies that an unconfirmed ACTIVE market signal older than 15 minutes
        is marked EXPIRED and NOT dispatched to MT5 when autopilot is enabled later.
        """
        import json
        import webapp.server as srv

        async def run_test():
            auth_req = MagicMock()
            auth_req.method = "GET"
            auth_req.headers = {"X-Bridge-Key": "test_secret_key"}
            auth_req.query = {}
            auth_req.remote = "127.0.0.1"

            stale_signal = {
                "id": 888,
                "symbol": "XAUUSD",
                "direction": "LONG",
                "order_type": "BUY_MARKET",
                "entry_price": 2650.0,
                "stop_loss": 2645.0,
                "take_profit_1": 2660.0,
                "take_profit_2": 2665.0,
                "stars": 5,
                "lot": 0.02,
                "status": "ACTIVE",
                "broker_confirmed": 0,
                "created_at": "2026-01-01T00:00:00Z",  # Hours/days old
            }

            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[stale_signal])
            mock_db = AsyncMock()
            mock_db.execute = AsyncMock(return_value=mock_cursor)
            mock_conn_ctx = MagicMock()
            mock_conn_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)

            with patch.object(config, "BRIDGE_API_KEY", "test_secret_key"), \
                 patch("trading.execution_bridge.bridge_manager.enabled", True), \
                 patch("trading.execution_bridge.bridge_manager.default_risk", 0.25), \
                 patch("trading.execution_bridge.bridge_manager.check_equity_hard_stop", return_value=(False, "")), \
                 patch("trading.execution_bridge.bridge_manager.check_daily_loss", return_value=(False, 0.0, 0.0, 5000.0)), \
                 patch("trading.execution_bridge.bridge_manager.can_open_new_position", return_value=(True, "OK")), \
                 patch("db.database.get_today_broker_pnl", new_callable=AsyncMock, return_value={"total_pnl": 0.0, "deals_count": 0}), \
                 patch("db.database.get_bot_setting", new_callable=AsyncMock, return_value="prop"), \
                 patch("db.database.get_consecutive_sl_count", new_callable=AsyncMock, return_value=0), \
                 patch("aiosqlite.connect", return_value=mock_conn_ctx), \
                 patch.object(srv, "update_signal_status", new_callable=AsyncMock) as mock_update_status:
                resp = await srv.bridge_get_orders(auth_req)
                payload = json.loads(resp.text)
                self.assertEqual(payload["orders"], [], "Stale unconfirmed market signal must NOT be dispatched!")
                mock_update_status.assert_called_once()
                self.assertEqual(mock_update_status.call_args[0][0], 888)
                self.assertEqual(mock_update_status.call_args[0][1], "EXPIRED")

        asyncio.run(run_test())

    def test_live_telemetry_balance_and_midnight_rollover_guard(self):
        """
        Verifies:
        1. ExecutionBridge.account_balance, account_equity, and get_effective_balance()
           use live MT5 telemetry (min of balance and equity).
        2. check_midnight_rollover_guard reads 'price' from MT5 position telemetry
           and computes safe_sl = open_price + 3.0 (not 0.0 + 3.0 = 3.0!).
        """
        self.bridge.mt5_telemetry["balance"] = 4800.0
        self.bridge.mt5_telemetry["equity"] = 4720.0
        self.assertEqual(self.bridge.account_balance, 4800.0)
        self.assertEqual(self.bridge.account_equity, 4720.0)
        self.assertEqual(self.bridge.get_effective_balance(5000.0), 4720.0)

        # Test check_midnight_rollover_guard with 'price' key (as sent by SmartTraderBridge.mq5)
        self.bridge.mt5_telemetry["positions"] = [
            {
                "ticket": 555111,
                "symbol": "XAUUSD",
                "type": "BUY",
                "lot": 0.02,
                "price": 2650.0,
                "sl": 2645.0,
                "tp": 2700.0,
                "profit": 95.0,
            }
        ]
        # Mock datetime.now in execution_bridge to 23:35 broker time (20:35 UTC in EEST UTC+3)
        rollover_utc = datetime(2026, 10, 20, 20, 35, tzinfo=timezone.utc)
        with patch("trading.execution_bridge.datetime") as mock_dt:
            mock_dt.now.return_value = rollover_utc
            actions = asyncio.run(self.bridge.check_midnight_rollover_guard())
            self.assertEqual(len(actions), 1)
            self.assertIn("2653.00", actions[0], "safe_sl must be 2650.0 + 3.0 = 2653.00, not 3.00!")

    def test_circuit_breaker_persistence_and_dxy_5_majors(self):
        """
        Verifies:
        1. Circuit breaker states (equity_hard_stop_triggered, daily_loss_locked) are restored
           from DB on restart and block enabling autopilot until unlock_equity_hard_stop() is called.
        2. get_dxy_bars_sync synthesizes 5-major DXY when USDSEK is not exported by the broker.
        """
        async def run_cb_test():
            bridge = ExecutionBridge()
            db_store = {
                "equity_hard_stop_triggered": "true",
                "equity_hard_stop_reason": "Equity $4440 <= $4450",
                "autotrade_enabled": "true",
            }
            with patch("db.database.get_bot_setting", new_callable=AsyncMock) as mock_get, \
                 patch("db.database.set_bot_setting", new_callable=AsyncMock):
                mock_get.side_effect = lambda k, default="": db_store.get(k, default)
                await bridge.load_settings_from_db()
                self.assertTrue(bridge.equity_hard_stop_triggered)
                self.assertFalse(bridge.enabled, "Hard stop must keep autopilot disabled on restart!")

                # Attempting set_enabled(True) while hard stop is active must be refused
                bridge.set_enabled(True)
                self.assertFalse(bridge.enabled)

                # Explicit admin unlock clears hard stop
                bridge.unlock_equity_hard_stop()
                await asyncio.sleep(0)
                self.assertFalse(bridge.equity_hard_stop_triggered)

        asyncio.run(run_cb_test())

        # Test 5-major DXY synthesis when USDSEK is None
        base_df = pd.DataFrame({
            "timestamp": ["2026.10.27 10:00", "2026.10.27 10:15", "2026.10.27 10:30", "2026.10.27 10:45", "2026.10.27 11:00"],
            "open":  [1.0800, 1.0805, 1.0810, 1.0815, 1.0820],
            "high":  [1.0810, 1.0815, 1.0820, 1.0825, 1.0830],
            "low":   [1.0790, 1.0795, 1.0800, 1.0805, 1.0810],
            "close": [1.0805, 1.0810, 1.0815, 1.0820, 1.0825],
            "volume": [100]*5,
        })

        def mock_read_bars(sym, tf, limit=150, closed_only=True):
            if sym == "USDSEK":
                return None  # Broker does not export USDSEK
            return base_df.copy()

        with patch.object(self.fetcher, "_read_mt5_bars", side_effect=mock_read_bars):
            dxy_5 = self.fetcher.get_dxy_bars_sync("M15", limit=50)
            self.assertFalse(dxy_5.empty, "5-major DXY must be synthesized when EUR/GBP/JPY/CHF/CAD are available!")
            self.assertEqual(len(dxy_5), 5)

    def test_xauusd_intraday_profile_and_validate_config(self):
        """
        Verifies:
        1. Explicit TRADING_PROFILES['XAUUSD_INTRADAY'] exists and has safe intraday defaults.
        2. validate_config() passes on valid config and raises ValueError on unsafe risk/equity bounds.
        3. Account number is masked in CHALLENGE_ACCOUNT_MASKED.
        """
        self.assertIn("XAUUSD_INTRADAY", config.TRADING_PROFILES)
        prof = config.get_active_profile()
        self.assertEqual(prof["symbol"], "XAUUSD")
        self.assertEqual(prof["default_risk_pct"], 0.25)
        self.assertEqual(prof["max_risk_pct"], 0.50)
        self.assertEqual(prof["min_lot"], 0.01)
        self.assertEqual(prof["min_rr"], 2.0)
        self.assertFalse(prof["autotrade_enabled_default"])
        self.assertTrue(config.CHALLENGE_ACCOUNT_MASKED.startswith("*"))

        chk = config.validate_config(raise_on_error=True)
        self.assertTrue(chk["valid"])
        self.assertEqual(chk["errors"], [])

        # Unsafe risk bound (default > max) must fail validation
        with patch.object(config, "AUTOTRADE_DEFAULT_RISK", 0.80), \
             patch.object(config, "AUTOTRADE_MAX_RISK", 0.50):
            bad_chk = config.validate_config(raise_on_error=False)
            self.assertFalse(bad_chk["valid"])
            with self.assertRaises(ValueError):
                config.validate_config(raise_on_error=True)

    def test_model_b_pullback_without_outer_sweep_achieves_4_stars(self):
        """
        Verifies that Model B (Trend Pullback -> Unmitigated OB in Discount < 45% DR -> M5 MSS)
        achieves >= 4 stars even when price does NOT sweep the outer swing low (hl1),
        resolving the 3-star deadlock.
        """
        df_d1, df_h1, df_m15, df_m5 = _build_intraday_gold_frames()
        # Separate the fractal Higher Low (hl1 at idx=17, low=2596.0 > hl2=2595.0 at idx=5)
        # from the Bullish Order Block at idx=20 (low=2602.5, high=2606.0).
        # Because idx=19 has low=2602.0 < 2602.5, idx=20 is NOT a 5-bar fractal swing low;
        # thus hl1=2596.0 (hl1 * 1.002 = 2601.19 < 2605.2), so has_liquidity_sweep is False!
        df_m15.loc[15, ["open", "high", "low", "close"]] = [2602.0, 2604.0, 2600.0, 2601.0]
        df_m15.loc[16, ["open", "high", "low", "close"]] = [2601.0, 2602.0, 2598.0, 2599.0]
        df_m15.loc[17, ["open", "high", "low", "close"]] = [2599.0, 2601.0, 2596.0, 2600.0]
        df_m15.loc[18, ["open", "high", "low", "close"]] = [2600.0, 2604.0, 2599.0, 2603.0]
        df_m15.loc[19, ["open", "high", "low", "close"]] = [2603.0, 2606.5, 2602.0, 2605.8]

        df_d1.loc[df_d1.index[-2], "low"] = 2580.0  # Ensure PDL is below 2605.8 so pdl_swept is False
        kz_time = datetime(2026, 10, 27, 8, 30, tzinfo=timezone.utc)
        res = self.strategy.analyze(
            df_m15,
            symbol="XAUUSD",
            timeframe="M15",
            df_d1=df_d1,
            df_ltf=df_m5,
            current_time=kz_time,
        )
        self.assertEqual(res.signal.direction, "LONG", f"Details: {res.signal.details}")
        self.assertGreaterEqual(res.signal.confidence, 4, "Model B pullback with M5 MSS must receive >= 4 stars!")
        joined_details = "\n".join(res.signal.details)
        self.assertIn("MODEL_B_TREND_OB_PULLBACK", joined_details)
        self.assertNotIn("Подтверждено снятие ликвидности", joined_details)


if __name__ == "__main__":
    unittest.main()


