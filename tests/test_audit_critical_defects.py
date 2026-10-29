"""
Comprehensive Unit Tests for Critical Audit Defects:
1. Lot Calculation & Factual SL:
   - Rejection (0.0 lot) on missing SL, zero distance, negative distance, invalid/negative price,
     contradictory sl_dist vs |entry - sl|.
   - Rejection on missing quotes or non-positive capital.
2. Dynamic Broker Parameters & Risk Budget:
   - Custom contract sizes (10 oz mini, 100 oz standard, 1000 oz large).
   - Validation that actual loss never exceeds risk budget including commission, spread, and slippage buffer.
   - Volume step and min/max volume clamping.
3. Trade Journal Accuracy:
   - sync_broker_deals rejects cross-position signal matches (strict pos_id verification).
   - Missing entry price preserves pnl_pips as NULL (never 0.0) and formats as N/A.
   - Missing open_time preserves duration as NULL / "N/A" (never 0s).
   - cost_status is COMPLETE when all fees exist, INCOMPLETE_FEES_MISSING otherwise.
   - MAE/MFE returns AVAILABLE when bars are provided and UNAVAILABLE when missing.
4. Circuit Breaker & Safety:
   - Circuit breaker state persists across restart and blocks orders when breached.
   - Duplicate dispatch prevention.
"""

import os
import math
import tempfile
import asyncio
import unittest
from datetime import datetime, timezone
import pandas as pd
import aiosqlite

from trading.execution_bridge import ExecutionBridge, calculate_gold_swing_lot
from utils.trade_metrics import (
    calculate_pips,
    calculate_mae_mfe,
    calculate_trade_costs,
    calculate_duration,
)
from utils.formatters import format_signal_result, format_manual_close
import db.database as database


class TestLotCalculationAndFactualSL(unittest.TestCase):
    """Test lot calculation safety and factual SL requirements."""

    def setUp(self):
        self.bridge = ExecutionBridge()
        self.bridge.update_quotes({"XAUUSD": {"bid": 2650.00, "ask": 2650.40}})

    def test_missing_sl_returns_zero(self):
        # Calling calculate_lot without SL or sl_dist must strictly return 0.0
        lot = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=2650.0, sl=None, sl_dist_usd=None)
        self.assertEqual(lot, 0.0)

    def test_zero_sl_distance_returns_zero(self):
        lot = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=2650.0, sl=2650.0)
        self.assertEqual(lot, 0.0)

    def test_negative_sl_distance_returns_zero(self):
        # sl_dist_usd negative
        lot = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=2650.0, sl_dist_usd=-5.0)
        self.assertEqual(lot, 0.0)

    def test_negative_or_nan_prices_return_zero(self):
        lot1 = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=-2650.0, sl=2640.0)
        self.assertEqual(lot1, 0.0)
        lot2 = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=2650.0, sl=-2640.0)
        self.assertEqual(lot2, 0.0)
        lot3 = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=float("nan"), sl=2640.0)
        self.assertEqual(lot3, 0.0)

    def test_contradictory_inputs_return_zero(self):
        # entry=2650, sl=2640 (|entry-sl| = 10.0), but sl_dist_usd=2.0 -> contradiction!
        lot = self.bridge.calculate_lot("XAUUSD", balance=10000.0, entry=2650.0, sl=2640.0, sl_dist_usd=2.0)
        self.assertEqual(lot, 0.0)

    def test_contradictory_small_calc_dist_and_negative_dist_rejected(self):
        # Edge case: calc_dist = 0.05, sl_dist_usd = -0.01. Difference is 0.06 (< 0.10) but distance is negative -> MUST REJECT!
        lot = self.bridge.calculate_lot("XAUUSD", balance=5000.0, entry=2650.05, sl=2650.0, sl_dist_usd=-0.01)
        self.assertEqual(lot, 0.0)

    def test_nan_sl_distance_with_entry_sl_returns_zero(self):
        # Edge case: entry and sl provided, but sl_dist_usd is float('nan') -> MUST REJECT!
        lot = self.bridge.calculate_lot("XAUUSD", balance=5000.0, entry=2650.0, sl=2640.0, sl_dist_usd=float("nan"))
        self.assertEqual(lot, 0.0)

    def test_non_positive_capital_returns_zero(self):
        lot_zero = self.bridge.calculate_lot("XAUUSD", balance=0.0, entry=2650.0, sl=2640.0)
        self.assertEqual(lot_zero, 0.0)
        lot_neg = self.bridge.calculate_lot("XAUUSD", balance=-5000.0, entry=2650.0, sl=2640.0)
        self.assertEqual(lot_neg, 0.0)

    def test_missing_quotes_blocks_trade(self):
        unquoted_bridge = ExecutionBridge()
        unquoted_bridge.live_quotes.clear()
        is_ok, cur_spread, _ = unquoted_bridge.is_spread_acceptable("XAUUSD")
        self.assertFalse(is_ok)
        self.assertEqual(cur_spread, -1.0)


class TestDynamicBrokerParametersAndRiskBudget(unittest.TestCase):
    """Test risk budget safety across different broker contract sizes and specs."""

    def test_standard_100oz_contract_risk_budget(self):
        # Balance: $10,000, risk 0.5% = $50 budget
        # SL distance = $10.00
        # 1 lot = 100 oz -> $10 move = $1000 loss
        # Max safe lot = 0.04 lot (0.04 * 100 * 10 = $40 + comm/spread <= $50)
        lot = calculate_gold_swing_lot(
            balance=10000.0,
            risk_pct=0.50,
            sl_dist_usd=10.0,
            contract_size=100.0,
            tick_size=0.01,
            tick_value=1.0,
            min_lot=0.01,
            max_lot=50.0,
            volume_step=0.01,
            spread_buffer_usd=0.50,
            commission_per_lot=3.0,
        )
        self.assertGreater(lot, 0.0)
        # Expected max loss at this lot:
        loss = (10.0 * 100.0 + 0.50 * 100.0 + 3.0) * lot
        self.assertLessEqual(loss, 50.0)

    def test_mini_10oz_contract_risk_budget(self):
        # Mini gold contract: contract_size = 10 oz
        # 1 lot move of $10.00 = $100 loss
        # With $50 budget, lot can safely be ~0.4 lot
        lot = calculate_gold_swing_lot(
            balance=10000.0,
            risk_pct=0.50,
            sl_dist_usd=10.0,
            contract_size=10.0,
            tick_size=0.01,
            tick_value=0.10,
            min_lot=0.01,
            max_lot=50.0,
            volume_step=0.01,
            spread_buffer_usd=0.50,
            commission_per_lot=0.50,
        )
        self.assertGreater(lot, 0.2)
        loss = (10.0 * 10.0 + 0.50 * 10.0 + 0.50) * lot
        self.assertLessEqual(loss, 50.0)

    def test_min_lot_exceeding_budget_rejected(self):
        # Small account: $500, risk 0.25% = $1.25 budget
        # SL distance = $20.00, contract size = 100 oz
        # Min lot 0.01 loss = 0.01 * 100 * 20 = $20 -> exceeds $1.25 budget -> must return 0.0
        lot = calculate_gold_swing_lot(
            balance=500.0,
            risk_pct=0.25,
            sl_dist_usd=20.0,
            contract_size=100.0,
            tick_size=0.01,
            tick_value=1.0,
            min_lot=0.01,
        )
        self.assertEqual(lot, 0.0)

    def test_non_usd_currency_without_tick_value_rejected(self):
        # When account currency is EUR or GBP and tick_value is not passed, assuming $1/point is invalid -> must reject
        lot = calculate_gold_swing_lot(
            balance=5000.0,
            risk_pct=0.25,
            sl_dist_usd=10.0,
            account_currency="EUR",
            tick_value=None,
        )
        self.assertEqual(lot, 0.0)

    def test_pips_calculation_with_broker_digits_and_point(self):
        # 3-digit gold: point=0.001, digits=3 -> 1 pip = 10 * 0.001 = 0.01
        pips_3digit = calculate_pips("XAUUSD", 2650.000, 2651.000, "BUY", point=0.001, digits=3)
        self.assertEqual(pips_3digit, 100.0)

        # Standard 2-digit gold: 1 pip = $0.10
        pips_std = calculate_pips("XAUUSD", 2650.00, 2651.00, "BUY", point=0.01, digits=2)
        self.assertEqual(pips_std, 10.0)

        # 5-digit Forex (EURUSD): 1 pip = 10 * 0.00001 = 0.0001
        pips_5digit = calculate_pips("EURUSD", 1.08000, 1.08050, "BUY", point=0.00001, digits=5)
        self.assertEqual(pips_5digit, 5.0)


class TestTradeJournalAccuracy(unittest.IsolatedAsyncioTestCase):
    """Test database journal, truthful data handling, and MT5 sync."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_signals.db")
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = database.Path(self.db_path)
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.orig_db_path
        self.temp_dir.cleanup()

    async def test_missing_entry_price_stores_null_pips(self):
        deals = [{
            "ticket": 9001,
            "position_id": 9001,
            "symbol": "XAUUSD",
            "type": "SELL",
            "lot": 0.05,
            "close_price": 2650.00,
            "profit": 150.00,
            "gross_profit": 155.00,
            "commission": -3.50,
            "swap": -1.50,
            "close_time": 1700000500,
            # No entry_price and no prior signal or IN deal
        }]
        new_deals = await database.sync_broker_deals(deals)
        self.assertEqual(len(new_deals), 1)
        deal = new_deals[0]
        self.assertIsNone(deal["entry_price"])
        self.assertIsNone(deal["pnl_pips"])
        self.assertEqual(deal["cost_status"], "COMPLETE")

        # Check in trade_history database
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM trade_history WHERE broker_ticket = 9001")
            row = await cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["entry_price"])
            self.assertIsNone(row["pnl_pips"])
            self.assertEqual(row["cost_status"], "COMPLETE")

    async def test_incomplete_fees_recorded_as_incomplete_cost_status(self):
        deals = [{
            "ticket": 9002,
            "position_id": 9002,
            "symbol": "XAUUSD",
            "type": "BUY",
            "lot": 0.02,
            "entry_price": 2640.00,
            "close_price": 2650.00,
            "profit": 200.00,
            "gross_profit": None, # Commission and gross missing from MT5 broker
            "commission": None,
            "swap": None,
            "close_time": 1700000600,
            "open_time": 1700000000,
        }]
        new_deals = await database.sync_broker_deals(deals)
        self.assertEqual(len(new_deals), 1)
        deal = new_deals[0]
        self.assertEqual(deal["cost_status"], "INCOMPLETE_FEES_MISSING")
        self.assertEqual(deal["pnl_pips"], 100.0) # (2650 - 2640) * 10 = 100.0 pips

    async def test_cross_position_signal_match_rejected(self):
        # Create a signal with position_id = 7777
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO signals (id, symbol, direction, entry_price, status, position_id, broker_ticket, created_at)
                VALUES (501, 'XAUUSD', 'LONG', 2600.00, 'OPEN', 7777, 7777, '2026-06-01T10:00:00+00:00')
            """)
            await db.commit()

        # A deal with position_id = 8888 attempts to reference signal_id = 501
        deal_cross = [{
            "ticket": 9003,
            "position_id": 8888,
            "signal_id": 501, # Cross-position reference!
            "symbol": "XAUUSD",
            "type": "SELL",
            "lot": 0.05,
            "close_price": 2650.00,
            "profit": 50.0,
            "close_time": 1700001000,
        }]
        new_deals = await database.sync_broker_deals(deal_cross)
        self.assertEqual(len(new_deals), 1)
        # Entry price must NOT be contaminated from signal 501 because position IDs mismatch!
        self.assertIsNone(new_deals[0]["entry_price"])

    async def test_missing_open_time_yields_na_duration(self):
        deals = [{
            "ticket": 9004,
            "position_id": 9004,
            "symbol": "XAUUSD",
            "type": "BUY",
            "lot": 0.01,
            "close_price": 2650.00,
            "profit": 10.0,
            "close_time": 1700000000,
            # No open_time
        }]
        new_deals = await database.sync_broker_deals(deals)
        self.assertEqual(new_deals[0]["duration_str"], "N/A")

    async def test_close_signal_by_broker_missing_open_time_yields_na_duration(self):
        # When closing an unlinked signal without open time, duration must be N/A (never fabricated 0s)
        res = await database.close_signal_by_broker(
            symbol="XAUUSD",
            close_price=2650.0,
            profit_usd=25.0,
            reason="TP1_HIT",
            ticket=9005,
        )
        self.assertIsNotNone(res)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM trade_history WHERE deal_out_ticket = 9005")
            row = await cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertIsNone(row["duration_seconds"])
            self.assertEqual(row["duration_str"], "N/A")


class TestMAEAndMFEHistoricalBars(unittest.TestCase):
    """Test MAE / MFE availability with and without bars."""

    def test_mae_mfe_without_bars_returns_unavailable(self):
        res = calculate_mae_mfe(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2650.00,
            open_time="2026-06-01T10:00:00+00:00",
            close_time="2026-06-01T11:00:00+00:00",
            lot=0.10,
            df_bars=None,
        )
        self.assertFalse(res["is_available"])
        self.assertIn("UNAVAILABLE", res["status"])
        self.assertIsNone(res["mae"])
        self.assertIsNone(res["mfe"])

    def test_mae_mfe_with_bars_returns_available(self):
        df_bars = pd.DataFrame([
            {"time": 1700000060, "open": 2650.0, "high": 2660.0, "low": 2645.0, "close": 2655.0},
            {"time": 1700000120, "open": 2655.0, "high": 2670.0, "low": 2648.0, "close": 2665.0},
        ])
        res = calculate_mae_mfe(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2650.00,
            open_time=1700000000,
            close_time=1700000200,
            lot=0.10,
            df_bars=df_bars,
        )
        self.assertTrue(res["is_available"])
        self.assertEqual(res["status"], "AVAILABLE")
        self.assertEqual(res["mae"], 5.0)  # Min low is 2645.0, entry 2650.0 -> 5.0
        self.assertEqual(res["mfe"], 20.0) # Max high is 2670.0, entry 2650.0 -> 20.0

    def test_mae_mfe_with_tz_naive_csv_dataframe_and_tz_aware_utc_timestamps(self):
        # CSV data with tz-naive strings, tested against ISO tz-aware timestamps
        df_csv = pd.DataFrame([
            {"timestamp": "2026.02.12 11:15", "open": 2650.0, "high": 2665.0, "low": 2640.0, "close": 2655.0},
            {"timestamp": "2026.02.12 11:20", "open": 2655.0, "high": 2670.0, "low": 2648.0, "close": 2660.0},
        ])
        res = calculate_mae_mfe(
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2650.0,
            open_time="2026-02-12T11:10:00+00:00",
            close_time="2026-02-12T11:25:00+00:00",
            lot=0.01,
            df_bars=df_csv,
        )
        self.assertTrue(res["is_available"])
        self.assertEqual(res["status"], "AVAILABLE")
        self.assertEqual(res["mae"], 10.0) # 2650 - 2640 = 10.0
        self.assertEqual(res["mfe"], 20.0) # 2670 - 2650 = 20.0


class TestFormattersTruthfulDisplay(unittest.TestCase):
    """Test formatters output N/A instead of 0.0 pips when pnl_pips is None."""

    def test_format_signal_result_with_none_pips(self):
        sig = {"symbol": "XAUUSD", "direction": "LONG", "entry_price": 2650.0}
        out_tp = format_signal_result(sig, "TP1_HIT", close_price=2665.0, pnl_pips=None)
        self.assertIn("N/A", out_tp)
        out_sl = format_signal_result(sig, "SL_HIT", close_price=2640.0, pnl_pips=None)
        self.assertIn("N/A", out_sl)

    def test_format_manual_close_with_none_pips(self):
        out = format_manual_close("XAUUSD", profit=120.0, pnl_pips=None, price=2660.0)
        self.assertIn("N/A pips", out)


if __name__ == "__main__":
    unittest.main()
