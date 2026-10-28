"""
Tests for Trade History Tracking and Audit Journal.
Verifies:
1. Distinct entry_price and close_price (no false equality).
2. Reliable position/ticket correlation without cross-contamination.
3. Partial position close handling.
4. Cost tracking (gross, commission, swap, slippage) and telemetry verification.
5. Missing data handling (graceful N/A, never fabricated numbers).
6. R-multiple calculation (planned RR and actual R).
7. MAE / MFE calculation (with bars vs missing bars status).
8. Trading session identification and duration tracking.
9. Secure export (CSV & JSON sanitization, secret/account masking).
10. Strict record type segregation (LIVE, DEMO, TEST_SIGNAL, BACKTEST).
11. Webapp API endpoints and history routes.
"""

import os
import json
import csv
import io
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pandas as pd
import aiosqlite

import db.database as database
from utils.trade_metrics import (
    calculate_planned_rr,
    calculate_actual_r,
    calculate_trade_costs,
    calculate_duration,
    calculate_mae_mfe,
    detect_trading_session,
)
from utils.trade_export import (
    sanitize_trade_record,
    export_trades_to_csv,
    export_trades_to_json,
)


class TestTradeMetrics(unittest.TestCase):
    """Unit tests for mathematical trade metrics, duration, MAE/MFE, and sessions."""

    def test_planned_rr_long_and_short(self):
        # Long: entry=2000, sl=1990 (risk=10), tp=2030 (reward=30) -> RR=3.0
        rr_long = calculate_planned_rr(2000.0, 1990.0, 2030.0, "LONG")
        self.assertEqual(rr_long, 3.0)

        # Short: entry=2000, sl=2010 (risk=10), tp=1970 (reward=30) -> RR=3.0
        rr_short = calculate_planned_rr(2000.0, 2010.0, 1970.0, "SHORT")
        self.assertEqual(rr_short, 3.0)

        # Invalid: missing sl or tp
        self.assertIsNone(calculate_planned_rr(2000.0, None, 2030.0, "LONG"))
        self.assertIsNone(calculate_planned_rr(2000.0, 2000.0, 2030.0, "LONG"))

    def test_actual_r_from_usd_and_pips(self):
        # 1. Using dollar risk: risk=$25, net_pnl=+$75 -> +3.0R
        r_win = calculate_actual_r(net_pnl=75.0, risk_usd=25.0)
        self.assertEqual(r_win, 3.0)

        # Loss: risk=$25, net_pnl=-$25 -> -1.0R
        r_loss = calculate_actual_r(net_pnl=-25.0, risk_usd=25.0)
        self.assertEqual(r_loss, -1.0)

        # 2. Using price distance: entry=2000, sl=1990 (risk=10), exit=2025 -> +2.5R
        r_dist = calculate_actual_r(entry_price=2000.0, close_price=2025.0, initial_sl=1990.0, direction="LONG")
        self.assertEqual(r_dist, 2.5)

        # Missing risk: returns None, never guesses
        self.assertIsNone(calculate_actual_r())

    def test_trade_costs_with_full_and_partial_telemetry(self):
        # Full verified telemetry
        costs = calculate_trade_costs(gross_profit=100.0, commission=-3.50, swap=-1.20, slippage_pips=0.5)
        self.assertEqual(costs["gross_profit"], 100.0)
        self.assertEqual(costs["commission"], -3.50)
        self.assertEqual(costs["swap"], -1.20)
        self.assertEqual(costs["slippage"], 0.5)
        self.assertEqual(costs["net_pnl"], 95.30)

        # Partial telemetry: missing commission & swap -> marked None (N/A), net_pnl uses gross if verified
        partial = calculate_trade_costs(gross_profit=50.0, commission=None, swap=None)
        self.assertEqual(partial["gross_profit"], 50.0)
        self.assertIsNone(partial["commission"])
        self.assertIsNone(partial["swap"])
        self.assertEqual(partial["net_pnl"], 50.0)

    def test_duration_calculation(self):
        # 1 hour 15 min 30 sec = 4530 sec
        open_ts = "2026-06-15T10:00:00+00:00"
        close_ts = "2026-06-15T11:15:30+00:00"
        dur_sec, dur_str = calculate_duration(open_ts, close_ts)
        self.assertEqual(dur_sec, 4530)
        self.assertEqual(dur_str, "1h 15m 30s")

        # Missing timestamp: returns (None, "N/A")
        dur_none_sec, dur_none_str = calculate_duration(None, close_ts)
        self.assertIsNone(dur_none_sec)
        self.assertEqual(dur_none_str, "N/A")

    def test_mae_mfe_with_bars(self):
        # Create 3 candles for LONG trade (entry=2000.0, close=2020.0, pip_size=0.1 for XAUUSD)
        # Candle 1: low=1995 (adverse -5.0 = -50 pips), high=2010 (favorable +10.0 = +100 pips)
        # Candle 2: low=1998, high=2025 (favorable +25.0 = +250 pips)
        # Pip multiplier for XAUUSD is 10.0 (pip_size=0.1)
        bars = pd.DataFrame([
            {"time": 1700000060, "open": 2000.0, "high": 2010.0, "low": 1995.0, "close": 2005.0},
            {"time": 1700000120, "open": 2005.0, "high": 2025.0, "low": 1998.0, "close": 2020.0},
        ])
        res = calculate_mae_mfe(
            entry_price=2000.0,
            close_price=2020.0,
            direction="LONG",
            symbol="XAUUSD",
            open_time=1700000000,
            close_time=1700000200,
            bars=bars,
        )
        self.assertEqual(res["status"], "AVAILABLE")
        self.assertEqual(res["mae_pips"], 50.0)
        self.assertEqual(res["mfe_pips"], 250.0)

    def test_mae_mfe_missing_bars_status(self):
        res = calculate_mae_mfe(
            entry_price=2000.0,
            close_price=2020.0,
            direction="LONG",
            symbol="XAUUSD",
            open_time=1700000000,
            close_time=1700000200,
            bars=None,
        )
        self.assertIsNone(res["mae_pips"])
        self.assertIsNone(res["mfe_pips"])
        self.assertIn("UNAVAILABLE: requires historical tick/bar data for XAUUSD", res["status"])

    def test_trading_session_detection(self):
        # 09:00 UTC is London session
        dt_london = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(detect_trading_session(dt_london), "London")

        # 14:00 UTC is London-NY Overlap
        dt_overlap = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(detect_trading_session(dt_overlap), "London-NY Overlap")

        # 18:00 UTC is New York session
        dt_ny = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(detect_trading_session(dt_ny), "New York")

        # 03:00 UTC is Asian session
        dt_asian = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(detect_trading_session(dt_asian), "Asian")


class TestSecureExport(unittest.TestCase):
    """Tests for secure export sanitization, CSV and JSON generation without secret leaks."""

    def test_sanitization_masks_account_and_removes_secrets(self):
        raw_trade = {
            "id": 1,
            "position_id": 1001,
            "account": "12345678",
            "api_key": "secret_key_abc",
            "token": "bot_token_xyz",
            "password": "broker_password",
            "symbol": "XAUUSD",
            "entry_price": 2000.0,
            "close_price": 2025.0,
            "net_pnl": 250.0,
        }
        sanitized = sanitize_trade_record(raw_trade)
        # Secrets removed
        self.assertNotIn("api_key", sanitized)
        self.assertNotIn("token", sanitized)
        self.assertNotIn("password", sanitized)
        # Account masked
        self.assertEqual(sanitized["account"], "****5678")
        # Trading data intact
        self.assertEqual(sanitized["symbol"], "XAUUSD")
        self.assertEqual(sanitized["entry_price"], 2000.0)

    def test_csv_export_format_and_cleanliness(self):
        trades = [
            {
                "id": 1,
                "position_id": 1001,
                "order_id": 2001,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "record_type": "LIVE",
                "lot": 0.05,
                "entry_price": 2000.0,
                "close_price": 2025.0,
                "initial_sl": 1990.0,
                "final_sl": 2005.0,
                "initial_tp": 2030.0,
                "final_tp": 2030.0,
                "gross_profit": 125.0,
                "commission": -3.50,
                "swap": -1.00,
                "slippage": 0.2,
                "net_pnl": 120.50,
                "planned_rr": 3.0,
                "actual_r": 2.41,
                "strategy": "Institutional SMC/ICT",
                "trading_session": "London",
                "result": "TP1_HIT",
                "account": "88776655",
                "api_key": "should_be_stripped",
            }
        ]
        csv_str = export_trades_to_csv(trades)
        self.assertNotIn("should_be_stripped", csv_str)
        self.assertNotIn("88776655", csv_str)
        self.assertIn("****6655", csv_str)

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Symbol"], "XAUUSD")
        self.assertEqual(rows[0]["Entry_Price"], "2000.00")
        self.assertEqual(rows[0]["Close_Price"], "2025.00")
        self.assertEqual(rows[0]["Net_PnL_USD"], "120.50")
        self.assertEqual(rows[0]["Account"], "****6655")

    def test_json_export_validity(self):
        trades = [{"id": 1, "symbol": "XAUUSD", "net_pnl": 50.0, "secret": "leaked_secret"}]
        json_str = export_trades_to_json(trades)
        self.assertNotIn("leaked_secret", json_str)
        parsed = json.loads(json_str)
        self.assertEqual(len(parsed["trades"]), 1)
        self.assertEqual(parsed["trades"][0]["symbol"], "XAUUSD")

    def test_forex_precision_preservation_in_csv(self):
        """Forex prices with 5 decimal places (e.g. 1.08543) must NOT be truncated to 2 decimals."""
        trades = [{
            "id": 10,
            "position_id": 9001,
            "symbol": "EURUSD",
            "direction": "BUY",
            "lot": 0.1,
            "entry_price": 1.08543,
            "close_price": 1.09124,
            "net_pnl": 58.10,
            "record_type": "LIVE",
        }]
        csv_str = export_trades_to_csv(trades)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Entry_Price"], "1.08543")
        self.assertEqual(rows[0]["Close_Price"], "1.09124")

    def test_embedded_tokens_and_passwords_sanitized(self):
        """Embedded Telegram tokens and broker passwords inside comments/telemetry must be redacted."""
        trade = {
            "id": 11,
            "symbol": "EURUSD",
            "comment": "Trade initiated by 123456789:ABCdefGHIjklMNOpqrSTUvwxyz123456789 with password=SecretBrokerPassword!",
            "raw_telemetry": '{"token": "987654321:XYZabc1234567890abcdefghijklmnopqr", "pass": "secret123"}',
            "account": "65432109",
            "net_pnl": 20.0,
        }
        sanitized = sanitize_trade_record(trade)
        self.assertNotIn("123456789:ABCdefGHIjklMNOpqrSTUvwxyz123456789", sanitized["comment"])
        self.assertNotIn("SecretBrokerPassword!", sanitized["comment"])
        self.assertIn("[REDACTED]", sanitized["comment"])
        self.assertEqual(sanitized["account"], "****2109")


class TestDatabaseTradeJournal(unittest.IsolatedAsyncioTestCase):
    """Integration tests for SQLite trade history tracking and audit journal."""

    async def asyncSetUp(self):
        # Create a unique temporary database file for isolation
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(self.temp_db_fd)
        database.DB_PATH = database.Path(self.temp_db_path)
        await database.init_db()

    async def asyncTearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    async def test_distinct_entry_and_close_prices_in_get_recent_signals(self):
        """Requirement 1: Closed trades must NOT have identical entry_price and close_price."""
        # 1. Insert a signal with entry_price=2000.0 and position_id=555
        sig_id = await database.save_signal(
            symbol="XAUUSD",
            direction="BUY",
            entry=2000.0,
            sl=1990.0,
            tp1=2030.0,
            status="OPEN",
            record_type="LIVE",
            position_id=555,
        )

        # 2. Simulate broker exit deal with ticket=901, position_id=555, close price=2025.0
        broker_deals_batch = [
            {
                "ticket": 901,
                "position_id": 555,
                "order_id": 401,
                "symbol": "XAUUSD",
                "type": "SELL",
                "lot": 0.05,
                "price": 2025.0,
                "close_price": 2025.0,
                "entry_price": 2000.0,
                "profit": 125.0,
                "gross_profit": 125.0,
                "commission": -3.50,
                "swap": 0.0,
                "time": int(datetime.now(timezone.utc).timestamp()),
                "comment": "TP_HIT",
                "magic": 888001,
            }
        ]
        await database.sync_broker_deals(broker_deals_batch)

        # 3. Retrieve recent signals
        signals = await database.get_recent_signals(limit=10)
        self.assertTrue(len(signals) >= 1)
        s = signals[0]

        # Crucial check: entry_price and close_price are distinct and correct!
        self.assertEqual(s["entry_price"], 2000.0)
        self.assertEqual(s["close_price"], 2025.0)
        self.assertNotEqual(s["entry_price"], s["close_price"])

    async def test_no_cross_contamination_on_same_symbol(self):
        """Requirement 2: Multiple positions on same symbol must NOT cross-contaminate."""
        # Save two distinct signals for XAUUSD
        sig1 = await database.save_signal(
            symbol="XAUUSD", direction="BUY", entry=2000.0, sl=1990.0, tp1=2030.0, position_id=101, record_type="LIVE"
        )
        sig2 = await database.save_signal(
            symbol="XAUUSD", direction="BUY", entry=2050.0, sl=2040.0, tp1=2080.0, position_id=102, record_type="LIVE"
        )

        # Close position 101 at 2015.0
        await database.close_signal_by_broker(
            signal_id=sig1,
            position_id=101,
            ticket=801,
            close_price=2015.0,
            net_pnl=75.0,
            gross_profit=75.0,
            status="TP1_HIT",
        )

        # Close position 102 at 2075.0
        await database.close_signal_by_broker(
            signal_id=sig2,
            position_id=102,
            ticket=802,
            close_price=2075.0,
            net_pnl=125.0,
            gross_profit=125.0,
            status="TP1_HIT",
        )

        t1 = await database.get_trade_by_position_id(101)
        t2 = await database.get_trade_by_position_id(102)

        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        # Position 101 must strictly have entry 2000.0 and close 2015.0
        self.assertEqual(t1["entry_price"], 2000.0)
        self.assertEqual(t1["close_price"], 2015.0)
        # Position 102 must strictly have entry 2050.0 and close 2075.0
        self.assertEqual(t2["entry_price"], 2050.0)
        self.assertEqual(t2["close_price"], 2075.0)

    async def test_partial_position_close_handling(self):
        """Requirement 3: Partial position close handling."""
        sig = await database.save_signal(
            symbol="XAUUSD", direction="BUY", entry=2000.0, sl=1990.0, tp1=2030.0, position_id=777, record_type="LIVE"
        )

        # Close half volume (0.02 of 0.04)
        deal_partial = {
            "ticket": 991,
            "position_id": 777,
            "symbol": "XAUUSD",
            "type": "SELL",
            "lot": 0.02,
            "price": 2015.0,
            "close_price": 2015.0,
            "entry_price": 2000.0,
            "profit": 30.0,
            "gross_profit": 30.0,
            "commission": -1.0,
            "swap": 0.0,
            "time": int(datetime.now(timezone.utc).timestamp()),
            "comment": "PARTIAL_TP1",
            "magic": 888001,
        }
        await database.sync_broker_deals([deal_partial])

        trade = await database.get_trade_by_position_id(777)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["lot"], 0.02)
        self.assertEqual(trade["net_pnl"], 29.0)  # 30 - 1.0 comm
        self.assertEqual(trade["result"], "PARTIAL_TP1")

    async def test_record_type_segregation(self):
        """Requirement 7 & 9: Strict segregation of LIVE vs DEMO vs TEST_SIGNAL vs BACKTEST."""
        # 1. Save 1 LIVE winning trade (+$100)
        await database.save_trade_record({
            "position_id": 11,
            "symbol": "XAUUSD",
            "direction": "BUY",
            "record_type": "LIVE",
            "entry_price": 2000.0,
            "close_price": 2020.0,
            "net_pnl": 100.0,
            "gross_profit": 100.0,
            "commission": 0.0,
            "swap": 0.0,
            "result": "WIN",
            "close_time": datetime.now(timezone.utc).isoformat(),
        })

        # 2. Save 5 DEMO losing trades (-$500 each)
        for i in range(5):
            await database.save_trade_record({
                "position_id": 20 + i,
                "symbol": "XAUUSD",
                "direction": "SELL",
                "record_type": "DEMO",
                "entry_price": 2000.0,
                "close_price": 2020.0,
                "net_pnl": -500.0,
                "gross_profit": -500.0,
                "result": "LOSS",
                "close_time": datetime.now(timezone.utc).isoformat(),
            })

        # 3. Save 10 BACKTEST trades
        for i in range(10):
            await database.save_trade_record({
                "position_id": 50 + i,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "record_type": "BACKTEST",
                "entry_price": 2000.0,
                "close_price": 2010.0,
                "net_pnl": 50.0,
                "gross_profit": 50.0,
                "result": "WIN",
                "close_time": datetime.now(timezone.utc).isoformat(),
            })

        # Test get_trade_history filtering
        live_trades, live_total = await database.get_trade_history(record_type="LIVE")
        self.assertEqual(live_total, 1)
        self.assertEqual(live_trades[0]["record_type"], "LIVE")

        demo_trades, demo_total = await database.get_trade_history(record_type="DEMO")
        self.assertEqual(demo_total, 5)

        backtest_trades, bt_total = await database.get_trade_history(record_type="BACKTEST")
        self.assertEqual(bt_total, 10)

        # Test get_stats segregation: LIVE stats must have 1 trade, 100% winrate, +$100 profit
        live_stats = await database.get_stats(record_type="LIVE")
        self.assertEqual(live_stats["wins"], 1)
        self.assertEqual(live_stats["losses"], 0)
        self.assertEqual(live_stats["win_rate"], 100.0)
        self.assertEqual(live_stats["total_profit_usd"], 100.0)

        # Test get_today_broker_pnl isolation
        today_live = await database.get_today_broker_pnl(record_type="LIVE")
        self.assertEqual(today_live["deals_count"], 1)
        self.assertEqual(today_live["total_pnl"], 100.0)

    async def test_breakeven_trade_matching_no_price_erasure(self):
        """Breakeven trade (entry == close) must NOT erase entry_price in get_recent_signals."""
        await database.save_signal(
            symbol="XAUUSD",
            direction="BUY",
            entry=2000.0,
            sl=1990.0,
            tp1=2030.0,
            position_id=601,
            record_type="LIVE",
        )
        # Close at exact breakeven 2000.0
        deal_be = {
            "ticket": 905,
            "position_id": 601,
            "symbol": "XAUUSD",
            "type": "SELL",
            "lot": 0.05,
            "price": 2000.0,
            "close_price": 2000.0,
            "entry_price": 2000.0,
            "profit": 0.0,
            "gross_profit": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "time": int(datetime.now(timezone.utc).timestamp()),
            "comment": "BREAKEVEN",
            "magic": 888001,
        }
        await database.sync_broker_deals([deal_be])

        signals = await database.get_recent_signals(limit=10)
        self.assertTrue(len(signals) >= 1)
        be_sig = [s for s in signals if s.get("position_id") == 601][0]
        self.assertEqual(be_sig["entry_price"], 2000.0)
        self.assertEqual(be_sig["close_price"], 2000.0)

    async def test_missing_entry_price_stored_as_null_and_displays_na(self):
        """Trade with missing entry_price is stored as NULL and renders N/A in card and web table."""
        from utils.formatters import format_trade_card

        tid = await database.save_trade_record({
            "position_id": 909,
            "symbol": "GBPUSD",
            "direction": "SELL",
            "record_type": "LIVE",
            "entry_price": None,
            "close_price": 1.25000,
            "lot": 0.05,
            "net_pnl": -15.0,
            "result": "SL_HIT",
        })
        trade = await database.get_trade_by_id(tid)
        self.assertIsNotNone(trade)
        self.assertIsNone(trade["entry_price"])

        card_text = format_trade_card(trade)
        self.assertIn("Вход: <code>N/A</code>", card_text)
        self.assertIn("Выход: <code>1.25000</code>", card_text)

    async def test_consecutive_sl_and_daily_count_isolated_from_demo_and_backtest(self):
        """DEMO and TEST_SIGNAL trades must NOT increment LIVE consecutive SL count or daily signal limit."""
        # Insert 3 DEMO signals that hit SL
        now_iso = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            await database.save_signal(
                symbol="EURUSD",
                direction="BUY",
                entry=1.0800,
                sl=1.0750,
                tp1=1.0900,
                record_type="DEMO",
                status="SL_HIT",
            )
            async with aiosqlite.connect(str(database.DB_PATH)) as db:
                await db.execute(
                    "UPDATE signals SET status = 'SL_HIT', closed_at = ? WHERE symbol = 'EURUSD' AND record_type = 'DEMO'",
                    (now_iso,)
                )
                await db.commit()

        # Insert 2 TEST_SIGNAL active signals
        for i in range(2):
            await database.save_signal(
                symbol="GBPUSD",
                direction="SELL",
                entry=1.2600,
                sl=1.2650,
                tp1=1.2500,
                record_type="TEST_SIGNAL",
                status="ACTIVE",
            )

        # Check LIVE counts: must be 0!
        live_consec_sl = await database.get_consecutive_sl_count(record_type="LIVE")
        self.assertEqual(live_consec_sl, 0)

        live_today_signals = await database.get_today_signal_count(record_type="LIVE")
        self.assertEqual(live_today_signals, 0)

        has_live_gbpusd = await database.has_open_signal_for_pair("GBPUSD", record_type="LIVE")
        self.assertFalse(has_live_gbpusd)

        # Check DEMO consecutive SL: must be 3
        demo_consec_sl = await database.get_consecutive_sl_count(record_type="DEMO")
        self.assertEqual(demo_consec_sl, 3)

        # Check TEST_SIGNAL daily count: must be 2
        test_today_signals = await database.get_today_signal_count(record_type="TEST_SIGNAL")
        self.assertEqual(test_today_signals, 2)

    async def test_trade_history_filters_multi_criteria(self):
        """get_trade_history must accurately filter by direction, trading_session, strategy, and date range."""
        await database.save_trade_record({
            "position_id": 1001, "symbol": "XAUUSD", "direction": "BUY", "record_type": "LIVE",
            "trading_session": "London", "strategy": "ICT_SMC", "result": "TP1_HIT",
            "close_price": 2020.0, "net_pnl": 50.0, "close_time": "2026-06-01T10:00:00+00:00",
        })
        await database.save_trade_record({
            "position_id": 1002, "symbol": "EURUSD", "direction": "SELL", "record_type": "LIVE",
            "trading_session": "New York", "strategy": "ICT_SMC", "result": "SL_HIT",
            "close_price": 1.0800, "net_pnl": -25.0, "close_time": "2026-06-02T16:00:00+00:00",
        })
        await database.save_trade_record({
            "position_id": 1003, "symbol": "GBPUSD", "direction": "BUY", "record_type": "DEMO",
            "trading_session": "London", "strategy": "Model_B", "result": "TP1_HIT",
            "close_price": 1.2700, "net_pnl": 40.0, "close_time": "2026-06-03T09:00:00+00:00",
        })
        await database.save_trade_record({
            "position_id": 1004, "symbol": "USDJPY", "direction": "BUY", "record_type": "LIVE",
            "trading_session": "Asian", "strategy": "Scalp_M1", "result": "BREAKEVEN",
            "close_price": 155.00, "net_pnl": 0.0, "close_time": "2026-06-05T02:00:00+00:00",
        })

        # Direction filter: BUY only on LIVE
        buy_trades, count_buy = await database.get_trade_history(record_type="LIVE", direction="BUY")
        self.assertEqual(count_buy, 2)
        pos_ids = {t["position_id"] for t in buy_trades}
        self.assertEqual(pos_ids, {1001, 1004})

        # Session filter: London only on LIVE
        london_trades, count_london = await database.get_trade_history(record_type="LIVE", trading_session="London")
        self.assertEqual(count_london, 1)
        self.assertEqual(london_trades[0]["position_id"], 1001)

        # Strategy filter: Model_B on DEMO
        model_b_trades, count_mb = await database.get_trade_history(record_type="DEMO", strategy="Model_B")
        self.assertEqual(count_mb, 1)
        self.assertEqual(model_b_trades[0]["position_id"], 1003)

        # Date range filter: 2026-06-02 to 2026-06-04 across ALL
        date_trades, count_date = await database.get_trade_history(record_type="ALL", date_from="2026-06-02", date_to="2026-06-04")
        self.assertEqual(count_date, 2)
        pos_ids_date = {t["position_id"] for t in date_trades}
        self.assertEqual(pos_ids_date, {1002, 1003})


class TestWebappServerEndpoints(unittest.IsolatedAsyncioTestCase):
    """Tests for web panel routes: /history, /api/v1/history, /api/v1/export/csv, /api/v1/export/json."""

    async def asyncSetUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(self.temp_db_fd)
        database.DB_PATH = database.Path(self.temp_db_path)
        await database.init_db()

        # Seed sample trades
        await database.save_trade_record({
            "position_id": 8881,
            "order_id": 9991,
            "symbol": "XAUUSD",
            "direction": "BUY",
            "record_type": "LIVE",
            "lot": 0.05,
            "entry_price": 2000.0,
            "close_price": 2025.0,
            "initial_sl": 1990.0,
            "final_sl": 2005.0,
            "initial_tp": 2030.0,
            "final_tp": 2030.0,
            "gross_profit": 125.0,
            "commission": -3.50,
            "swap": -1.00,
            "slippage": 0.2,
            "net_pnl": 120.50,
            "planned_rr": 3.0,
            "actual_r": 2.41,
            "strategy": "Institutional SMC/ICT",
            "trading_session": "London",
            "result": "TP1_HIT",
            "account": "12345678",
            "close_time": datetime.now(timezone.utc).isoformat(),
        })

    async def asyncTearDown(self):
        try:
            if os.path.exists(self.temp_db_path):
                os.remove(self.temp_db_path)
        except Exception:
            pass

    async def test_api_history_and_export_routes(self):
        from webapp.server import create_webapp_app
        from aiohttp.test_utils import TestClient, TestServer

        app = create_webapp_app()
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        try:
            # 1. Test GET /api/v1/history
            resp = await client.get("/api/v1/history?record_type=LIVE")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(len(data["trades"]), 1)
            self.assertEqual(data["trades"][0]["symbol"], "XAUUSD")
            # Account masked
            self.assertEqual(data["trades"][0]["account"], "****5678")

            # 2. Test GET /api/v1/history/{id}
            trade_id = data["trades"][0]["id"]
            resp_det = await client.get(f"/api/v1/history/{trade_id}")
            self.assertEqual(resp_det.status, 200)
            det_data = await resp_det.json()
            self.assertEqual(det_data["trade"]["position_id"], 8881)

            # 3. Test GET /api/v1/export/csv
            resp_csv = await client.get("/api/v1/export/csv?record_type=LIVE")
            self.assertEqual(resp_csv.status, 200)
            self.assertIn("text/csv", resp_csv.headers.get("Content-Type", ""))
            csv_text = await resp_csv.text()
            self.assertIn("XAUUSD", csv_text)
            self.assertIn("****5678", csv_text)

            # 4. Test GET /api/v1/export/json
            resp_json = await client.get("/api/v1/export/json?record_type=LIVE")
            self.assertEqual(resp_json.status, 200)
            self.assertIn("application/json", resp_json.headers.get("Content-Type", ""))
            export_data = await resp_json.json()
            self.assertEqual(export_data["total_records"], 1)
            self.assertEqual(export_data["trades"][0]["symbol"], "XAUUSD")

            # 5. Test GET /history (HTML web panel)
            resp_html = await client.get("/history?record_type=LIVE")
            self.assertEqual(resp_html.status, 200)
            html_text = await resp_html.text()
            self.assertIn("SMART TRADER | ЖУРНАЛ АУДИТА СДЕЛОК", html_text)
            self.assertIn("XAUUSD", html_text)
            self.assertIn("+120.50 USD", html_text)

        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
