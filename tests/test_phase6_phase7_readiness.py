"""
Unit tests for Phase 6 (Demo Forward Simulation & Decision Matrix)
and Phase 7 (Prop Challenge Monitoring, Equity Reconciliation & Rule 1 Launch Gate).
"""

import os
import sys
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
from scripts.run_demo_forward_simulation import (
    DemoForwardSessionHarness, get_live_like_spread
)
from scripts.daily_challenge_monitor import (
    ChallengeMonitor, CONFIRMATION_TOKEN
)
from db.database import init_db, set_bot_setting, get_bot_setting
from bot.handlers import cmd_autotrade, cmd_confirm_launch, cmd_revoke_launch


class TestPhase6DemoForwardHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = DemoForwardSessionHarness(initial_balance=5000.0, base_risk_pct=0.25)

    def test_01_spread_fluctuation_model(self):
        """Verify dynamic spread model varies by session and spikes during rollover."""
        # London KZ (e.g. 09:30 UTC on Wednesday)
        dt_london = datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc)
        spread_london = get_live_like_spread(dt_london)
        self.assertTrue(0.20 <= spread_london <= 0.45, f"Unexpected London spread: {spread_london}")

        # NY KZ (e.g. 14:00 UTC)
        dt_ny = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
        spread_ny = get_live_like_spread(dt_ny)
        self.assertTrue(0.25 <= spread_ny <= 0.50, f"Unexpected NY spread: {spread_ny}")

        # Rollover / Off-hours (e.g. 21:30 UTC)
        dt_rollover = datetime(2026, 10, 21, 21, 30, tzinfo=timezone.utc)
        spread_rollover = get_live_like_spread(dt_rollover)
        self.assertTrue(spread_rollover >= 0.85, f"Rollover spread should widen: {spread_rollover}")

    def test_02_harness_initialization_safety(self):
        """Verify harness initializes with autotrade strictly disabled."""
        self.assertFalse(self.harness.bridge.enabled, "Bridge must start disabled")
        self.assertEqual(self.harness.balance, 5000.0)
        self.assertEqual(self.harness.equity, 5000.0)
        self.assertTrue(self.harness.bridge.symbol_specs["XAUUSD"]["broker_confirmed"])

    def test_03_decision_matrix_evaluation(self):
        """Verify Go / No-Go decision matrix evaluates all 8 critical rules."""
        matrix = self.harness._evaluate_decision_matrix()
        self.assertIn("verdict", matrix)
        self.assertIn("rules", matrix)
        self.assertEqual(len(matrix["rules"]), 8)

        rule_names = [r["rule"] for r in matrix["rules"]]
        self.assertTrue(any("Max Daily Drawdown" in n for n in rule_names))
        self.assertTrue(any("Max Total Drawdown" in n for n in rule_names))
        self.assertTrue(any("Hard Equity Floor" in n for n in rule_names))
        self.assertTrue(any("EOD Flat" in n for n in rule_names))
        self.assertTrue(any("Anti-Widening" in n for n in rule_names))
        self.assertTrue(any("Deduplication" in n for n in rule_names))
        self.assertTrue(any("Circuit Breaker" in n for n in rule_names))
        self.assertTrue(any("AUTOTRADE_ENABLED=false" in n for n in rule_names))

    def test_04_session_simulation_execution(self):
        """Verify harness runs across at least 3 sessions cleanly without errors."""
        test_harness = DemoForwardSessionHarness(initial_balance=5000.0, base_risk_pct=0.25)
        # Run on 3 test sessions
        results = test_harness.run_simulation(session_dates=["2026-10-21", "2026-10-22", "2026-10-23"])
        self.assertEqual(len(results["sessions"]), 3)
        self.assertEqual(results["decision_matrix"]["overall_status"], "PASS")
        self.assertTrue(results["decision_matrix"]["verdict"].startswith("GO"))
        self.assertNotIn("NO-GO", results["decision_matrix"]["verdict"])
        self.assertTrue(results["max_daily_drawdown_usd"] < 200.0)
        self.assertTrue(results["max_total_drawdown_usd"] < 600.0)

    def test_05_session_simulation_full_stress_test(self):
        """Verify 5-session simulation executes stress injection, circuit breaker engagement and true drawdown tracking."""
        test_harness = DemoForwardSessionHarness(initial_balance=5000.0, base_risk_pct=0.25)
        results = test_harness.run_simulation()
        self.assertEqual(len(results["sessions"]), 5)
        self.assertEqual(results["decision_matrix"]["overall_status"], "PASS")
        self.assertTrue(results["decision_matrix"]["verdict"].startswith("GO"))
        self.assertNotIn("NO-GO", results["decision_matrix"]["verdict"])
        self.assertGreaterEqual(results["reconciliation_summary"]["circuit_breaker_events_count"], 3)
        # Verify Session 4 factual drawdown calculation
        s4 = results["sessions"][3]
        self.assertTrue(s4["stress_injected"])
        self.assertEqual(s4["max_drawdown_usd"], 37.50)
        self.assertTrue(results["max_daily_drawdown_usd"] >= 37.50)

    def test_06_signals_only_forward_observer(self):
        """Verify signals-only observer initializes, enforces zero orders, and cleanly reports REAL_DEMO_FORWARD_NOT_PERFORMED when offline."""
        from scripts.run_demo_forward_simulation import SignalsOnlyForwardObserver
        observer = SignalsOnlyForwardObserver(target_sessions=5)
        conn = observer.check_mt5_connection()
        self.assertFalse(conn["autotrade_enabled"])

        obs_res = observer.run_observation()
        self.assertEqual(obs_res["observation_mode"], "SIGNALS_ONLY (Zero Orders Dispatched)")
        self.assertFalse(obs_res["autotrade_enabled"])
        self.assertEqual(obs_res["status"], "REAL_DEMO_FORWARD_NOT_PERFORMED")
        self.assertEqual(obs_res["phase6_overall_status"], "PARTIAL")
        self.assertIn("REAL_DEMO_FORWARD_NOT_PERFORMED", obs_res["verdict"])


class TestPhase7ChallengeMonitor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        self.monitor = ChallengeMonitor()
        self.test_token = "SECURE_TEST_LAUNCH_TOKEN_2026"
        os.environ["CHALLENGE_LAUNCH_TOKEN"] = self.test_token

    def test_01_configuration_audit_fundingpips(self):
        """Verify audit passes against FundingPips $5,000 2-step Flex specs."""
        audit = self.monitor.audit_configuration()
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(len(audit["errors"]), 0)
        self.assertEqual(audit["broker"], "FundingPips")
        self.assertIn("Flex", audit["plan_name"])

    async def test_02_equity_reconciliation_metrics(self):
        """Verify equity, drawdown headroom, and target calculations."""
        metrics = await self.monitor.get_equity_metrics()
        self.assertEqual(metrics["initial_balance_usd"], 5000.0)
        self.assertEqual(metrics["equity_floor_usd"], 4400.0)
        self.assertEqual(metrics["hard_equity_stop_usd"], 4450.0)
        self.assertEqual(metrics["daily_loss_limit_usd"], 200.0)
        self.assertEqual(metrics["max_loss_limit_usd"], 600.0)
        self.assertEqual(metrics["phase_1_target_usd"], 500.0)
        self.assertEqual(metrics["phase_2_target_usd"], 400.0)

        # Budget headroom must be non-negative
        self.assertGreaterEqual(metrics["remaining_daily_budget_usd"], 0.0)
        self.assertGreaterEqual(metrics["remaining_max_loss_budget_usd"], 0.0)
        self.assertGreaterEqual(metrics["cushion_to_hard_stop_usd"], 0.0)

    async def test_03_phase7_rule1_launch_confirmation_gate(self):
        """
        Verify Rule 1: 'Запустить челлендж только после ручного подтверждения пользователя'
        - Unconfirmed initially -> LOCKED_FAIL_CLOSED
        - Unauthorized non-admin user -> Rejected
        - Invalid token -> Rejected without secret leakage
        - Valid token -> Confirmed (one-time consumed), strictly respects AUTOTRADE_ENABLED=false
        - Token reuse -> Blocked by single-use consumption
        - Revoke -> Restored to LOCKED_FAIL_CLOSED
        """
        # Ensure fresh state
        await self.monitor.revoke_challenge_launch()

        # Step 1: Initial state must be unconfirmed and locked
        metrics = await self.monitor.get_equity_metrics()
        gate = metrics["phase7_rule1_launch_gate"]
        self.assertFalse(gate["is_confirmed"])
        self.assertEqual(gate["routing_status"], "LOCKED_FAIL_CLOSED")

        # Step 2A: Non-admin user must be rejected
        ok_non_admin, msg_non_admin = await self.monitor.confirm_challenge_launch(
            user_id="unauthorized_user_999", confirmation_token=self.test_token
        )
        self.assertFalse(ok_non_admin)
        self.assertIn("not an authorized administrator", msg_non_admin)

        # Step 2B: Confirmation with invalid token must fail
        ok_bad, msg_bad = await self.monitor.confirm_challenge_launch(
            user_id="test_admin", confirmation_token="WRONG_TOKEN"
        )
        self.assertFalse(ok_bad)
        self.assertIn("REJECTED", msg_bad)
        self.assertNotIn(self.test_token, msg_bad, "Error message must never leak secret token")

        # Step 3: Confirmation with valid token succeeds
        ok_good, msg_good = await self.monitor.confirm_challenge_launch(
            user_id="test_admin", confirmation_token=self.test_token
        )
        self.assertTrue(ok_good)
        self.assertIn("USER MANUAL CONFIRMATION REGISTERED", msg_good)

        # Verify gate updated
        metrics2 = await self.monitor.get_equity_metrics()
        gate2 = metrics2["phase7_rule1_launch_gate"]
        self.assertTrue(gate2["is_confirmed"])
        self.assertEqual(gate2["confirmed_by"], "test_admin")

        # Invariant check: because AUTOTRADE_ENABLED is False in .env, routing remains fail-closed
        self.assertFalse(gate2["autotrade_enabled_config"])
        self.assertEqual(gate2["routing_status"], "LOCKED_FAIL_CLOSED")

        # Step 4: Token single-use consumption check
        # Clear confirmation flag manually to test that token was consumed and cannot be re-used
        await set_bot_setting("challenge_user_confirmed_at", "")
        ok_reuse, msg_reuse = await self.monitor.confirm_challenge_launch(
            user_id="test_admin", confirmation_token=self.test_token
        )
        self.assertFalse(ok_reuse)
        self.assertIn("already consumed", msg_reuse)

        # Step 5: Revocation works cleanly and resets consumption state
        ok_rev, msg_rev = await self.monitor.revoke_challenge_launch()
        self.assertTrue(ok_rev)
        metrics3 = await self.monitor.get_equity_metrics()
        self.assertFalse(metrics3["phase7_rule1_launch_gate"]["is_confirmed"])

    def test_04_safety_invariant_autotrade_disabled(self):
        """Strictly verify AUTOTRADE_ENABLED=false is intact in config and .env."""
        self.assertFalse(config.AUTOTRADE_ENABLED, "AUTOTRADE_ENABLED must strictly remain False")

    async def test_05_bot_handler_rule1_enforcement(self):
        """Verify bot handlers enforce Rule 1 confirmation and block autotrade when disabled."""
        from unittest.mock import AsyncMock, MagicMock
        from db.database import set_bot_setting, get_bot_setting

        # Step A: Revoke any existing confirmation
        await self.monitor.revoke_challenge_launch()
        await set_bot_setting("autotrade_enabled", "false")

        # Mock Message from admin
        mock_msg = MagicMock()
        mock_msg.from_user.id = config.ADMIN_ID
        mock_msg.answer = AsyncMock()

        # Step B: /autotrade on without user confirmation -> BLOCKED by Rule 1
        mock_msg.text = "/autotrade on"
        await cmd_autotrade(mock_msg)
        mock_msg.answer.assert_called()
        ans_text = mock_msg.answer.call_args[0][0]
        self.assertIn("ПРАВИЛО 1 ФАЗЫ 7", ans_text)
        self.assertEqual(await get_bot_setting("autotrade_enabled", "false"), "false")

        # Step C: /confirm_launch with valid token -> SUCCESS
        mock_msg.text = f"/confirm_launch {self.test_token}"
        await cmd_confirm_launch(mock_msg)
        self.assertTrue(await get_bot_setting("challenge_user_confirmed_at", ""))

        # Step D: /autotrade on with confirmation, but AUTOTRADE_ENABLED=false in config -> BLOCKED
        mock_msg.text = "/autotrade on"
        await cmd_autotrade(mock_msg)
        ans_text2 = mock_msg.answer.call_args[0][0]
        self.assertIn("AUTOTRADE_ENABLED=false", ans_text2)
        self.assertEqual(await get_bot_setting("autotrade_enabled", "false"), "false")

        # Step E: Revocation via command works cleanly
        mock_msg.text = "/revoke_launch"
        await cmd_revoke_launch(mock_msg)
        self.assertEqual(await get_bot_setting("challenge_user_confirmed_at", ""), "")


if __name__ == "__main__":
    unittest.main()
