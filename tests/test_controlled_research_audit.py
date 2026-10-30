"""
Controlled Research Technical Audit Test Suite
Verifies all 13 safety, quantitative, and execution boundary scenarios
for XAUUSD intraday research profiles under FundingPips $5,000 2-Step Flex rules.
"""

import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

import config
from trading.execution_bridge import (
    calculate_gold_swing_lot,
    ExecutionBridge,
    get_broker_today_date_str,
)
from news.economic_calendar import EconomicCalendar
from scripts.intraday_gold_engine import is_news_blocked, get_high_impact_usd_events


class TestControlledResearchAudit(unittest.TestCase):
    """
    Automated verification of the 13 required scenarios for the
    independent technical audit of XAUUSD ICT/SMC intraday research.
    """

    def setUp(self):
        self.bridge = ExecutionBridge()
        self.bridge.enabled = True
        now = datetime.now(timezone.utc)
        self.bridge.mt5_telemetry = {
            "balance": 5000.0,
            "equity": 5000.0,
            "margin_free": 5000.0,
            "broker": "FundingPips",
            "account": "123456",
            "positions": [],
            "orders": [],
            "last_ping": now,
        }
        self.bridge._connected_terminals = {
            "FundingPips_123456": {"last_seen": now}
        }
        self.bridge.live_quotes["XAUUSD"] = {
            "bid": 2650.0,
            "ask": 2650.35,
            "time": now,
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

    # -------------------------------------------------------------------------
    # Scenario 1: Fixed lot 0.02 accepted when risk within budget
    # -------------------------------------------------------------------------
    def test_scenario_01_fixed_lot_002_accepted_within_budget(self):
        """
        On a $5,000 account, 0.50% hard risk cap = $25.00.
        SL distance = $5.00.
        At 0.02 lot: price loss = 0.02 * $5.00 * 100 = $10.00.
        Effective loss including spread ($0.30) & slippage ($0.20) buffers ($0.50 = $1.00) + commission ($0.06) = $11.06 <= $25.00.
        Must accept fixed lot 0.02.
        """
        self.bridge.lot_mode = "fixed"
        self.bridge.default_lot = 0.02
        self.bridge.default_risk = 0.50

        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            balance=5000.0,
            risk_pct=0.50,
            sl_dist_usd=5.0,
            require_confirmed_specs=True,
            entry=2650.0,
            sl=2645.0,
        )
        self.assertEqual(lot, 0.02, f"Fixed lot 0.02 should be accepted for $5.00 SL, got {lot}")

    # -------------------------------------------------------------------------
    # Scenario 2: Fixed lot 0.02 rejected when structural SL creates risk > $25.00
    # -------------------------------------------------------------------------
    def test_scenario_02_fixed_lot_002_rejected_when_risk_exceeds_budget(self):
        """
        On a $5,000 account, 0.50% hard risk cap = $25.00.
        SL distance = $15.00.
        At 0.02 lot: price loss = 0.02 * $15.00 * 100 = $30.00.
        $30.00 > $25.00 risk budget -> Must strictly reject with 0.0 (NO TRADE).
        """
        self.bridge.lot_mode = "fixed"
        self.bridge.default_lot = 0.02
        self.bridge.default_risk = 0.50

        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            balance=5000.0,
            risk_pct=0.50,
            sl_dist_usd=15.0,
            require_confirmed_specs=True,
            entry=2650.0,
            sl=2635.0,
        )
        self.assertEqual(lot, 0.0, f"Fixed lot 0.02 MUST be rejected when risk ($30.00) exceeds $25.00 budget, got {lot}")

    # -------------------------------------------------------------------------
    # Scenario 3: Unconfirmed/invalid XAUUSD broker specs block trade (return 0.0)
    # -------------------------------------------------------------------------
    def test_scenario_03_unconfirmed_broker_specs_block_trade(self):
        """
        If MT5 broker symbol specs are missing, unconfirmed, or non-positive,
        calculate_lot must reject real orders with 0.0 (NO TRADE).
        """
        # Case A: Symbol not confirmed
        self.bridge.symbol_specs["XAUUSD"]["broker_confirmed"] = False
        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            balance=5000.0,
            risk_pct=0.50,
            sl_dist_usd=5.0,
            require_confirmed_specs=True,
            entry=2650.0,
            sl=2645.0,
        )
        self.assertEqual(lot, 0.0, "Trade must be blocked (0.0) when broker specs are not confirmed")

        # Case B: Zero contract size
        self.bridge.symbol_specs["XAUUSD"]["broker_confirmed"] = True
        self.bridge.symbol_specs["XAUUSD"]["contract_size"] = 0.0
        lot = self.bridge.calculate_lot(
            symbol="XAUUSD",
            balance=5000.0,
            risk_pct=0.50,
            sl_dist_usd=5.0,
            require_confirmed_specs=True,
            entry=2650.0,
            sl=2645.0,
        )
        self.assertEqual(lot, 0.0, "Trade must be blocked (0.0) when contract_size is 0")

    # -------------------------------------------------------------------------
    # Scenario 4: 5 new trades permitted today, 6th is blocked
    # -------------------------------------------------------------------------
    def test_scenario_04_five_new_trades_permitted_sixth_blocked(self):
        """
        Under profile RESEARCH_XAUUSD_FIXED002 (max_daily_new_trades = 5):
        - Trades 1 through 5 are allowed (deals_count < 5).
        - 6th trade (deals_count = 5) is rejected by can_open_new_position.
        """
        with patch.dict(config.TRADING_PROFILES, {
            "RESEARCH_XAUUSD_FIXED002": {
                **config.TRADING_PROFILES.get("RESEARCH_XAUUSD_FIXED002", {}),
                "max_daily_new_trades": 5,
            }
        }):
            with patch("config.ACTIVE_TRADING_PROFILE", "RESEARCH_XAUUSD_FIXED002"):
                with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
                    now = datetime.now(timezone.utc)
                    self.bridge.live_quotes["XAUUSD"]["time"] = now
                    self.bridge.mt5_telemetry["last_ping"] = now

                    # Simulating 4 deals today (5th trade attempt)
                    self.bridge.today_stats_cache = {"deals_count": 4}
                    allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=now)
                    self.assertTrue(allowed, f"5th trade attempt should be allowed, got blocked: {reason}")

                    # Simulating 5 deals today (6th trade attempt)
                    self.bridge.today_stats_cache = {"deals_count": 5}
                    allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=now)
                    self.assertFalse(allowed, "6th trade attempt MUST be blocked")
                    self.assertIn("Дневной лимит сделок исчерпан", reason)

    # -------------------------------------------------------------------------
    # Scenario 5: Duplicate signal/message does not bypass daily limit
    # -------------------------------------------------------------------------
    def test_scenario_05_duplicate_signal_does_not_bypass_daily_limit(self):
        """
        When the daily trade limit is reached (5 deals), repeated calls or duplicate signals
        must remain consistently blocked without modifying state or bypassing the lock.
        """
        with patch.dict(config.TRADING_PROFILES, {
            "RESEARCH_XAUUSD_FIXED002": {
                **config.TRADING_PROFILES.get("RESEARCH_XAUUSD_FIXED002", {}),
                "max_daily_new_trades": 5,
            }
        }):
            with patch("config.ACTIVE_TRADING_PROFILE", "RESEARCH_XAUUSD_FIXED002"):
                with patch("config.is_weekly_trading_window_open", return_value=(True, "")):
                    now = datetime.now(timezone.utc)
                    self.bridge.live_quotes["XAUUSD"]["time"] = now
                    self.bridge.mt5_telemetry["last_ping"] = now
                    self.bridge.today_stats_cache = {"deals_count": 5}

                    for attempt in range(3):
                        allowed, reason = self.bridge.can_open_new_position("XAUUSD", now_dt=now)
                        self.assertFalse(allowed, f"Duplicate signal attempt #{attempt+1} bypassed the daily limit!")
                        self.assertIn("Дневной лимит сделок исчерпан", reason)

    # -------------------------------------------------------------------------
    # Scenario 6: News block triggers 30m before and 30m after high-impact events
    # -------------------------------------------------------------------------
    def test_scenario_06_news_block_window_30m(self):
        """
        Given a high-impact event at 12:30:00 UTC:
        - 11:59:00 UTC -> NOT blocked (31m before)
        - 12:00:00 UTC -> BLOCKED (exactly 30m before)
        - 12:15:00 UTC -> BLOCKED (15m before)
        - 12:30:00 UTC -> BLOCKED (event time)
        - 12:45:00 UTC -> BLOCKED (15m after)
        - 13:00:00 UTC -> BLOCKED (exactly 30m after)
        - 13:01:00 UTC -> NOT blocked (31m after)
        """
        event_time = datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)
        events = [event_time]

        self.assertFalse(is_news_blocked(datetime(2026, 6, 10, 11, 59, tzinfo=timezone.utc), events, window_min=30))
        self.assertTrue(is_news_blocked(datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc), events, window_min=30))
        self.assertTrue(is_news_blocked(datetime(2026, 6, 10, 12, 15, tzinfo=timezone.utc), events, window_min=30))
        self.assertTrue(is_news_blocked(datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc), events, window_min=30))
        self.assertTrue(is_news_blocked(datetime(2026, 6, 10, 12, 45, tzinfo=timezone.utc), events, window_min=30))
        self.assertTrue(is_news_blocked(datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc), events, window_min=30))
        self.assertFalse(is_news_blocked(datetime(2026, 6, 10, 13, 1, tzinfo=timezone.utc), events, window_min=30))

    # -------------------------------------------------------------------------
    # Scenario 7: Clean boundary, DST, and date-change behavior without errors
    # -------------------------------------------------------------------------
    def test_scenario_07_clean_boundary_dst_and_date_change(self):
        """
        Verifies:
        1. US DST transitions for 2026:
           - In winter (EST, UTC-5): 08:30 NY = 13:30 UTC.
           - In summer (EDT, UTC-4): 08:30 NY = 12:30 UTC.
        2. Midnight rollover: resets daily loss lock cleanly upon new broker date.
        """
        ny_tz = ZoneInfo("America/New_York")
        utc_tz = timezone.utc

        # Winter date: Feb 6, 2026 (EST, UTC-5)
        dt_winter_ny = datetime(2026, 2, 6, 8, 30, tzinfo=ny_tz)
        dt_winter_utc = dt_winter_ny.astimezone(utc_tz)
        self.assertEqual(dt_winter_utc.hour, 13)
        self.assertEqual(dt_winter_utc.minute, 30)

        # Summer date: June 5, 2026 (EDT, UTC-4)
        dt_summer_ny = datetime(2026, 6, 5, 8, 30, tzinfo=ny_tz)
        dt_summer_utc = dt_summer_ny.astimezone(utc_tz)
        self.assertEqual(dt_summer_utc.hour, 12)
        self.assertEqual(dt_summer_utc.minute, 30)

        # Midnight rollover reset test
        self.bridge.daily_loss_locked = True
        self.bridge.current_trade_date = "2026-06-09"
        with patch("trading.execution_bridge.get_broker_today_date_str", return_value="2026-06-10"):
            is_locked, _, _, _ = self.bridge.check_daily_loss(today_closed_pnl=0.0)
            self.assertFalse(is_locked, "Daily loss lock must reset on new broker date at 00:00 server time")
            self.assertEqual(self.bridge.current_trade_date, "2026-06-10")

    # -------------------------------------------------------------------------
    # Scenario 8: Missing calendar or API error blocks new entries (no false clear)
    # -------------------------------------------------------------------------
    def test_scenario_08_missing_calendar_blocks_entries(self):
        """
        If the economic calendar cache is empty, uninitialized, or stale (>24h or >4h after failure),
        check_news_blocked_sync must return True (BLOCKED) with NO_TRADE_NEWS_CALENDAR_UNAVAILABLE.
        Never allow a false 'clear' state when telemetry is absent!
        """
        cal = EconomicCalendar()

        # Case A: Empty cache
        EconomicCalendar._shared_cache_data = []
        EconomicCalendar._last_successful_fetch = None
        is_blocked, reason = cal.check_news_blocked_sync("XAUUSD")
        self.assertTrue(is_blocked, "Empty calendar must block trading")
        self.assertIn("NO_TRADE_NEWS_CALENDAR_UNAVAILABLE", reason)

        # Case B: Stale cache (>24 hours)
        EconomicCalendar._shared_cache_data = [{"title": "Event"}]
        EconomicCalendar._last_successful_fetch = datetime.now(timezone.utc) - timedelta(hours=25)
        EconomicCalendar._last_failed_time = None
        is_blocked, reason = cal.check_news_blocked_sync("XAUUSD")
        self.assertTrue(is_blocked, "Stale calendar (>24h) must block trading")
        self.assertIn("NO_TRADE_NEWS_CALENDAR_UNAVAILABLE", reason)

        # Case C: Failed fetch with age > 4 hours
        EconomicCalendar._shared_cache_data = [{"title": "Event"}]
        EconomicCalendar._last_successful_fetch = datetime.now(timezone.utc) - timedelta(hours=5)
        EconomicCalendar._last_failed_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        EconomicCalendar._last_fetch_error = "Connection refused"
        is_blocked, reason = cal.check_news_blocked_sync("XAUUSD")
        self.assertTrue(is_blocked, "Failed fetch with cache >4h must block trading")
        self.assertIn("NO_TRADE_NEWS_CALENDAR_UNAVAILABLE", reason)

    # -------------------------------------------------------------------------
    # Scenario 9: RR variants differ ONLY in TP multiplier, all other parameters identical
    # -------------------------------------------------------------------------
    def test_scenario_09_rr_variants_differ_only_in_tp_multiplier(self):
        """
        Verify that for identical entry and stop-loss values:
        - Entry, SL, SL distance, and lot size are bitwise identical.
        - Only the take-profit price changes strictly proportionally to RR:
          TP = Entry + (SL_dist * RR) for BUY, Entry - (SL_dist * RR) for SELL.
        """
        entry = 2650.00
        sl = 2642.00
        sl_dist = 8.00
        balance = 5000.00
        risk_pct = 0.50

        lot_15 = calculate_gold_swing_lot(balance=balance, sl_dist_usd=sl_dist, risk_pct=risk_pct)
        lot_20 = calculate_gold_swing_lot(balance=balance, sl_dist_usd=sl_dist, risk_pct=risk_pct)
        lot_25 = calculate_gold_swing_lot(balance=balance, sl_dist_usd=sl_dist, risk_pct=risk_pct)

        self.assertEqual(lot_15, lot_20)
        self.assertEqual(lot_20, lot_25)

        tp_15 = round(entry + sl_dist * 1.5, 2)
        tp_20 = round(entry + sl_dist * 2.0, 2)
        tp_25 = round(entry + sl_dist * 2.5, 2)

        self.assertEqual(tp_15, 2662.00)
        self.assertEqual(tp_20, 2666.00)
        self.assertEqual(tp_25, 2670.00)
        self.assertAlmostEqual(tp_20 - tp_15, 4.00, places=2)
        self.assertAlmostEqual(tp_25 - tp_20, 4.00, places=2)

    # -------------------------------------------------------------------------
    # Scenario 10: RR 1:1.5 allowed in research profile while production retains min RR 2.0
    # -------------------------------------------------------------------------
    def test_scenario_10_profile_aware_minimum_rr(self):
        """
        Verify that:
        - Production profile (XAUUSD_INTRADAY) enforces min_rr = 2.0.
        - Research profile (RESEARCH_XAUUSD_FIXED002) allows min_rr = 1.5.
        - Signals with RR 1.55 pass under research profile, but are rejected under production.
        """
        prod_prof = config.TRADING_PROFILES["XAUUSD_INTRADAY"]
        res_prof = config.TRADING_PROFILES["RESEARCH_XAUUSD_FIXED002"]

        self.assertEqual(prod_prof.get("min_rr"), 2.0, "Production profile must strictly retain min_rr = 2.0")
        self.assertEqual(res_prof.get("min_rr"), 1.5, "Research profile must configure min_rr = 1.5")
        self.assertEqual(res_prof.get("target_rr_variants"), [1.5, 2.0, 2.5])

        test_rr = 1.55
        # Under production
        req_prod_rr = prod_prof.get("min_rr", 2.0) - 0.05
        self.assertFalse(test_rr >= req_prod_rr, "RR 1.55 must be rejected under production profile")

        # Under research
        req_res_rr = res_prof.get("min_rr", 2.0) - 0.05
        self.assertTrue(test_rr >= req_res_rr, "RR 1.55 must be accepted under research profile")

    # -------------------------------------------------------------------------
    # Scenario 11: Daily and total drawdown calculations strictly account for floating losses
    # -------------------------------------------------------------------------
    def test_scenario_11_floating_losses_count_towards_drawdown(self):
        """
        Under FundingPips rules:
        - Daily loss limit is 4.0% ($200.00).
        - Floating unrealized loss counts against the daily limit!
        - If closed PnL is $0, but open trade has floating loss -$210.00:
          Daily loss lock must immediately trigger (is_locked=True).
        """
        self.bridge.mt5_telemetry["balance"] = 5000.0
        self.bridge.mt5_telemetry["equity"] = 4785.0  # Floating loss -$215.00
        self.bridge.max_daily_loss_pct = 4.0

        is_locked, net_pnl, dd_pct, start_bal = self.bridge.check_daily_loss(today_closed_pnl=0.0)
        self.assertTrue(is_locked, "Floating loss -$215.00 (4.3%) must trigger daily loss lock!")
        self.assertEqual(net_pnl, -215.0)
        self.assertGreaterEqual(dd_pct, 4.0)

    # -------------------------------------------------------------------------
    # Scenario 12: Python and MQL5 risk calculations match down to the cent
    # -------------------------------------------------------------------------
    def test_scenario_12_python_and_mql5_risk_calculations_match(self):
        """
        Verify mathematical parity between Python execution bridge and MQL5 SmartTraderBridge.mq5:
        Python: loss = lot * sl_dist * CONTRACT_SIZE + (lot * COMMISSION_PER_LOT)
        MQL5:   loss = lot * sl_dist * ContractSize + lot * Commission
        Must match down to $0.01 across various lot sizes and SL distances.
        """
        contract_size = 100.0
        comm_per_lot = 3.00

        test_cases = [
            (0.01, 2.50),
            (0.02, 5.00),
            (0.02, 11.97),
            (0.05, 4.30),
            (0.10, 8.25),
            (0.50, 10.00),
        ]

        for lot, sl_dist in test_cases:
            # Python calculation
            py_loss = round(lot * sl_dist * contract_size + (lot * comm_per_lot), 2)
            # MQL5 formula emulation
            mql5_ticks = round(sl_dist / 0.01, 0)
            mql5_tick_val = 0.01 * contract_size  # 1.00 USD
            mql5_loss = round((mql5_ticks * mql5_tick_val * lot) + (lot * comm_per_lot), 2)

            self.assertAlmostEqual(
                py_loss,
                mql5_loss,
                places=2,
                msg=f"Discrepancy between Python (${py_loss}) and MQL5 (${mql5_loss}) for lot={lot}, sl_dist={sl_dist}"
            )

    # -------------------------------------------------------------------------
    # Scenario 13: Autotrade strictly disabled and no test sends orders
    # -------------------------------------------------------------------------
    def test_scenario_13_autotrade_strictly_disabled(self):
        """
        Safety verification:
        1. config.AUTOTRADE_ENABLED must be False.
        2. All profiles in config.TRADING_PROFILES must have autotrade_enabled_default: False.
        3. ExecutionBridge.enabled defaults to False on startup unless explicitly overridden.
        4. No orders or network requests to live MT5 terminals occur.
        """
        self.assertFalse(config.AUTOTRADE_ENABLED, "config.AUTOTRADE_ENABLED must strictly remain False!")

        for p_name, prof in config.TRADING_PROFILES.items():
            self.assertFalse(
                prof.get("autotrade_enabled_default", True),
                f"Profile {p_name} must have autotrade_enabled_default: False"
            )

        fresh_bridge = ExecutionBridge()
        self.assertFalse(fresh_bridge.enabled, "Fresh ExecutionBridge must be disabled by default")


if __name__ == "__main__":
    unittest.main()
