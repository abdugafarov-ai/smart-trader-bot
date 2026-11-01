"""
Unit tests for Phase 2 (Risk Management Engine) and Phase 3 (Strategy Upgrades & Quarantine)
under FundingPips $5,000 Challenge Rules.
"""

import unittest
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

from trading.execution_bridge import ExecutionBridge
from scripts.intraday_gold_engine import IntradaySimulator, calculate_atr


class TestPhase2Phase3Upgrades(unittest.TestCase):
    def setUp(self):
        self.bridge = ExecutionBridge()
        self.bridge.enabled = True
        # Tuesday 10:00 UTC (active London trading session)
        self.trading_dt = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        self.bridge.mt5_telemetry = {
            "balance": 5000.0,
            "equity": 5000.0,
            "margin_free": 5000.0,
            "connected": True,
            "last_ping": datetime.now(timezone.utc),
            "positions": [],
            "orders": [],
        }
        self.bridge.live_quotes["XAUUSD"] = {
            "bid": 2650.00,
            "ask": 2650.25,
            "time": self.trading_dt,
        }
        self.bridge.symbol_specs["XAUUSD"] = {
            "contract_size": 100.0,
            "tick_size": 0.01,
            "tick_value": 1.0,
            "min_lot": 0.01,
            "max_lot": 100.0,
            "lot_step": 0.01,
            "broker_confirmed": True,
        }
        self.bridge.today_stats_cache = {
            "start_balance": 5000.0,
            "closed_pnl": 0.0,
            "net_pnl": 0.0,
            "trades_count": 0,
        }
        self.bridge.peak_equity = 5000.0
        self.bridge.consecutive_losses = 0
        self.bridge.today_consecutive_losses = 0
        self.bridge.consecutive_loss_paused = False
        self.bridge.consecutive_loss_reason = ""

    def test_dynamic_risk_scaling_consecutive_losses(self):
        """
        Verify get_dynamic_risk_pct scales down risk by:
        - 0 losses: 0.25%
        - 1 loss: 0.25%
        - 2 losses: 0.1875% (25% reduction)
        - 3 losses: 0.125% (50% reduction)
        """
        base_risk = 0.25

        # 0 losses
        r0 = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r0, 0.25, places=4)

        # 1 loss
        self.bridge.consecutive_losses = 1
        self.bridge.today_consecutive_losses = 1
        r1 = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r1, 0.25, places=4)

        # 2 losses
        self.bridge.consecutive_losses = 2
        self.bridge.today_consecutive_losses = 2
        r2 = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r2, 0.25 * 0.75, places=4)

        # 3 losses
        self.bridge.consecutive_losses = 3
        self.bridge.today_consecutive_losses = 3
        r3 = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r3, 0.25 * 0.50, places=4)

    def test_dynamic_risk_scaling_drawdowns(self):
        """
        Verify get_dynamic_risk_pct scales down risk when daily DD >= 1.5%
        or total DD >= 6.0%.
        """
        base_risk = 0.25

        # Daily DD >= 1.5% ($80 drop from $5000)
        self.bridge.today_stats_cache["start_balance"] = 5000.0
        self.bridge.today_stats_cache["net_pnl"] = -80.0
        self.bridge.mt5_telemetry["equity"] = 4920.0  # DD = 1.6%
        r_daily_dd = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r_daily_dd, 0.25 * 0.50, places=4)

        # Reset daily, test Total DD >= 6.0% ($350 drop from peak $5000)
        self.bridge.today_stats_cache["start_balance"] = 4650.0
        self.bridge.today_stats_cache["net_pnl"] = 0.0
        self.bridge.peak_equity = 5000.0
        self.bridge.mt5_telemetry["balance"] = 4650.0
        self.bridge.mt5_telemetry["equity"] = 4650.0  # Total DD = 7.0%
        r_tot_dd = self.bridge.get_dynamic_risk_pct(base_risk=base_risk)
        self.assertAlmostEqual(r_tot_dd, 0.25 * 0.50, places=4)

    def test_consecutive_loss_pause_blocks_trading(self):
        """
        Verify consecutive_loss_paused blocks new position opening
        after 3 consecutive losses until reset.
        """
        # Initially can open during active market session
        self.assertFalse(self.bridge.consecutive_loss_paused)
        can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.trading_dt)
        self.assertTrue(can_open, f"Expected can_open to be True, got {can_open}: {reason}")

        # Record 2 losses
        self.bridge.record_trade_result(-12.50)
        self.bridge.record_trade_result(-12.50)
        self.assertEqual(self.bridge.consecutive_losses, 2)
        self.assertFalse(self.bridge.consecutive_loss_paused)

        # Record 3rd loss -> trigger pause
        self.bridge.record_trade_result(-10.00)
        self.assertEqual(self.bridge.consecutive_losses, 3)
        self.assertTrue(self.bridge.consecutive_loss_paused)

        # Verify can_open_new_position rejects
        can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.trading_dt)
        self.assertFalse(can_open)
        self.assertIn("серия", reason.lower())

        # Daily pause remains active for the remainder of the trading day
        self.assertTrue(self.bridge.consecutive_loss_paused)
        can_open, _ = self.bridge.can_open_new_position("XAUUSD", now_dt=self.trading_dt)
        self.assertFalse(can_open)

        # On next day rollover (00:00 EET / new trading day), pause resets
        self.bridge.today_consecutive_losses = 0
        self.bridge.consecutive_loss_paused = False
        self.bridge.consecutive_loss_reason = ""

        # Can trade again on new trading day
        next_day_dt = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
        self.bridge.live_quotes["XAUUSD"]["time"] = next_day_dt
        can_open, _ = self.bridge.can_open_new_position("XAUUSD", now_dt=next_day_dt)
        self.assertTrue(can_open)

    def test_validate_sl_update_anti_widening(self):
        """
        Verify validate_sl_update rejects any Stop Loss widening
        and strictly allows only moves in favor of the trade (trailing / BE).
        """
        # BUY: entry = 2650.0, current SL = 2640.0
        # Widening to 2635.0 (further from price) must be REJECTED
        ok = self.bridge.validate_sl_update(
            current_sl=2640.0,
            new_sl=2635.0,
            pos_type="BUY"
        )
        self.assertFalse(ok)

        # Moving to 2645.0 (closer to / beyond entry) must be ACCEPTED
        ok = self.bridge.validate_sl_update(
            current_sl=2640.0,
            new_sl=2645.0,
            pos_type="BUY"
        )
        self.assertTrue(ok)

        # SELL: entry = 2650.0, current SL = 2660.0
        # Widening to 2665.0 (higher / worse for sell) must be REJECTED
        ok = self.bridge.validate_sl_update(
            current_sl=2660.0,
            new_sl=2665.0,
            pos_type="SELL"
        )
        self.assertFalse(ok)

        # Moving to 2655.0 (closer to / beyond entry) must be ACCEPTED
        ok = self.bridge.validate_sl_update(
            current_sl=2660.0,
            new_sl=2655.0,
            pos_type="SELL"
        )
        self.assertTrue(ok)

    def test_legacy_mt4_quarantine(self):
        """
        Verify SmartTraderBridge.mq4 is removed from active trading/ea/
        and safely archived inside trading/ea/legacy_mt4_quarantine/ with README.
        """
        root_dir = Path(__file__).resolve().parent.parent
        active_mq4 = root_dir / "trading" / "ea" / "SmartTraderBridge.mq4"
        quarantined_mq4 = root_dir / "trading" / "ea" / "legacy_mt4_quarantine" / "SmartTraderBridge.mq4"
        readme = root_dir / "trading" / "ea" / "legacy_mt4_quarantine" / "README.md"

        self.assertFalse(active_mq4.exists(), "SmartTraderBridge.mq4 must NOT exist in active EA folder!")
        self.assertTrue(quarantined_mq4.exists(), "SmartTraderBridge.mq4 must exist in quarantine folder!")
        self.assertTrue(readme.exists(), "README.md must explain MT4 quarantine reasons!")
        self.assertGreater(readme.stat().st_size, 100)

    def test_use_strong_trend_filter_impact(self):
        """
        Verify IntradaySimulator supports use_strong_trend_filter flag
        and that turning it ON filters out losing cascade trades in strong uptrends.
        """
        root_dir = Path(__file__).resolve().parent.parent
        csv_m5 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_M5.csv"
        csv_m15 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_M15.csv"
        csv_h1 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_H1.csv"

        if not csv_m5.exists() or not csv_m15.exists() or not csv_h1.exists():
            self.skipTest("Gold historical CSV files not available for simulation test")

        m5 = pd.read_csv(csv_m5)
        m15 = pd.read_csv(csv_m15)
        h1 = pd.read_csv(csv_h1)

        m15["atr"] = calculate_atr(m15, 14)
        h1["atr"] = calculate_atr(h1, 14)

        # Run with filter OFF (Baseline)
        sim_off = IntradaySimulator(
            m5, m15, h1,
            model="model_a_plus_b",
            risk_pct=0.005,
            lot_mode="fixed",
            fixed_lot=0.02,
            use_strong_trend_filter=False
        )
        res_off = sim_off.run()

        # Run with filter ON (Upgraded)
        sim_on = IntradaySimulator(
            m5, m15, h1,
            model="model_a_plus_b",
            risk_pct=0.005,
            lot_mode="fixed",
            fixed_lot=0.02,
            use_strong_trend_filter=True
        )
        res_on = sim_on.run()

        # Upgraded must show higher profit factor, higher net profit, higher expectancy R
        self.assertGreater(res_on["profit_factor"], res_off["profit_factor"])
        self.assertGreater(res_on["net_profit_usd"], res_off["net_profit_usd"])
        self.assertGreater(res_on["expectancy_r"], res_off["expectancy_r"])
        self.assertLess(res_on["total_trades"], res_off["total_trades"])
        # Total DD must remain well below FundingPips 12% total limit ($600)
        self.assertLess(res_on["max_total_dd_usd"], 600.0)

    def test_record_trade_result_ticket_deduplication(self):
        """
        Verify record_trade_result prevents duplicate processing of the same MT5 deal ticket,
        correctly counts consecutive losses, and resets on profit.
        """
        self.bridge.consecutive_losses = 0
        self.bridge.today_consecutive_losses = 0
        self.bridge.consecutive_loss_paused = False
        self.bridge.processed_deal_tickets.clear()

        # Deal #1001: Loss of -$12.50
        self.bridge.record_trade_result(-12.50, ticket=1001)
        self.assertEqual(self.bridge.consecutive_losses, 1)
        self.assertEqual(self.bridge.today_consecutive_losses, 1)

        # Duplicate delivery of Deal #1001 (e.g. repeated EA poll or network retry)
        self.bridge.record_trade_result(-12.50, ticket=1001)
        self.assertEqual(self.bridge.consecutive_losses, 1, "Duplicate ticket must NOT increment loss count!")
        self.assertEqual(self.bridge.today_consecutive_losses, 1)

        # Deal #1002: Loss of -$12.50 -> 2nd loss
        self.bridge.record_trade_result(-12.50, ticket=1002)
        self.assertEqual(self.bridge.consecutive_losses, 2)
        self.assertFalse(self.bridge.consecutive_loss_paused)

        # Deal #1003: Loss of -$10.00 -> 3rd loss triggers pause
        self.bridge.record_trade_result(-10.00, ticket=1003)
        self.assertEqual(self.bridge.consecutive_losses, 3)
        self.assertTrue(self.bridge.consecutive_loss_paused)

        # Deal #1004: Winning trade resets consecutive losses
        self.bridge.record_trade_result(25.00, ticket=1004)
        self.assertEqual(self.bridge.consecutive_losses, 0)
        self.assertEqual(self.bridge.today_consecutive_losses, 0)

    def test_circuit_breaker_db_persistence_and_restore(self):
        """
        Verify circuit breaker state (consecutive_loss_paused, peak_equity, consecutive_losses)
        persists to SQLite and restores faithfully after simulated server reboot.
        """
        import asyncio
        from unittest.mock import patch, AsyncMock
        from utils.broker_time import get_broker_today_date_str

        async def run_async_test():
            stored_settings = {}

            async def mock_set(key, val):
                stored_settings[key] = str(val)

            async def mock_get(key, default=""):
                return stored_settings.get(key, default)

            with patch("db.database.set_bot_setting", new_callable=AsyncMock, side_effect=mock_set), \
                 patch("db.database.get_bot_setting", new_callable=AsyncMock, side_effect=mock_get):

                # 1. Populate bridge with tripped circuit breaker & peak equity
                self.bridge.peak_equity = 5320.50
                self.bridge.consecutive_losses = 3
                self.bridge.today_consecutive_losses = 3
                self.bridge.consecutive_loss_paused = True
                self.bridge.consecutive_loss_reason = "Test 3-loss pause"
                today_str = get_broker_today_date_str()
                self.bridge.current_trade_date = today_str

                # Persist to database
                await self.bridge.persist_circuit_breaker_state()

                # Verify entries were saved
                self.assertEqual(stored_settings.get("consecutive_loss_paused"), "true")
                self.assertEqual(stored_settings.get("consecutive_losses"), "3")
                self.assertEqual(stored_settings.get("today_consecutive_losses"), "3")
                self.assertEqual(stored_settings.get("peak_equity"), "5320.50")
                self.assertEqual(stored_settings.get("consecutive_loss_reason"), "Test 3-loss pause")

                # 2. Simulate fresh reboot with a new ExecutionBridge instance
                fresh_bridge = ExecutionBridge()
                self.assertEqual(fresh_bridge.consecutive_losses, 0)
                self.assertFalse(fresh_bridge.consecutive_loss_paused)

                # Load settings from mock DB
                await fresh_bridge.load_settings_from_db()

                # Verify all state restored
                self.assertEqual(fresh_bridge.peak_equity, 5320.50)
                self.assertEqual(fresh_bridge.consecutive_losses, 3)
                self.assertEqual(fresh_bridge.today_consecutive_losses, 3)
                self.assertTrue(fresh_bridge.consecutive_loss_paused)
                self.assertEqual(fresh_bridge.consecutive_loss_reason, "Test 3-loss pause")

        asyncio.run(run_async_test())

    def test_bridge_get_orders_fail_closed_and_dynamic_risk(self):
        """
        Verify webapp server bridge_get_orders immediately fails closed
        when consecutive_loss_paused is True, and propagates dynamic risk.
        """
        import asyncio
        import json
        from unittest.mock import patch, MagicMock, AsyncMock
        from webapp.server import bridge_get_orders
        from trading.execution_bridge import bridge_manager

        async def run_server_test():
            req = MagicMock()
            req.headers = {"X-API-Key": ""}
            req.remote = "127.0.0.1"
            req.method = "GET"
            req.query = {}

            with patch("db.database.get_today_broker_pnl", new_callable=AsyncMock) as mock_pnl:
                mock_pnl.return_value = {"total_pnl": 0.0, "deals_count": 0}

                # Condition 1: Normal operational state (0 losses)
                bridge_manager.consecutive_loss_paused = False
                bridge_manager.consecutive_losses = 0
                bridge_manager.today_consecutive_losses = 0
                bridge_manager.daily_loss_locked = False
                bridge_manager.equity_hard_stop_triggered = False

                res_normal = await bridge_get_orders(req)
                body_normal = json.loads(res_normal.text)
                self.assertEqual(body_normal["risk_percent"], 0.25)
                self.assertFalse(body_normal["consecutive_loss_paused"])

                # Condition 2: Consecutive loss paused
                bridge_manager.consecutive_loss_paused = True
                bridge_manager.consecutive_losses = 3
                bridge_manager.today_consecutive_losses = 3

                res_paused = await bridge_get_orders(req)
                body_paused = json.loads(res_paused.text)
                self.assertFalse(body_paused["autotrade_enabled"], "Must fail closed!")
                self.assertTrue(body_paused["consecutive_loss_paused"], "Must flag consecutive loss pause!")
                self.assertEqual(body_paused["orders"], [], "Orders must be empty when paused!")
                # Dynamic risk scaled down to 50% of 0.25% = 0.125%
                self.assertAlmostEqual(body_paused["risk_percent"], 0.125, places=3)

                # Reset bridge_manager state
                bridge_manager.consecutive_loss_paused = False
                bridge_manager.consecutive_losses = 0
                bridge_manager.today_consecutive_losses = 0

        asyncio.run(run_server_test())

    def test_h1_timeframe_propagation_in_handlers(self):
        """
        Verify run_multi_tf_analysis in bot/handlers.py propagates df_h1
        into strategy.analyze to activate Strong Trend Cascade Guard in live bot.
        """
        import inspect
        from bot.handlers import run_multi_tf_analysis
        src = inspect.getsource(run_multi_tf_analysis)
        self.assertIn('df_h1=dfs_by_tf.get("H1")', src)

    def test_model_a_and_model_b_ablation_standalone(self):
        """
        Verify standalone execution of Model A alone and Model B alone in IntradaySimulator.
        Both models must execute successfully and return valid trade metrics.
        """
        root_dir = Path(__file__).resolve().parent.parent
        csv_m5 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_M5.csv"
        csv_m15 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_M15.csv"
        csv_h1 = root_dir / "data" / "gold_candles" / "candles_XAUUSD_H1.csv"

        if not csv_m5.exists() or not csv_m15.exists() or not csv_h1.exists():
            self.skipTest("Gold historical CSV files not available for simulation test")

        m5 = pd.read_csv(csv_m5)
        m15 = pd.read_csv(csv_m15)
        h1 = pd.read_csv(csv_h1)

        m15["atr"] = calculate_atr(m15, 14)
        h1["atr"] = calculate_atr(h1, 14)

        # 1. Model A alone
        sim_a = IntradaySimulator(
            m5, m15, h1,
            model="model_a",
            risk_pct=0.0025,
            lot_mode="dynamic",
            use_strong_trend_filter=True
        )
        res_a = sim_a.run()
        self.assertGreater(res_a["total_trades"], 0)
        self.assertIn("profit_factor", res_a)
        self.assertIn("three_way_split", res_a)

        # 2. Model B alone
        sim_b = IntradaySimulator(
            m5, m15, h1,
            model="model_b",
            risk_pct=0.0025,
            lot_mode="dynamic",
            use_strong_trend_filter=True
        )
        res_b = sim_b.run()
        self.assertGreater(res_b["total_trades"], 0)
        self.assertIn("profit_factor", res_b)
        self.assertIn("three_way_split", res_b)


if __name__ == "__main__":
    unittest.main()
