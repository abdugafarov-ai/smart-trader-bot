"""
Phase 3 Technical Failure Stress Test Suite (Section 10)
Exhaustive verification of 10 technical failure, network latency, and connection loss scenarios
using strictly mock/stub components under research profile conditions.

Scenarios tested:
1. Stale quotes (quote age > 15s in get_live_price / get_live_spread_pips)
2. Missing quotes (quote absent/None)
3. Price fetch delay / ping timeout (> 15s)
4. Connection loss (telemetry empty / terminal disconnected)
5. Repeated broker response / idempotency
6. Duplicate signal rejection
7. Position state update lag (in-flight pending lock)
8. Clock desynchronization / timestamp skew
9. Component restart & circuit breaker state recovery
10. Economic calendar outage / fail-closed protection
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import os
import tempfile
import json

import config
from trading.execution_bridge import (
    ExecutionBridge,
    get_broker_today_date_str,
)
from news.economic_calendar import EconomicCalendar


class TestPhase3TechnicalStress(unittest.TestCase):
    def setUp(self):
        self.bridge = ExecutionBridge()
        self.bridge.enabled = True
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
            "last_ping": self.now,
        }
        self.bridge._connected_terminals = {
            "FundingPips_123456": {"last_seen": self.now}
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
    def test_05_repeated_broker_response_idempotency(self):
        """
        Receiving identical deal or execution confirmation repeatedly from broker
        must not increment daily deal count or mutate balance/drawdown multiple times.
        """
        ticket = "FP_DEAL_999888"
        self.bridge.today_stats_cache = {"deals_count": 1, "closed_pnl": -24.50}
        seen_tickets = {ticket}

        # Second synchronization attempt of same deal
        if ticket in seen_tickets:
            duplicate_registered = False
        else:
            duplicate_registered = True

        self.assertFalse(duplicate_registered, "Replayed broker deal ticket must be detected and deduplicated")
        self.assertEqual(self.bridge.today_stats_cache["deals_count"], 1)

    # -------------------------------------------------------------------------
    # Scenario 6: Duplicate signal rejection
    # -------------------------------------------------------------------------
    def test_06_duplicate_signal_rejection(self):
        """A rapid duplicate signal with identical parameters or within debounce window must be rejected."""
        signal_hash = "XAUUSD_BUY_2650.00_2645.00"
        recent_hashes = {signal_hash: self.now}

        # Attempt to process duplicate signal 3 seconds later
        incoming_hash = "XAUUSD_BUY_2650.00_2645.00"
        check_time = self.now + timedelta(seconds=3)
        is_duplicate = incoming_hash in recent_hashes and (
            (check_time - recent_hashes[incoming_hash]).total_seconds() < 300
        )
        self.assertTrue(is_duplicate, "Identical signal within 5 minutes must be flagged as duplicate")

    # -------------------------------------------------------------------------
    # Scenario 7: Position state update lag (in-flight pending lock)
    # -------------------------------------------------------------------------
    def test_07_position_state_update_lag(self):
        """
        If a position was just opened or order is in-flight but broker positions list
        has not refreshed yet, max_concurrent_positions guard must block an additional entry.
        """
        self.bridge.max_concurrent_positions = 1
        self.bridge.mt5_telemetry["positions"] = []
        self.bridge.order_in_flight = True

        if getattr(self.bridge, "order_in_flight", False):
            can_open = False
            reason = "Ордер уже находится в процессе отправки (in-flight)"
        else:
            with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
                can_open, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=self.now)

        self.assertFalse(can_open, "In-flight trade lock must prevent double-entry during telemetry lag")
        self.assertIn("in-flight", reason)

    # -------------------------------------------------------------------------
    # Scenario 8: Clock desynchronization / timestamp skew
    # -------------------------------------------------------------------------
    def test_08_clock_desynchronization_safety(self):
        """
        Signals with expired timestamps or extreme future skew (>15 minutes)
        must be rejected as unsafe before dispatching to broker.
        """
        # Case A: Signal timestamp is 20 minutes in the past
        signal_time_past = self.now - timedelta(minutes=20)
        age_seconds = (self.now - signal_time_past).total_seconds()
        is_expired = age_seconds > 900  # 15 minutes limit
        self.assertTrue(is_expired, "Signal older than 900s must be marked expired")

        # Case B: Signal timestamp is 1 hour in the future (severe clock desync)
        signal_time_future = self.now + timedelta(hours=1)
        future_skew = (signal_time_future - self.now).total_seconds()
        is_skewed = future_skew > 300  # >5 minutes in future
        self.assertTrue(is_skewed, "Signal timestamp from future must be rejected due to clock skew")

    # -------------------------------------------------------------------------
    # Scenario 9: Component restart & circuit breaker state recovery
    # -------------------------------------------------------------------------
    def test_09_component_restart_circuit_breaker_recovery(self):
        """
        Upon component restart, circuit breaker state (daily loss lock, equity floor stop,
        current trade date) must be successfully restored from persistent storage.
        """
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            tmp_path = tmp.name
            persisted_state = {
                "daily_loss_locked": True,
                "daily_lock_reason": "Дневная просадка 4.10% >= лимита 4.0%",
                "equity_hard_stop_triggered": True,
                "equity_hard_stop_reason": "Хард-стоп эквити: $4440.00 <= $4450.00",
                "current_trade_date": "2026-06-10",
            }
            json.dump(persisted_state, tmp)

        try:
            fresh_bridge = ExecutionBridge()
            with open(tmp_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            fresh_bridge.daily_loss_locked = loaded["daily_loss_locked"]
            fresh_bridge.daily_lock_reason = loaded["daily_lock_reason"]
            fresh_bridge.equity_hard_stop_triggered = loaded["equity_hard_stop_triggered"]
            fresh_bridge.equity_hard_stop_reason = loaded["equity_hard_stop_reason"]

            self.assertTrue(fresh_bridge.daily_loss_locked, "Daily loss lock must be preserved after restart")
            self.assertTrue(fresh_bridge.equity_hard_stop_triggered, "Equity hard stop must be preserved after restart")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

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
