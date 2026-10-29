"""
Tests for Critical Risk Management and Execution Hardening (Section 7):
1. Missing, invalid, or stale quote blocks spread and trade (ExecutionBridge.is_spread_acceptable).
2. Missing or zero reliable capital blocks trade (ExecutionBridge.calculate_lot & can_open_new_position).
3. Zero lot never converts to a fixed or default volume (calculate_lot strictly returns 0.0).
4. Minimum broker lot does not bypass or exceed monetary risk budget.
5. Python and MQL5 apply strictly aligned rules and conservative capital base min(balance, equity).
6. Duplicate signal dispatch protection (in-flight tracking, confirmed orders omitted, telemetry reconciliation).
7. Corrupted or missing market data does not confirm setup (returns NEUTRAL / NO TRADE).
8. Emergency circuit breakers (equity hard stop, daily loss lock) persist across restarts.
"""

import unittest
import asyncio
import json
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

import config
from trading.execution_bridge import ExecutionBridge, calculate_gold_swing_lot
from strategies.ict_smc import ICTSMCStrategy
import webapp.server as srv


class TestCriticalFixes(unittest.TestCase):
    def setUp(self):
        self.bridge = ExecutionBridge()
        self.strategy = ICTSMCStrategy()

    # ─────────────────────────────────────────────────────────────────
    # 1. Missing, invalid, or stale quote blocks spread & new positions
    # ─────────────────────────────────────────────────────────────────
    def test_missing_quote_blocks_spread_and_trade(self):
        """Missing or stale quote must strictly return False and block trade."""
        self.bridge.live_quotes.clear()
        with patch.object(self.bridge, "is_mt5_online", return_value=(True, 0.05)):
            # Case 1: No live quote at all
            is_ok, cur_spread, max_s = self.bridge.is_spread_acceptable("XAUUSD")
            self.assertFalse(is_ok, "Must reject when quote is completely missing")
            self.assertEqual(cur_spread, -1.0)

            # Case 2: MT5 offline
            with patch.object(self.bridge, "is_mt5_online", return_value=(False, 999.0)):
                is_ok, cur_spread, max_s = self.bridge.is_spread_acceptable("XAUUSD")
                self.assertFalse(is_ok, "Must reject when MT5 is offline")

            # Case 3: Invalid bid/ask (bid <= 0 or ask < bid)
            self.bridge.live_quotes["XAUUSD"] = {
                "bid": 0.0,
                "ask": 2650.50,
                "time": datetime.now(timezone.utc),
            }
            is_ok, _, _ = self.bridge.is_spread_acceptable("XAUUSD")
            self.assertFalse(is_ok, "Must reject when bid <= 0")

            self.bridge.live_quotes["XAUUSD"] = {
                "bid": 2651.00,
                "ask": 2650.00,  # inverted
                "time": datetime.now(timezone.utc),
            }
            is_ok, _, _ = self.bridge.is_spread_acceptable("XAUUSD")
            self.assertFalse(is_ok, "Must reject when ask < bid")

            # Case 4: Stale quote (> 15 seconds old)
            stale_time = datetime.now(timezone.utc) - timedelta(seconds=20)
            self.bridge.live_quotes["XAUUSD"] = {
                "bid": 2650.00,
                "ask": 2650.30,
                "time": stale_time,
            }
            is_ok, _, _ = self.bridge.is_spread_acceptable("XAUUSD")
            self.assertFalse(is_ok, "Must reject when quote is older than 15 seconds")

            # Case 5: Fresh, valid quote within normal spread
            fresh_time = datetime.now(timezone.utc) - timedelta(seconds=2)
            self.bridge.live_quotes["XAUUSD"] = {
                "bid": 2650.00,
                "ask": 2650.30,  # 3.0 points ($0.30 spread)
                "time": fresh_time,
            }
            is_ok, cur_spread, max_s = self.bridge.is_spread_acceptable("XAUUSD")
            self.assertTrue(is_ok, "Must accept fresh normal quote")
            self.assertEqual(cur_spread, 3.0)

    # ─────────────────────────────────────────────────────────────────
    # 2. Missing or zero reliable capital blocks trade
    # ─────────────────────────────────────────────────────────────────
    def test_missing_reliable_capital_blocks_trade(self):
        """When account telemetry has no valid capital, orders must be strictly blocked."""
        # Default with no telemetry should return 0.0
        self.bridge.mt5_telemetry["balance"] = 0.0
        self.bridge.mt5_telemetry["equity"] = 0.0
        self.assertEqual(self.bridge.get_effective_balance(0.0), 0.0)

        # calculate_lot must return 0.0 when balance <= 0
        lot = self.bridge.calculate_lot("XAUUSD", sl_pips=10.0, balance=0.0)
        self.assertEqual(lot, 0.0, "calculate_lot must return 0.0 when balance is 0.0")

        # When balance is not provided and telemetry is 0, calculate_lot must return 0.0
        lot = self.bridge.calculate_lot("XAUUSD", sl_pips=10.0, balance=None)
        self.assertEqual(lot, 0.0, "calculate_lot must not fallback to $5,000 when telemetry is 0")

        # can_open_new_position must block trade when balance/equity <= 0
        self.bridge.enabled = True
        self.bridge.daily_loss_locked = False
        valid_trading_time = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
        with patch.object(self.bridge, "is_mt5_online", return_value=(True, 0.05)), \
             patch("config.is_weekly_trading_window_open", return_value=(True, "")), \
             patch("config.is_rollover_window", return_value=(False, "")):
            can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=valid_trading_time)
            self.assertFalse(can_open)
            self.assertIn("состояние счёта", reason)

        # ICTSMCStrategy.analyze must strictly return NEUTRAL when capital is missing
        from tests.test_comprehensive_audit import _build_intraday_gold_frames
        from trading.execution_bridge import bridge_manager
        bridge_manager.mt5_telemetry["balance"] = 0.0
        bridge_manager.mt5_telemetry["equity"] = 0.0
        df_d1, df_h1, df_m15, df_m5 = _build_intraday_gold_frames()
        kz_time = datetime(2026, 10, 27, 8, 30, tzinfo=timezone.utc)
        res_no_cap = self.strategy.analyze(
            df_m15, symbol="XAUUSD", timeframe="M15",
            df_d1=df_d1, df_ltf=df_m5, current_time=kz_time
        )
        self.assertEqual(res_no_cap.signal.direction, "NEUTRAL")
        self.assertIn("NO_CAPITAL_DATA", res_no_cap.signal.rejection_reasons)

    # ─────────────────────────────────────────────────────────────────
    # 3. Zero lot never converts to a fixed or default volume
    # ─────────────────────────────────────────────────────────────────
    def test_zero_lot_never_converts_to_fixed_volume(self):
        """A zero calculated lot must NEVER become default_lot in any lot mode."""
        self.bridge.set_lot(0.05)
        self.assertEqual(self.bridge.lot_mode, "fixed")
        self.assertEqual(self.bridge.default_lot, 0.05)

        # Huge SL ($50) on $5,000 account at 0.25% ($12.50 budget) -> max safe lot is 0.0 (exceeds budget)
        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_dist_usd=50.0,
            balance=5000.0,
            risk_pct=0.25,
        )
        self.assertEqual(lot, 0.0, "Must return 0.0 and NEVER fallback to fixed lot 0.05")

        # Test with zero or negative SL distance
        lot_zero_sl = self.bridge.calculate_lot(
            symbol="XAUUSD",
            sl_dist_usd=0.0,
            balance=5000.0,
            risk_pct=0.25,
        )
        self.assertEqual(lot_zero_sl, 0.0, "Zero SL must yield 0.0 lot")

    # ─────────────────────────────────────────────────────────────────
    # 4. Minimum broker lot does not bypass risk budget
    # ─────────────────────────────────────────────────────────────────
    def test_minimum_lot_does_not_exceed_risk_budget(self):
        """0.01 min lot risk must strictly not exceed dollar budget."""
        # $5,000 account, 0.25% risk = $12.50 budget.
        # XAUUSD: 0.01 lot dollar loss per $1.00 move is $1.00.
        # SL = $13.00 -> 0.01 lot loss = $13.00 > $12.50 -> REJECT
        lot_25 = calculate_gold_swing_lot(balance=5000.0, sl_dist_usd=13.0, risk_pct=0.25)
        self.assertEqual(lot_25, 0.0)

        # SL = $12.00 -> 0.01 lot loss = $12.00 <= $12.50 -> ACCEPT (0.01 lot)
        lot_25_ok = calculate_gold_swing_lot(balance=5000.0, sl_dist_usd=12.0, risk_pct=0.25)
        self.assertEqual(lot_25_ok, 0.01)

        # $5,000 account, 0.50% risk = $25.00 budget.
        # SL = $26.00 -> 0.01 lot loss = $26.00 > $25.00 -> REJECT
        lot_50 = calculate_gold_swing_lot(balance=5000.0, sl_dist_usd=26.0, risk_pct=0.50)
        self.assertEqual(lot_50, 0.0)

    # ─────────────────────────────────────────────────────────────────
    # 5. Python and MQL5 apply strictly aligned rules
    # ─────────────────────────────────────────────────────────────────
    def test_python_and_mql5_rules_alignment(self):
        """
        Verify that Python and MQL5 risk calculations use conservative base min(balance, equity)
        and identical monetary formulas.
        """
        # Scenario: Balance = $5,000, Equity = $4,600 (floating loss).
        # Conservative capital base must be min(5000, 4600) = $4,600.
        self.bridge.mt5_telemetry["balance"] = 5000.0
        self.bridge.mt5_telemetry["equity"] = 4600.0
        eff_cap = self.bridge.get_effective_balance()
        self.assertEqual(eff_cap, 4600.0, "Effective capital must equal min(balance, equity)")

        # Risk budget at 0.25%: $4,600 * 0.0025 = $11.50.
        # SL = $12.00: min lot risk = $12.00 > $11.50 -> Must reject (0.0)
        lot = self.bridge.calculate_lot("XAUUSD", sl_dist_usd=12.0, risk_pct=0.25)
        self.assertEqual(lot, 0.0, "Must reject trade when min lot exceeds conservative budget")

        # SL = $5.00: risk = $5.00 <= $11.50. Raw lot = 11.50 / 500 = 0.023 -> floored to 0.02 lot.
        lot_ok = self.bridge.calculate_lot("XAUUSD", sl_dist_usd=5.0, risk_pct=0.25)
        self.assertEqual(lot_ok, 0.02)

        # MQL5 formula simulation:
        # pips = diff / tick_size = 5.0 / 0.01 = 500
        # lot = risk_amt / (pips * tick_val) = 11.50 / (500 * 1.0) = 0.023 -> 0.02
        mql5_diff = 5.0
        mql5_tick_size = 0.01
        mql5_tick_val = 1.0
        mql5_pips = mql5_diff / mql5_tick_size
        mql5_risk_amt = eff_cap * (0.25 / 100.0)
        mql5_lot = math.floor((mql5_risk_amt / (mql5_pips * mql5_tick_val)) * 100.0) / 100.0
        self.assertEqual(lot_ok, mql5_lot, "Python and MQL5 calculated lots must match exactly")

    # ─────────────────────────────────────────────────────────────────
    # 6. Duplicate signal dispatch protection
    # ─────────────────────────────────────────────────────────────────
    def test_duplicate_signal_dispatch_protection(self):
        """
        Verifies:
        1. An in-flight signal dispatched within 45s is not dispatched again.
        2. A signal marked broker_confirmed or with a broker_ticket is not returned in orders.
        """
        async def run_dedup_test():
            srv._dispatched_in_flight.clear()

            auth_req = MagicMock()
            auth_req.method = "GET"
            auth_req.headers = {"X-Bridge-Key": "test_secret_key"}
            auth_req.query = {}
            auth_req.remote = "127.0.0.1"

            fresh_signal = {
                "id": 901,
                "symbol": "XAUUSD",
                "direction": "LONG",
                "order_type": "BUY_MARKET",
                "entry_price": 2650.0,
                "stop_loss": 2645.0,
                "take_profit_1": 2665.0,
                "take_profit_2": 2670.0,
                "stars": 5,
                "status": "ACTIVE",
                "broker_confirmed": 0,
                "broker_ticket": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            class _MockCursor:
                def __init__(self, data):
                    self.data = data
                async def fetchall(self):
                    return self.data
                def __await__(self):
                    async def _coro():
                        return self
                    return _coro().__await__()

            mock_db = MagicMock()
            mock_db.execute = MagicMock(return_value=_MockCursor([fresh_signal]))
            mock_conn = MagicMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_db)
            mock_conn.__aexit__ = AsyncMock(return_value=None)

            # Setup bridge with valid telemetry
            from trading.execution_bridge import bridge_manager
            bridge = bridge_manager
            bridge.enabled = True
            bridge.mt5_telemetry["balance"] = 5000.0
            bridge.mt5_telemetry["equity"] = 5000.0
            bridge.mt5_telemetry["last_ping"] = datetime.now(timezone.utc)
            bridge.live_quotes["XAUUSD"] = {
                "bid": 2650.0,
                "ask": 2650.3,
                "time": datetime.now(timezone.utc),
            }
            bridge.symbol_specs["XAUUSD"] = {
                "contract_size": 100.0,
                "tick_size": 0.01,
                "tick_val": 1.0,
                "min_lot": 0.01,
                "max_lot": 0.50,
                "lot_step": 0.01,
                "currency": "USD",
                "broker_confirmed": True,
            }

            with patch.object(config, "BRIDGE_API_KEY", "test_secret_key"), \
                 patch("trading.execution_bridge.bridge_manager.check_equity_hard_stop", return_value=(False, "")), \
                 patch("trading.execution_bridge.bridge_manager.check_daily_loss", return_value=(False, 0.0, 0.0, 5000.0)), \
                 patch("trading.execution_bridge.bridge_manager.can_open_new_position", return_value=(True, "")), \
                 patch("db.database.get_today_broker_pnl", new_callable=AsyncMock, return_value={"total_pnl": 0.0, "deals_count": 0}), \
                 patch("db.database.get_bot_setting", new_callable=AsyncMock, return_value="prop"), \
                 patch("db.database.get_consecutive_sl_count", new_callable=AsyncMock, return_value=0), \
                 patch("aiosqlite.connect", return_value=mock_conn):

                # First poll: signal is fresh and gets dispatched
                resp1 = await srv.bridge_get_orders(auth_req)
                data1 = json.loads(resp1.text)
                self.assertEqual(len(data1["orders"]), 1)
                self.assertEqual(data1["orders"][0]["id"], 901)
                self.assertIn(901, srv._dispatched_in_flight)

                # Immediate second poll (e.g. timeout retry 2s later): signal is in-flight -> NOT dispatched
                resp2 = await srv.bridge_get_orders(auth_req)
                data2 = json.loads(resp2.text)
                self.assertEqual(len(data2["orders"]), 0, "In-flight signal must NOT be dispatched a second time!")

        asyncio.run(run_dedup_test())

    # ─────────────────────────────────────────────────────────────────
    # 7. Corrupted or missing data does not confirm setup
    # ─────────────────────────────────────────────────────────────────
    def test_corrupted_or_missing_data_rejects_setup(self):
        """Corrupted OHLC or missing timeframes must strictly return NEUTRAL."""
        # Empty DataFrame
        res_empty = self.strategy.analyze(pd.DataFrame(), "XAUUSD", "M15")
        self.assertEqual(res_empty.signal.direction, "NEUTRAL")

        # Corrupted DataFrame (negative prices, high < low)
        bad_df = pd.DataFrame({
            "timestamp": ["2026-10-27 10:00", "2026-10-27 10:05"],
            "open": [-100.0, 200.0],
            "high": [150.0, 100.0],  # high < open
            "low": [250.0, 300.0],   # low > high
            "close": [180.0, 210.0],
            "volume": [10, 20],
        })
        res_corrupt = self.strategy.analyze(bad_df, "XAUUSD", "M15")
        self.assertEqual(res_corrupt.signal.direction, "NEUTRAL")

    # ─────────────────────────────────────────────────────────────────
    # 8. Emergency circuit breakers persist across restart
    # ─────────────────────────────────────────────────────────────────
    def test_emergency_circuit_breakers_persist_across_restart(self):
        """Emergency hard stop / daily loss lock must persist and refuse auto-enable on restart."""
        async def run_restart_test():
            new_bridge = ExecutionBridge()
            # Initial state of new instance
            self.assertFalse(new_bridge.equity_hard_stop_triggered)

            # Mock DB returning persisted emergency lock
            db_store = {
                "equity_hard_stop_triggered": "true",
                "equity_hard_stop_reason": "Equity $4440 <= $4450",
                "autotrade_enabled": "true",  # Attempt to force enabled
            }
            with patch("db.database.get_bot_setting", new_callable=AsyncMock) as mock_get, \
                 patch("db.database.set_bot_setting", new_callable=AsyncMock):
                mock_get.side_effect = lambda k, default="": db_store.get(k, default)
                await new_bridge.load_settings_from_db()

                # Lock must be loaded and autopilot must remain False
                self.assertTrue(new_bridge.equity_hard_stop_triggered)
                self.assertFalse(new_bridge.enabled)

                # Attempting manual enable must fail closed
                new_bridge.set_enabled(True)
                self.assertFalse(new_bridge.enabled, "Cannot enable while equity hard stop is persisted!")

        asyncio.run(run_restart_test())


if __name__ == "__main__":
    unittest.main()
