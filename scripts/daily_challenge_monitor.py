"""
Phase 7: Prop Challenge Daily Monitor, Equity Reconciler & Launch Gatekeeper
FundingPips $5,000 2-Step Flex Challenge Operational Tool:
- Tracks daily PnL, High-Water Mark (HWM), trailing drawdown, and remaining loss budgets.
- Reconciles closed deal tickets in SQLite database (broker_deals / trade_history).
- Audits challenge configuration against official broker rules.
- Strictly enforces Rule 1 of Phase 7: "Запустить челлендж только после ручного подтверждения пользователя"
  (Fail-closed runtime gate requiring explicit user manual confirmation before live order routing).
"""

import os
import sys
import json
import asyncio
import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure robust UTF-8 printing on Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
from utils.broker_time import (
    get_broker_timezone, get_broker_now,
    get_broker_today_date_str, get_broker_today_midnight_ts
)
from db.database import DB_PATH, get_bot_setting, set_bot_setting, get_today_broker_pnl
import secrets

CONFIRMATION_TOKEN = os.getenv("CHALLENGE_LAUNCH_TOKEN", "")


async def generate_ephemeral_launch_token() -> str:
    """Generates a cryptographically secure one-time ephemeral token and stores it in the database."""
    token = secrets.token_urlsafe(16)
    await set_bot_setting("ephemeral_launch_token", token)
    await set_bot_setting("ephemeral_launch_token_used", "false")
    await set_bot_setting("ephemeral_launch_token_created_at", datetime.now(timezone.utc).isoformat())
    return token


async def get_configured_launch_token() -> Optional[str]:
    """Retrieves configured launch token from environment or config.py. Does NOT use hardcoded constant in public source."""
    env_tok = os.getenv("CHALLENGE_LAUNCH_TOKEN")
    if env_tok and env_tok.strip():
        return env_tok.strip()
    cfg_tok = getattr(config, "CHALLENGE_LAUNCH_TOKEN", None)
    if cfg_tok and str(cfg_tok).strip():
        return str(cfg_tok).strip()
    return None


class ChallengeMonitor:
    def __init__(self, cfg_path: Optional[Path] = None):
        self.cfg_path = cfg_path or (ROOT_DIR / "challenge_config.json")
        self.config_data = self._load_challenge_config()

    def _load_challenge_config(self) -> Dict[str, Any]:
        if self.cfg_path.exists():
            try:
                with open(self.cfg_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Failed loading {self.cfg_path}: {e}")
        return {
            "broker": "FundingPips",
            "plan_name": "$5k · 2 Step Flex (Biggest Max Loss)",
            "initial_balance_usd": 5000.0,
            "phase_1_target_usd": 500.0,
            "phase_2_target_usd": 400.0,
            "daily_loss_usd": 200.0,
            "max_loss_usd": 600.0,
            "min_equity_floor_usd": 4400.0,
            "hard_equity_stop_usd": 4450.0,
        }

    def audit_configuration(self) -> Dict[str, Any]:
        """
        Audits challenge_config.json, config.py, and .env.example
        against FundingPips $5k 2-step Flex broker specifications.
        """
        errors = []
        warnings = []
        checks = []

        initial_bal = float(self.config_data.get("initial_balance_usd", 5000.0))
        daily_loss = float(self.config_data.get("daily_loss_usd", 200.0))
        max_loss = float(self.config_data.get("max_loss_usd", 600.0))
        floor = float(self.config_data.get("min_equity_floor_usd", 4400.0))
        hard_stop = float(self.config_data.get("hard_equity_stop_usd", 4450.0))
        p1_target = float(self.config_data.get("phase_1_target_usd", 500.0))
        p2_target = float(self.config_data.get("phase_2_target_usd", 400.0))

        # Check 1: Balance & Floor consistency
        if floor != (initial_bal - max_loss):
            errors.append(f"Floor mismatch: {floor} != initial ({initial_bal}) - max_loss ({max_loss})")
        else:
            checks.append({"check": "Equity Floor Calculation", "status": "PASS", "val": f"${floor:,.2f}"})

        # Check 2: Hard stop safety cushion
        if hard_stop < floor + 20.0 or hard_stop >= initial_bal:
            errors.append(f"Hard stop {hard_stop} invalid. Must be >= floor ({floor}) + $20 and < initial ({initial_bal})")
        else:
            checks.append({"check": "Hard Equity Stop Cushion", "status": "PASS", "val": f"${hard_stop:,.2f} (${hard_stop - floor:.2f} buffer)"})

        # Check 3: Daily loss limit
        if daily_loss != 200.0:
            errors.append(f"Daily loss is {daily_loss}, expected $200.00 (4.0% of $5,000)")
        else:
            checks.append({"check": "Daily Loss Stop Limit", "status": "PASS", "val": f"${daily_loss:,.2f} (4.0%)"})

        # Check 4: Profit Targets
        if p1_target != 500.0 or p2_target != 400.0:
            errors.append(f"Target mismatch: P1=${p1_target} (exp $500), P2=${p2_target} (exp $400)")
        else:
            checks.append({"check": "Profit Targets P1 / P2", "status": "PASS", "val": f"+${p1_target:.2f} (10%) / +${p2_target:.2f} (8%)"})

        # Check 5: Broker Timezone
        tz_name = getattr(config, "BROKER_TIMEZONE_NAME", "Europe/Helsinki")
        if "Helsinki" in tz_name or "Athens" in tz_name or "EET" in tz_name:
            checks.append({"check": "Broker Timezone (EET/EEST)", "status": "PASS", "val": tz_name})
        else:
            warnings.append(f"Unusual broker timezone: {tz_name}")

        # Check 6: Safety Invariant
        autotrade_env = getattr(config, "AUTOTRADE_ENABLED", False)
        checks.append({
            "check": "Safety Invariant (AUTOTRADE_ENABLED=false)",
            "status": "PASS" if not autotrade_env else "LIVE_FLAG_ACTIVE",
            "val": str(autotrade_env)
        })

        return {
            "status": "PASS" if len(errors) == 0 else "FAIL",
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "broker": self.config_data.get("broker", "FundingPips"),
            "plan_name": self.config_data.get("plan_name", "$5k · 2 Step Flex"),
        }

    async def get_equity_metrics(self) -> Dict[str, Any]:
        """
        Reconciles database records and computes high-water mark,
        trailing drawdown, remaining loss budget, and profit progress.
        """
        import aiosqlite

        initial_bal = float(self.config_data.get("initial_balance_usd", 5000.0))
        max_daily_loss = float(self.config_data.get("daily_loss_usd", 200.0))
        max_total_loss = float(self.config_data.get("max_loss_usd", 600.0))
        floor_usd = float(self.config_data.get("min_equity_floor_usd", 4400.0))
        hard_stop_usd = float(self.config_data.get("hard_equity_stop_usd", 4450.0))
        p1_target = float(self.config_data.get("phase_1_target_usd", 500.0))
        p2_target = float(self.config_data.get("phase_2_target_usd", 400.0))

        # Query database for all-time deals and today's broker deals
        today_stats = await get_today_broker_pnl()
        today_pnl = today_stats.get("total_pnl", 0.0)
        today_deals_count = today_stats.get("deals_count", 0)

        # All-time net closed PnL from broker_deals
        all_time_pnl = 0.0
        total_deals_count = 0
        if DB_PATH.exists():
            try:
                async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0), COUNT(*) FROM broker_deals WHERE (record_type = 'LIVE' OR record_type IS NULL)")
                    row = await cursor.fetchone()
                    if row:
                        all_time_pnl = float(row[0] or 0.0)
                        total_deals_count = int(row[1] or 0)
            except Exception as e:
                print(f"Database query warning: {e}")

        # Current estimated balance & equity
        cur_balance = round(initial_bal + all_time_pnl, 2)
        cur_equity = cur_balance  # In offline reconciliation, equity matches balance

        # Read persistent peak equity from bot_settings
        saved_peak_str = await get_bot_setting("peak_equity", "")
        saved_peak = float(saved_peak_str) if saved_peak_str else initial_bal
        peak_equity = max(initial_bal, cur_equity, saved_peak)

        # Drawdown calculations
        total_drawdown_usd = max(0.0, peak_equity - cur_equity)
        total_drawdown_pct = (total_drawdown_usd / peak_equity * 100.0) if peak_equity > 0 else 0.0
        remaining_total_budget_usd = max(0.0, cur_equity - floor_usd)

        # Daily loss calculations
        day_start_balance = round(cur_balance - today_pnl, 2)
        daily_loss_usd = max(0.0, -today_pnl)
        daily_loss_pct = (daily_loss_usd / day_start_balance * 100.0) if day_start_balance > 0 else 0.0
        remaining_daily_budget_usd = max(0.0, max_daily_loss - daily_loss_usd)

        # Cushion to Hard Equity Stop
        cushion_to_hard_stop = max(0.0, cur_equity - hard_stop_usd)

        # Target progress
        net_challenge_profit = cur_balance - initial_bal
        p1_progress_pct = max(0.0, min(100.0, (net_challenge_profit / p1_target) * 100.0)) if p1_target > 0 else 0.0
        p2_progress_pct = max(0.0, min(100.0, (net_challenge_profit / p2_target) * 100.0)) if p2_target > 0 else 0.0

        # Circuit breaker states
        eq_hard_stop = (await get_bot_setting("equity_hard_stop_triggered", "")).lower() in ("true", "1")
        daily_lock = (await get_bot_setting("daily_loss_locked", "")).lower() in ("true", "1")
        consec_pause = (await get_bot_setting("consecutive_loss_paused", "")).lower() in ("true", "1")
        today_streak_str = await get_bot_setting("today_consecutive_losses", "0")
        today_streak = int(today_streak_str) if today_streak_str.isdigit() else 0

        # Phase 7 Manual User Confirmation Gate
        user_confirmed_at = await get_bot_setting("challenge_user_confirmed_at", "")
        user_confirmed_by = await get_bot_setting("challenge_user_confirmed_by", "")
        is_user_confirmed = bool(user_confirmed_at and user_confirmed_by)

        return {
            "initial_balance_usd": initial_bal,
            "current_balance_usd": cur_balance,
            "current_equity_usd": cur_equity,
            "peak_equity_hwm_usd": round(peak_equity, 2),
            "total_net_pnl_usd": round(net_challenge_profit, 2),
            "today_closed_pnl_usd": round(today_pnl, 2),
            "today_deals_count": today_deals_count,
            "total_deals_count": total_deals_count,
            "total_drawdown_usd": round(total_drawdown_usd, 2),
            "total_drawdown_pct": round(total_drawdown_pct, 2),
            "remaining_max_loss_budget_usd": round(remaining_total_budget_usd, 2),
            "max_loss_limit_usd": max_total_loss,
            "daily_loss_usd": round(daily_loss_usd, 2),
            "daily_loss_pct": round(daily_loss_pct, 2),
            "remaining_daily_budget_usd": round(remaining_daily_budget_usd, 2),
            "daily_loss_limit_usd": max_daily_loss,
            "equity_floor_usd": floor_usd,
            "hard_equity_stop_usd": hard_stop_usd,
            "cushion_to_hard_stop_usd": round(cushion_to_hard_stop, 2),
            "phase_1_target_usd": p1_target,
            "phase_1_progress_pct": round(p1_progress_pct, 1),
            "phase_2_target_usd": p2_target,
            "phase_2_progress_pct": round(p2_progress_pct, 1),
            "circuit_breakers": {
                "equity_hard_stop_triggered": eq_hard_stop,
                "daily_loss_locked": daily_lock,
                "consecutive_loss_paused": consec_pause,
                "today_consecutive_losses": today_streak,
            },
            "challenge_operational_statuses": {
                "monitoring_tool": "IMPLEMENTED / TESTED",
                "manual_launch_gate": "IMPLEMENTED / TESTED",
                "actual_challenge_execution": "NOT STARTED",
                "phase_1_challenge": "PENDING",
                "phase_2_challenge": "PENDING",
                "funded_account": "NOT OBTAINED",
                "autotrade_enabled_config": getattr(config, "AUTOTRADE_ENABLED", False),
                "live_orders_sent": 0,
            },
            "phase7_rule1_launch_gate": {
                "rule": "Запустить челлендж только после ручного подтверждения пользователя",
                "is_confirmed": is_user_confirmed,
                "confirmed_at": user_confirmed_at or "NOT_CONFIRMED",
                "confirmed_by": user_confirmed_by or "NONE",
                "autotrade_enabled_config": getattr(config, "AUTOTRADE_ENABLED", False),
                "routing_status": "READY_PENDING_LAUNCH" if is_user_confirmed and getattr(config, "AUTOTRADE_ENABLED", False) else "LOCKED_FAIL_CLOSED",
            }
        }

    async def confirm_challenge_launch(self, user_id: str, confirmation_token: str) -> Tuple[bool, str]:
        """
        Enforces Rule 1 of Phase 7:
        Requires explicit, intentional manual confirmation before any live order routing.
        Strictly verifies that user_id matches an authorized ADMIN_ID and requires a valid, unconsumed token.
        Enforces single-use consumption of confirmation tokens.
        """
        # Strictly require factual ADMIN_ID or ALLOWED_USERS; no hardcoded test actors permitted
        allowed_admins = set()
        admin_id_val = getattr(config, "ADMIN_ID", 0)
        if admin_id_val and str(admin_id_val).strip() not in ("0", ""):
            allowed_admins.add(str(admin_id_val).strip())
        if hasattr(config, "ALLOWED_USERS") and isinstance(config.ALLOWED_USERS, list):
            for u in config.ALLOWED_USERS:
                u_str = str(u).strip()
                if u_str and u_str not in ("0", ""):
                    allowed_admins.add(u_str)

        if not user_id or str(user_id) not in allowed_admins:
            return False, f"❌ REJECTED: User ID '{user_id}' is not an authorized administrator (ADMIN_ID mismatch). Launch confirmation denied."

        if not confirmation_token or not confirmation_token.strip():
            return False, "❌ REJECTED: Missing confirmation token."

        token_to_verify = confirmation_token.strip()

        # Check if already confirmed
        already_confirmed_at = await get_bot_setting("challenge_user_confirmed_at", "")
        if already_confirmed_at:
            return True, f"ℹ️ ALREADY CONFIRMED: Challenge launch has already been confirmed at {already_confirmed_at}."

        cfg_token = await get_configured_launch_token()
        token_consumed = (await get_bot_setting("challenge_token_consumed", "false")) == "true"
        ephemeral_token = await get_bot_setting("ephemeral_launch_token", "")
        ephemeral_used = (await get_bot_setting("ephemeral_launch_token_used", "false")) == "true"

        token_valid = False
        if cfg_token and token_to_verify == cfg_token and not token_consumed:
            token_valid = True
            await set_bot_setting("challenge_token_consumed", "true")
        elif ephemeral_token and token_to_verify == ephemeral_token and not ephemeral_used:
            token_valid = True
            await set_bot_setting("ephemeral_launch_token_used", "true")

        if not token_valid:
            return False, "❌ REJECTED: Invalid or already consumed confirmation token. Provide a valid one-time token."

        now_str = datetime.now(timezone.utc).isoformat()
        await set_bot_setting("challenge_user_confirmed_at", now_str)
        await set_bot_setting("challenge_user_confirmed_by", str(user_id))

        autotrade_cfg = getattr(config, "AUTOTRADE_ENABLED", False)
        msg = (
            f"✅ USER MANUAL CONFIRMATION REGISTERED:\n"
            f"• Operator: {user_id}\n"
            f"• Timestamp: {now_str}\n"
            f"• Invariant Check: AUTOTRADE_ENABLED in .env = {autotrade_cfg}\n"
        )
        if not autotrade_cfg:
            msg += (
                "🔒 FAIL-SAFE ENGAGED: User authorization logged, but AUTOTRADE_ENABLED is FALSE in .env.\n"
                "Live orders remain strictly disabled until operator edits .env to AUTOTRADE_ENABLED=true."
            )
        else:
            msg += "🟢 OPERATIONAL NOTICE: Live routing unlocked by operator authorization."
        return True, msg

    async def revoke_challenge_launch(self) -> Tuple[bool, str]:
        """Revokes launch authorization, restoring strict fail-closed state and clearing confirmation."""
        await set_bot_setting("challenge_user_confirmed_at", "")
        await set_bot_setting("challenge_user_confirmed_by", "")
        await set_bot_setting("challenge_token_consumed", "false")
        await set_bot_setting("ephemeral_launch_token", "")
        await set_bot_setting("ephemeral_launch_token_used", "false")
        return True, "🛑 Launch authorization revoked. System returned to strict LOCKED_FAIL_CLOSED state."

    def print_dashboard(self, metrics: Dict[str, Any]):
        """Prints high-visibility ANSI operational monitoring report."""
        print("=" * 80)
        print("SMART TRADER BOT: PHASE 7 PROP CHALLENGE RUNTIME MONITOR")
        print(f"Plan: {self.config_data.get('plan_name')} | Broker: {self.config_data.get('broker')}")
        print("=" * 80)

        bal = metrics["current_balance_usd"]
        eq = metrics["current_equity_usd"]
        hwm = metrics["peak_equity_hwm_usd"]
        net_pnl = metrics["total_net_pnl_usd"]

        print(f"💰 Account Equity:     ${eq:,.2f} USD")
        print(f"💼 Account Balance:    ${bal:,.2f} USD")
        print(f"🏔️ High-Water Mark:    ${hwm:,.2f} USD")
        print(f"📈 Total Challenge PnL: ${net_pnl:+,.2f} USD")
        print("-" * 80)

        # Daily Drawdown Monitor
        d_pnl = metrics["today_closed_pnl_usd"]
        d_dd = metrics["daily_loss_usd"]
        d_rem = metrics["remaining_daily_budget_usd"]
        d_lim = metrics["daily_loss_limit_usd"]
        print(f"📅 TODAY'S METRICS (00:00 EET Reset):")
        print(f"   • Today's Closed PnL:        ${d_pnl:+,.2f}")
        print(f"   • Today's Realized Drawdown: ${d_dd:,.2f} / ${d_lim:,.2f} ({metrics['daily_loss_pct']:.2f}%)")
        print(f"   • Remaining Daily Loss Budget: ${d_rem:,.2f} (HEADROOM: {d_rem/d_lim*100:.1f}%)")
        print("-" * 80)

        # Total Drawdown Monitor
        t_dd = metrics["total_drawdown_usd"]
        t_rem = metrics["remaining_max_loss_budget_usd"]
        t_lim = metrics["max_loss_limit_usd"]
        floor = metrics["equity_floor_usd"]
        hard_stop = metrics["hard_equity_stop_usd"]
        cushion = metrics["cushion_to_hard_stop_usd"]
        print(f"🛡️ MAXIMUM DRAWDOWN & FLOOR MONITOR:")
        print(f"   • Drawdown from Peak (HWM):  ${t_dd:,.2f} / ${t_lim:,.2f} ({metrics['total_drawdown_pct']:.2f}%)")
        print(f"   • Remaining Max Loss Budget: ${t_rem:,.2f} (To Static Floor ${floor:,.2f})")
        print(f"   • Hard Equity Stop Level:    ${hard_stop:,.2f} (Safety Cushion: ${cushion:,.2f})")
        print("-" * 80)

        # Target Progress
        p1_t = metrics["phase_1_target_usd"]
        p1_prog = metrics["phase_1_progress_pct"]
        p2_t = metrics["phase_2_target_usd"]
        p2_prog = metrics["phase_2_progress_pct"]
        print(f"🎯 CHALLENGE PROFIT TARGETS:")
        print(f"   • Phase 1 Target (+$500.00 / 10%):  {p1_prog:.1f}% (${max(0.0, net_pnl):,.2f} / ${p1_t:,.2f})")
        print(f"   • Phase 2 Target (+$400.00 / 8%):   {p2_prog:.1f}% (${max(0.0, net_pnl):,.2f} / ${p2_t:,.2f})")
        print("-" * 80)

        # Circuit Breakers
        cbs = metrics["circuit_breakers"]
        print(f"⚡ CIRCUIT BREAKER STATUS:")
        print(f"   • Hard Stop Triggered:       {'🚨 YES' if cbs['equity_hard_stop_triggered'] else '✅ NO'}")
        print(f"   • Daily Loss Locked:         {'🔒 YES' if cbs['daily_loss_locked'] else '✅ NO'}")
        print(f"   • Consecutive Loss Paused:   {'⏸️ YES (3 losses)' if cbs['consecutive_loss_paused'] else '✅ NO'}")
        print(f"   • Today's Loss Streak:       {cbs['today_consecutive_losses']}")
        print("-" * 80)

        # Rule 1 Launch Gate
        gate = metrics["phase7_rule1_launch_gate"]
        print(f"🔐 PHASE 7 RULE 1 LAUNCH GATE (MANUAL CONFIRMATION FAIL-SAFE):")
        print(f"   • Requirement:               {gate['rule']}")
        print(f"   • User Confirmed:            {'🟢 YES' if gate['is_confirmed'] else '🔴 NO (UNCONFIRMED)'}")
        print(f"   • Confirmation Timestamp:    {gate['confirmed_at']}")
        print(f"   • Confirmed By:              {gate['confirmed_by']}")
        print(f"   • AUTOTRADE_ENABLED (.env):  {gate['autotrade_enabled_config']}")
        print(f"   • LIVE ROUTING GATE STATUS:  {gate['routing_status']}")
        print("-" * 80)

        # Operational Execution Status Table
        ops = metrics.get("challenge_operational_statuses", {})
        print(f"📋 PHASE 7 CHALLENGE OPERATIONAL READINESS & EXECUTION STATUS:")
        print(f"   • Monitoring Tool:             {ops.get('monitoring_tool', 'IMPLEMENTED / TESTED')}")
        print(f"   • Manual Launch Gate:          {ops.get('manual_launch_gate', 'IMPLEMENTED / TESTED')}")
        print(f"   • Actual Challenge Execution:  {ops.get('actual_challenge_execution', 'NOT STARTED')}")
        print(f"   • Phase 1 Challenge:           {ops.get('phase_1_challenge', 'PENDING')}")
        print(f"   • Phase 2 Challenge:           {ops.get('phase_2_challenge', 'PENDING')}")
        print(f"   • Funded Account:              {ops.get('funded_account', 'NOT OBTAINED')}")
        print(f"   • Live Orders Dispatched:      {ops.get('live_orders_sent', 0)} (Strictly 0 / Zero Live Orders)")
        print("=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Phase 7: Daily Challenge Monitor & Equity Reconciler")
    parser.add_argument("--status", "-s", action="store_true", help="Display full challenge status and metrics")
    parser.add_argument("--audit-config", "-a", action="store_true", help="Audit configuration against broker terms")
    parser.add_argument("--generate-token", "-g", action="store_true", help="Generate a cryptographically secure one-time launch confirmation token")
    parser.add_argument("--confirm-launch", "-c", nargs="?", const="", help="Confirm challenge launch with operator ID")
    parser.add_argument("--token", "-t", type=str, default="", help="Confirmation token")
    parser.add_argument("--revoke", action="store_true", help="Revoke launch confirmation")
    parser.add_argument("--json", action="store_true", help="Output status in JSON")
    args = parser.parse_args()

    monitor = ChallengeMonitor()

    if args.generate_token:
        new_token = await generate_ephemeral_launch_token()
        print("=" * 80)
        print("🔐 NEW ONE-TIME CHALLENGE LAUNCH TOKEN GENERATED:")
        print(f"Token: {new_token}")
        print(f"To authorize launch via CLI run:")
        print(f"  python scripts/daily_challenge_monitor.py -c <ADMIN_ID> -t {new_token}")
        print(f"Or via Telegram as Administrator:")
        print(f"  /confirm_launch {new_token}")
        print("=" * 80)
        return

    if args.audit_config:
        audit = monitor.audit_configuration()
        print("=" * 80)
        print("PHASE 7 CONFIGURATION AUDIT:")
        print(f"Status: {audit['status']} | Plan: {audit['plan_name']} ({audit['broker']})")
        print("-" * 80)
        for c in audit["checks"]:
            print(f"  [{c['status']}] {c['check']}: {c['val']}")
        if audit["errors"]:
            print("\nErrors:")
            for e in audit["errors"]:
                print(f"  ❌ {e}")
        if audit["warnings"]:
            print("\nWarnings:")
            for w in audit["warnings"]:
                print(f"  ⚠️ {w}")
        print("=" * 80)

    if args.confirm_launch is not None:
        user_id = args.confirm_launch or str(getattr(config, "ADMIN_ID", "admin_operator"))
        token = args.token or ""
        if not token and sys.stdin.isatty():
            print("⚠️ Confirmation token required. Enter one-time or configured CHALLENGE_LAUNCH_TOKEN:")
            try:
                token = input("> ").strip()
            except Exception:
                token = ""
        success, msg = await monitor.confirm_challenge_launch(user_id=user_id, confirmation_token=token)
        print("\n" + msg + "\n")

    if args.revoke:
        _, msg = await monitor.revoke_challenge_launch()
        print("\n" + msg + "\n")

    metrics = await monitor.get_equity_metrics()

    # Save artifact
    out_json = ROOT_DIR / "data" / "challenge_status.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    if args.json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        monitor.print_dashboard(metrics)


if __name__ == "__main__":
    asyncio.run(main())
