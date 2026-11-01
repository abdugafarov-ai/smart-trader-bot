"""
Phase 3 Technical Failure Stress Test Suite (Section 10)
Exhaustive verification of 10 technical failure, network latency, and connection loss scenarios
using mock/stub components under research profile conditions.

Scenarios tested:
1. Stale quotes (quote age > 15s in get_live_price / get_live_spread_pips)
2. Missing quotes (quote absent/None)
3. Price fetch delay / ping timeout (> 15s)
4. Connection loss (telemetry empty / terminal disconnected)
5. Repeated broker response / idempotency (duplicate deals in sync_broker_deals)
6. Duplicate signal rejection (existing position / order for symbol in can_open_new_position)
7. Position state update lag (in-flight pending lock in can_open_new_position)
8. Clock desynchronization / timestamp skew (future and expired quotes)
9. Component restart & circuit breaker state recovery (persist and reload from DB)
10. Economic calendar outage / fail-closed protection
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import os
import tempfile
import json
import asyncio

import config
from trading.execution_bridge import (
    ExecutionBridge,
    get_broker_today_date_str,
)
from news.economic_calendar import EconomicCalendar
from db.database import init_db, set_bot_setting, sync_broker_deals, DB_PATH
import aiosqlite


class TestPhase3TechnicalStress(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.bridge = ExecutionBridge()
        self.bridge.enabled = True
        self.bridge.order_in_flight = False
        # Choose a Wednesday at 14:00 UTC (active London/NY overlap, normal trading window)
        self.now = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)
        self.bridge.mt5_telemetry = {
            "balance": 5000.0,
            "equity": 5000.0,
            "margin_free": 5000.0,
            "broker": "FundingPips",
            "account": "123456",
            "positions": [],
            "orders": [],
            "last_ping": datetime.now(timezone.utc),
        }
        self.bridge._connected_terminals = {
            "FundingPips_123456": {"last_seen": datetime.now(timezone.utc)}
        }
        self.bridge.live_quotes["XAUUSD"] = {
            "bid": 2650.0,
            "ask": 2650.35,
            "time": self.now,
        }
        self.bridge.symbol_specs = {
            "XAUUSD": {
                "contract_size": 100.0,
                "tick_size": 0.01,
                "tick_val": 1.0,
                "min_lot": 0.01,
                "max_lot": 50.0,
                "lot_step": 0.01,
                "broker_confirmed": True,
            }
        }
        EconomicCalendar._shared_cache_data = [
            {"title": "Prior USD Event", "country": "USD", "impact": "Low", "date": "2026-01-01T00:00:00Z"}
        ]
        EconomicCalendar._last_successful_fetch = self.now
        EconomicCalendar._last_failed_time = None
        EconomicCalendar._last_fetch_error = ""

    async def asyncTearDown(self):
        # Clean up any potential circuit breaker test settings
        await set_bot_setting("equity_hard_stop_triggered", "false")
        await set_bot_setting("daily_loss_locked", "false")
        await set_bot_setting("equity_hard_stop_reason", "")
        await set_bot_setting("daily_lock_reason", "")

    # -------------------------------------------------------------------------
    # Scenario 1: Stale quote rejection (>15s)
    # -------------------------------------------------------------------------
    def test_01_stale_quote_rejection(self):
        """Quotes older than 15 seconds must return None/False, preventing order execution."""
        stale_time = self.now - timedelta(seconds=20)
        self.bridge.live_quotes["XAUUSD"]["time"] = stale_time

        ok, spread, max_s = self.bridge.is_spread_acceptable("XAUUSD", now_dt=self.now)
        self.assertFalse(ok, "Stale quote must fail spread acceptability check")
        self.assertEqual(spread, -1.0, "Stale quote must return sentinel -1.0 spread")

        live_p = self.bridge.get_live_price("XAUUSD")
        self.assertIsNone(live_p, "Stale quote must cause get_live_price to return None")

    # -------------------------------------------------------------------------
    # Scenario 2: Missing quote rejection
    # -------------------------------------------------------------------------
    def test_02_missing_quote_rejection(self):
        """Missing or uninitialized quote for symbol must reject trade immediately."""
        del self.bridge.live_quotes["XAUUSD"]

        ok, spread, max_s = self.bridge.is_spread_acceptable("XAUUSD", now_dt=self.now)
        self.assertFalse(ok, "Missing quote must fail spread acceptability check")
        self.assertEqual(spread, -1.0)

        live_p = self.bridge.get_live_price("XAUUSD")
        self.assertIsNone(live_p, "Missing quote must cause get_live_price to return None")

    # -------------------------------------------------------------------------
    # Scenario 3: Price fetch delay / ping timeout (>15s)
    # -------------------------------------------------------------------------
    def test_03_price_fetch_delay_ping_timeout(self):
        """If terminal telemetry ping is older than 15 seconds, MT5 is deemed offline and trading blocked."""
        delayed_ping = datetime.now(timezone.utc) - timedelta(seconds=18.5)
        self.bridge.mt5_telemetry["last_ping"] = delayed_ping

        is_online, ping_delta = self.bridge.is_mt5_online()
        self.assertFalse(is_online, "Terminal must be marked offline when last ping > 15s")
        self.assertGreaterEqual(ping_delta, 15.0)

        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(allowed, "Trading must be blocked during ping latency timeout")
            self.assertIn("MT5 не в сети", reason)

    # -------------------------------------------------------------------------
    # Scenario 4: Connection loss blocks all actions
    # -------------------------------------------------------------------------
    def test_04_connection_loss_blocks_all_actions(self):
        """When terminal telemetry is empty or completely disconnected, fail-closed safety is enforced."""
        self.bridge.mt5_telemetry = {}
        self.bridge._connected_terminals = {}

        is_online, ping_delta = self.bridge.is_mt5_online()
        self.assertFalse(is_online, "Disconnected terminal must report offline")

        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(allowed, "Trading must be strictly blocked on lost connection")
            self.assertIn("MT5 не в сети", reason)

    # -------------------------------------------------------------------------
    # Scenario 5: Repeated broker response idempotency
    # -------------------------------------------------------------------------
    async def test_05_repeated_broker_response_idempotency(self):
        """
        Receiving identical deal or execution confirmation repeatedly from broker
        must not increment daily deal count or mutate balance/drawdown multiple times.
        """
        test_ticket = 999888777
        deal = {
            "ticket": test_ticket,
            "symbol": "XAUUSD",
            "type": "BUY",
            "lot": 0.02,
            "price": 2650.00,
            "profit": -24.50,
            "close_time": int(self.now.timestamp()),
        }

        try:
            # First sync records the new deal
            res1 = await sync_broker_deals([deal])
            self.assertEqual(len(res1), 1, "First sync of unique ticket must record deal")

            # Second sync of the EXACT SAME deal must be deduplicated
            res2 = await sync_broker_deals([deal])
            self.assertEqual(len(res2), 0, "Duplicate ticket must be detected and rejected without double-counting")

            # Verify ExecutionBridge.check_daily_loss idempotency
            self.bridge.check_daily_loss(today_closed_pnl=-24.50, today_deals_count=1)
            self.assertEqual(self.bridge.today_stats_cache["deals_count"], 1)
            self.assertEqual(self.bridge.today_stats_cache["closed_pnl"], -24.50)

            # Repeated call with identical stats must preserve exact counts
            self.bridge.check_daily_loss(today_closed_pnl=-24.50, today_deals_count=1)
            self.assertEqual(self.bridge.today_stats_cache["deals_count"], 1)
            self.assertEqual(self.bridge.today_stats_cache["closed_pnl"], -24.50)
        finally:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute("DELETE FROM broker_deals WHERE ticket = ?", (test_ticket,))
                await db.execute("DELETE FROM trade_history WHERE deal_out_ticket = ?", (test_ticket,))
                await db.commit()

    # -------------------------------------------------------------------------
    # Scenario 6: Duplicate signal rejection
    # -------------------------------------------------------------------------
    def test_06_duplicate_signal_rejection(self):
        """A rapid duplicate signal for an active symbol must be rejected by can_open_new_position."""
        self.bridge.max_concurrent_positions = 2
        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            # Case A: Position already active in market
            self.bridge.mt5_telemetry["positions"] = [
                {"symbol": "XAUUSD", "type": "BUY", "lot": 0.02, "entry": 2650.0}
            ]
            allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(allowed, "Duplicate signal must be blocked when position already exists")
            self.assertIn("уже открыта в рынке", reason)

            # Case B: Pending order already placed
            self.bridge.mt5_telemetry["positions"] = []
            self.bridge.mt5_telemetry["orders"] = [
                {"symbol": "XAUUSD", "type": "BUY_LIMIT", "price": 2640.0, "lot": 0.02}
            ]
            allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(allowed, "Duplicate signal must be blocked when pending order exists")
            self.assertIn("уже выставлен", reason)

    # -------------------------------------------------------------------------
    # Scenario 7: Position state update lag (in-flight pending lock)
    # -------------------------------------------------------------------------
    def test_07_position_state_update_lag(self):
        """
        If an order is in-flight before the broker positions list refreshes,
        can_open_new_position must strictly enforce the in-flight lock to prevent double entries.
        """
        self.bridge.mt5_telemetry["positions"] = []
        self.bridge.mt5_telemetry["orders"] = []
        self.bridge.order_in_flight = True

        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(can_open, "In-flight trade lock must block new entry during telemetry lag")
            self.assertIn("in-flight", reason)

        # Clear in-flight state -> position opening is permitted
        self.bridge.order_in_flight = False
        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertTrue(can_open, "Once in-flight state clears, valid entry must be permitted")

    # -------------------------------------------------------------------------
    # Scenario 8: Clock desynchronization / timestamp skew
    # -------------------------------------------------------------------------
    def test_08_clock_desynchronization_safety(self):
        """
        Quotes and signals with expired timestamps or extreme future skew
        must be rejected as unsafe before dispatching to broker.
        """
        with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
            # Case A: Future clock skew (quote time 1 hour in future)
            future_skew = self.now + timedelta(hours=1)
            self.bridge.live_quotes["XAUUSD"]["time"] = future_skew
            ok, spread, _ = self.bridge.is_spread_acceptable("XAUUSD", now_dt=self.now)
            self.assertFalse(ok, "Future quote skew must fail spread check")
            can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(can_open, "Future clock skew must block order execution")
            self.assertIn("Спред недоступен или устарел", reason)

            # Case B: Stale quote (quote time 30s in past)
            stale_past = self.now - timedelta(seconds=30)
            self.bridge.live_quotes["XAUUSD"]["time"] = stale_past
            ok, spread, _ = self.bridge.is_spread_acceptable("XAUUSD", now_dt=self.now)
            self.assertFalse(ok, "Stale quote must fail spread check")
            can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)
            self.assertFalse(can_open, "Stale quote must block order execution")

    # -------------------------------------------------------------------------
    # Scenario 9: Component restart & circuit breaker state recovery
    # -------------------------------------------------------------------------
    async def test_09_component_restart_circuit_breaker_recovery(self):
        """
        Upon component restart, circuit breaker state (daily loss lock, equity floor stop)
        must be restored from persistent SQLite storage via load_settings_from_db.
        """
        # Set circuit breaker state on active bridge and persist to DB
        self.bridge.equity_hard_stop_triggered = True
        self.bridge.equity_hard_stop_reason = "Хард-стоп эквити: $4440.00 <= $4450.00"
        self.bridge.daily_loss_locked = True
        self.bridge.daily_lock_reason = "Дневная просадка 4.10% >= лимита 4.0%"
        self.bridge.current_trade_date = get_broker_today_date_str()
        await self.bridge.persist_circuit_breaker_state()

        # Instantiate fresh bridge simulating component restart
        fresh_bridge = ExecutionBridge()
        self.assertFalse(fresh_bridge.equity_hard_stop_triggered, "Uninitialized fresh bridge must start false")
        self.assertFalse(fresh_bridge.daily_loss_locked, "Uninitialized fresh bridge must start false")

        # Load persisted settings from DB
        await fresh_bridge.load_settings_from_db()
        self.assertTrue(fresh_bridge.equity_hard_stop_triggered, "Equity hard stop must be restored from DB after restart")
        self.assertTrue(fresh_bridge.daily_loss_locked, "Daily loss lock must be restored from DB after restart")
        self.assertIn("4440.00", fresh_bridge.equity_hard_stop_reason)
        self.assertIn("4.10%", fresh_bridge.daily_lock_reason)

    # -------------------------------------------------------------------------
    # Scenario 10: Economic calendar outage fail-closed protection
    # -------------------------------------------------------------------------
    def test_10_economic_calendar_outage_fail_closed(self):
        """
        If the economic calendar cache is uninitialized, stale, or experiencing API errors,
        check_news_blocked_sync must fail-closed (return True / BLOCKED) with NO_TRADE_NEWS_CALENDAR_UNAVAILABLE.
        """
        cal = EconomicCalendar()

        # Empty cache & failed fetch
        EconomicCalendar._shared_cache_data = []
        EconomicCalendar._last_successful_fetch = None
        EconomicCalendar._last_failed_time = self.now
        EconomicCalendar._last_fetch_error = "500 Server Error: upstream news provider unreachable"

        is_blocked, reason = cal.check_news_blocked_sync("XAUUSD")
        self.assertTrue(is_blocked, "Calendar outage must fail-closed and block trading")
        self.assertIn("NO_TRADE_NEWS_CALENDAR_UNAVAILABLE", reason)


if __name__ == "__main__":
    unittest.main()
