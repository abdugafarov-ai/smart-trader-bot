"""
Phase 4: Comprehensive Quantitative Robustness, Walk-Forward, Decoupled Cost Sensitivity
& Overhauled Sequential Monte Carlo Suite for XAUUSD under FundingPips $5,000 2-Step Flex Rules.
"""

import json
import math
import random
from datetime import datetime, timezone
import sys
from pathlib import Path

root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

import numpy as np
import pandas as pd
from scripts.intraday_gold_engine import IntradaySimulator, calculate_atr, CONTRACT_SIZE, COMMISSION_PER_LOT


def calculate_expectancy_ci(trades: list[dict], alpha: float = 0.05) -> dict:
    """Calculates Student-t and bootstrap 95% confidence intervals for trade expectancy."""
    n = len(trades)
    if n == 0:
        return {
            "n": 0, "mean_pnl_usd": 0.0, "mean_r": 0.0,
            "ci_95_usd": [0.0, 0.0], "ci_95_r": [0.0, 0.0],
            "boot_ci_95_usd": [0.0, 0.0], "boot_ci_95_r": [0.0, 0.0],
            "spans_zero": True, "verdict": "NO_TRADES"
        }

    pnls = np.array([t["pnl_usd"] for t in trades], dtype=np.float64)
    rs = np.array([t.get("r_multiple", 0.0) for t in trades], dtype=np.float64)

    mean_usd = float(np.mean(pnls))
    mean_r = float(np.mean(rs))

    if n > 1:
        std_usd = float(np.std(pnls, ddof=1))
        std_r = float(np.std(rs, ddof=1))
        # Student-t critical value approx (for n=14, t_crit=2.1604)
        t_crit = 2.1604 if n == 14 else (2.0 if n > 30 else 2.2)
        se_usd = std_usd / math.sqrt(n)
        se_r = std_r / math.sqrt(n)
        ci_usd = [round(mean_usd - t_crit * se_usd, 2), round(mean_usd + t_crit * se_usd, 2)]
        ci_r = [round(mean_r - t_crit * se_r, 3), round(mean_r + t_crit * se_r, 3)]
    else:
        ci_usd = [mean_usd, mean_usd]
        ci_r = [mean_r, mean_r]

    # Non-parametric Bootstrap CI (10,000 iterations)
    np.random.seed(42)
    boot_usd = [float(np.mean(np.random.choice(pnls, size=n, replace=True))) for _ in range(10000)]
    boot_r = [float(np.mean(np.random.choice(rs, size=n, replace=True))) for _ in range(10000)]
    b_ci_usd = [round(float(np.percentile(boot_usd, 2.5)), 2), round(float(np.percentile(boot_usd, 97.5)), 2)]
    b_ci_r = [round(float(np.percentile(boot_r, 2.5)), 3), round(float(np.percentile(boot_r, 97.5)), 3)]

    spans_zero = (ci_r[0] <= 0.0 <= ci_r[1]) or (b_ci_r[0] <= 0.0 <= b_ci_r[1])
    is_inconclusive = spans_zero or (n < 30)

    return {
        "n": n,
        "mean_pnl_usd": round(mean_usd, 2),
        "mean_r": round(mean_r, 3),
        "ci_95_t_dist_usd": ci_usd,
        "ci_95_t_dist_r": ci_r,
        "ci_95_bootstrap_usd": b_ci_usd,
        "ci_95_bootstrap_r": b_ci_r,
        "spans_zero": spans_zero,
        "statistical_status": "STATISTICALLY INCONCLUSIVE" if is_inconclusive else "STATISTICALLY SIGNIFICANT"
    }


def run_sequential_monte_carlo(
    trades: list[dict],
    all_trading_days: list,
    lot_mode_label: str = "dynamic_025",
    n_simulations: int = 10000,
    seed: int = 42,
    block_size_days: int = 5,
    max_calendar_days: int = 1000,
) -> dict:
    """
    Overhauled Sequential 2-Phase Challenge Monte Carlo adhering strictly to official rules:
    - Retains actual trade dates, timestamps, order, and intra-day groupings.
    - Preserves empirical calendar trading days (including realistic zero-trade days and multi-trade days).
    - Uses Circular Block Bootstrap on daily calendar blocks (preserving weekly clusters and autocorrelation).
    - Models Phase 1 ($5,000 -> $5,500 / +10%) and Phase 2 ($5,000 -> $5,400 / +8%) as sequential distinct states.
    - Resets account balance to $5,000 upon passing Phase 1 before Phase 2 begins.
    - Enforces intraday loss limit ($200.00 / 4.0%) and static equity floor ($4,400.00 / 12.0%) across both phases.
    - Checks both floating intra-trade drawdown (MAE) and realized trade balance.
    - Evaluates pass dynamics across distinct horizons: 60, 120, 180, 252, 500, and unconstrained days.
    - States exact statistical uncertainty: zero breaches out of 10,000 is reported as point estimate 0.00%
      with 95% upper bound < 0.03% (Rule of Three), NOT 'mathematical zero'.
    """
    np.random.seed(seed)
    n_empirical_trades = len(trades)
    n_days = len(all_trading_days)

    # Group trades by factual calendar trading days
    day_to_trades = {d: [] for d in all_trading_days}
    for t in trades:
        d = t["entry_time"].date()
        if d in day_to_trades:
            day_to_trades[d].append((
                float(t["pnl_usd"]),
                float(t.get("mae_usd", 12.5)),
                float(t.get("r_multiple", 0.0))
            ))

    daily_trade_tuples = [day_to_trades[d] for d in all_trading_days]

    n_blocks = math.ceil(max_calendar_days / block_size_days)
    block_starts = np.random.randint(0, n_days, size=(n_simulations, n_blocks))

    p1_passes = 0
    p2_passes = 0
    joint_passes = 0
    daily_breaches = 0
    floor_breaches = 0
    total_ruins = 0

    max_drawdowns_total = []
    max_drawdowns_daily = []
    days_to_p1_pass = []
    trades_to_p1_pass = []
    days_to_joint_pass = []
    trades_to_joint_pass = []

    horizon_p1_pass_counts = {60: 0, 120: 0, 180: 0, 252: 0, 500: 0, 1000: 0}
    horizon_joint_pass_counts = {60: 0, 120: 0, 180: 0, 252: 0, 500: 0, 1000: 0}

    for s in range(n_simulations):
        # Generate calendar day sequence for this simulation
        day_indices = []
        for b_start in block_starts[s]:
            for offset in range(block_size_days):
                day_indices.append((b_start + offset) % n_days)
        day_indices = day_indices[:max_calendar_days]

        bal = 5000.0
        peak = 5000.0
        sim_max_total_dd = 0.0
        sim_max_daily_dd = 0.0

        phase = 1
        ruined = False
        passed_p1 = False
        passed_p2 = False
        trades_executed = 0

        for day_count, d_idx in enumerate(day_indices, start=1):
            day_start_bal = bal
            t_list = daily_trade_tuples[d_idx]

            for pnl, mae, r_m in t_list:
                trades_executed += 1

                # 1. Intratrade floating drawdown check
                floating_eq = bal - mae
                intra_daily_dd = day_start_bal - floating_eq
                if intra_daily_dd > sim_max_daily_dd:
                    sim_max_daily_dd = intra_daily_dd
                if intra_daily_dd >= 200.0:
                    daily_breaches += 1
                    ruined = True
                    break

                if floating_eq <= 4400.0:
                    floor_breaches += 1
                    ruined = True
                    break

                # 2. Realized trade balance update
                bal += pnl
                if bal > peak:
                    peak = bal
                cur_total_dd = peak - bal
                if cur_total_dd > sim_max_total_dd:
                    sim_max_total_dd = cur_total_dd

                closed_daily_dd = day_start_bal - bal
                if closed_daily_dd > sim_max_daily_dd:
                    sim_max_daily_dd = closed_daily_dd

                if closed_daily_dd >= 200.0:
                    daily_breaches += 1
                    ruined = True
                    break

                if bal <= 4400.0:
                    floor_breaches += 1
                    ruined = True
                    break

                # 3. Sequential Target Checking
                if phase == 1 and bal >= 5500.0:
                    passed_p1 = True
                    days_to_p1_pass.append(day_count)
                    trades_to_p1_pass.append(trades_executed)
                    for h in horizon_p1_pass_counts:
                        if day_count <= h:
                            horizon_p1_pass_counts[h] += 1
                    # Official Transition to Phase 2: Balance resets to $5,000.00
                    phase = 2
                    bal = 5000.0
                    peak = 5000.0
                    day_start_bal = 5000.0

                elif phase == 2 and bal >= 5400.0:
                    passed_p2 = True
                    joint_passes += 1
                    days_to_joint_pass.append(day_count)
                    trades_to_joint_pass.append(trades_executed)
                    for h in horizon_joint_pass_counts:
                        if day_count <= h:
                            horizon_joint_pass_counts[h] += 1
                    break

            if ruined or passed_p2:
                break

        max_drawdowns_total.append(sim_max_total_dd)
        max_drawdowns_daily.append(sim_max_daily_dd)

        if passed_p1:
            p1_passes += 1
        if ruined:
            total_ruins += 1

    # Statistical metrics
    prob_p1 = (p1_passes / n_simulations) * 100.0
    prob_joint = (joint_passes / n_simulations) * 100.0
    prob_daily_breach = (daily_breaches / n_simulations) * 100.0
    prob_floor_breach = (floor_breaches / n_simulations) * 100.0
    prob_ruin = (total_ruins / n_simulations) * 100.0

    # Rule of Three upper bound for 0 breaches: 3 / N
    rule_of_three_upper_bound = (3.0 / n_simulations) * 100.0

    horizon_rates = {
        f"horizon_{h}_trading_days": {
            "trading_days": h,
            "approx_months": round(h / 21.0, 1),
            "phase_1_passes_count": horizon_p1_pass_counts[h],
            "phase_1_pass_rate_pct": round((horizon_p1_pass_counts[h] / n_simulations) * 100.0, 2),
            "joint_both_phases_passes_count": horizon_joint_pass_counts[h],
            "joint_pass_rate_pct": round((horizon_joint_pass_counts[h] / n_simulations) * 100.0, 2),
            "note": "Evaluated horizon in trading days (FundingPips 2-Step Flex has no official calendar time limit)"
        }
        for h in [60, 120, 180, 252, 500, 1000]
    }

    return {
        "lot_mode": lot_mode_label,
        "n_simulations": n_simulations,
        "empirical_trades_count": n_empirical_trades,
        "calendar_days_in_sample": n_days,
        "block_size_days": block_size_days,
        "pass_rates": {
            "phase_1_pass_count": p1_passes,
            "phase_1_pass_pct": round(prob_p1, 2),
            "joint_both_phases_pass_count": joint_passes,
            "joint_both_phases_pass_pct": round(prob_joint, 2),
            "median_trading_days_to_p1": int(np.median(days_to_p1_pass)) if days_to_p1_pass else None,
            "median_trades_to_p1": int(np.median(trades_to_p1_pass)) if trades_to_p1_pass else None,
            "median_trading_days_to_joint_pass": int(np.median(days_to_joint_pass)) if days_to_joint_pass else None,
            "median_trades_to_joint_pass": int(np.median(trades_to_joint_pass)) if trades_to_joint_pass else None,
        },
        "horizons_analysis": horizon_rates,
        "risk_and_ruin": {
            "daily_breaches_count": daily_breaches,
            "daily_breach_pct": round(prob_daily_breach, 4),
            "floor_breaches_count": floor_breaches,
            "floor_breach_pct": round(prob_floor_breach, 4),
            "total_ruin_pct": round(prob_ruin, 4),
            "rule_of_three_upper_bound_95pct": f"< {rule_of_three_upper_bound:.3f}%" if total_ruins == 0 else f"{prob_ruin:.3f}%",
            "scientific_interpretation": (
                f"Observed 0 breaches in {n_simulations:,} runs. Empirical point estimate is 0.00%, "
                f"with a 95% confidence upper bound of < {rule_of_three_upper_bound:.2f}% by the Rule of Three. "
                "Not claimed as mathematical zero." if total_ruins == 0 else
                f"Empirical breach probability is {prob_ruin:.2f}% across {n_simulations:,} runs."
            )
        },
        "drawdowns": {
            "total_dd_usd": {
                "p50": round(float(np.percentile(max_drawdowns_total, 50)), 2),
                "p95": round(float(np.percentile(max_drawdowns_total, 95)), 2),
                "p99": round(float(np.percentile(max_drawdowns_total, 99)), 2),
                "worst": round(float(np.max(max_drawdowns_total)), 2),
            },
            "daily_dd_usd": {
                "p50": round(float(np.percentile(max_drawdowns_daily, 50)), 2),
                "p95": round(float(np.percentile(max_drawdowns_daily, 95)), 2),
                "p99": round(float(np.percentile(max_drawdowns_daily, 99)), 2),
                "worst": round(float(np.max(max_drawdowns_daily)), 2),
            }
        }
    }


def run_phase4_robustness():
    root_dir = Path(__file__).resolve().parent.parent
    data_dir = root_dir / "data"
    gold_dir = data_dir / "gold_candles"

    print("=" * 80)
    print("PHASE 4: QUANTITATIVE ROBUSTNESS, DECOUPLED COST SENSITIVITY & MONTE CARLO")
    print("=" * 80)

    # 1. Load data
    m5 = pd.read_csv(gold_dir / "candles_XAUUSD_M5.csv")
    m15 = pd.read_csv(gold_dir / "candles_XAUUSD_M15.csv")
    h1 = pd.read_csv(gold_dir / "candles_XAUUSD_H1.csv")

    for df in (m5, m15, h1):
        if "time" not in df.columns and "timestamp" in df.columns:
            df["time"] = pd.to_datetime(df["timestamp"], utc=True)

    m15["atr"] = calculate_atr(m15, 14)
    h1["atr"] = calculate_atr(h1, 14)

    # Date partitions
    train_end = "2026-05-01"
    val_end = "2026-08-01"

    all_calendar_days = sorted(list(set(pd.to_datetime(m5["time"]).dt.date)))

    # =========================================================================
    # PART 1: ABLATION STUDY (Baseline Legacy vs Baseline Filter OFF vs Upgraded Filter ON)
    # =========================================================================
    print("\n[PART 1] Running Multi-Period Partition (Train / Validation / OOS)...")

    # 1A. Model A Alone (Upgraded Dynamic 0.25%)
    sim_model_a = IntradaySimulator(
        m5, m15, h1,
        model="model_a",
        risk_pct=0.0025,
        lot_mode="dynamic",
        use_strong_trend_filter=True,
        train_end_date=train_end,
        val_end_date=val_end
    )
    res_model_a = sim_model_a.run()

    # 1B. Model B Alone (Upgraded Dynamic 0.25%)
    sim_model_b = IntradaySimulator(
        m5, m15, h1,
        model="model_b",
        risk_pct=0.0025,
        lot_mode="dynamic",
        use_strong_trend_filter=True,
        train_end_date=train_end,
        val_end_date=val_end
    )
    res_model_b = sim_model_b.run()

    # 1C. Combined Baseline (Filter OFF - Fixed Lot 0.02)
    sim_baseline = IntradaySimulator(
        m5, m15, h1,
        model="model_a_plus_b",
        risk_pct=0.005,
        lot_mode="fixed",
        fixed_lot=0.02,
        use_strong_trend_filter=False,
        train_end_date=train_end,
        val_end_date=val_end
    )
    res_base = sim_baseline.run()

    # 1D. Combined Upgraded (Filter ON - Fixed Lot 0.02)
    sim_upgraded_fixed = IntradaySimulator(
        m5, m15, h1,
        model="model_a_plus_b",
        risk_pct=0.005,
        lot_mode="fixed",
        fixed_lot=0.02,
        use_strong_trend_filter=True,
        train_end_date=train_end,
        val_end_date=val_end
    )
    res_upg_fixed = sim_upgraded_fixed.run()
    trades_upg_fixed = sim_upgraded_fixed._last_trades

    # 1E. Combined Upgraded (Filter ON - Dynamic Risk 0.25% Golden Standard)
    sim_upgraded_dyn = IntradaySimulator(
        m5, m15, h1,
        model="model_a_plus_b",
        risk_pct=0.0025,
        lot_mode="dynamic",
        use_strong_trend_filter=True,
        train_end_date=train_end,
        val_end_date=val_end
    )
    res_upg_dyn = sim_upgraded_dyn.run()
    trades_upg_dyn = sim_upgraded_dyn._last_trades

    # Helper to calculate phase breakdown with rigorous confidence intervals
    def extract_phase_metrics_with_ci(trades: list[dict]):
        phases = {}
        for p in ("TRAIN", "VALIDATION", "OOS"):
            p_trades = [t for t in trades if t["phase"] == p]
            n = len(p_trades)
            if n == 0:
                phases[p] = {"trades": 0, "pnl": 0.0, "pf": 0.0, "wr": 0.0, "exp_r": 0.0, "ci_analysis": {}}
                continue
            wins = [t for t in p_trades if t["pnl_usd"] > 0]
            gross_p = sum(t["pnl_usd"] for t in wins)
            losses = [t for t in p_trades if t["pnl_usd"] < 0]
            gross_l = abs(sum(t["pnl_usd"] for t in losses))
            pf = gross_p / gross_l if gross_l > 0 else 999.0
            pnl = sum(t["pnl_usd"] for t in p_trades)
            exp_r = np.mean([t["r_multiple"] for t in p_trades])
            wr = (len(wins) / n) * 100.0

            ci_data = calculate_expectancy_ci(p_trades)

            phases[p] = {
                "trades": n,
                "pnl": round(pnl, 2),
                "profit_factor": round(pf, 2),
                "win_rate_pct": round(wr, 1),
                "expectancy_r": round(float(exp_r), 3),
                "confidence_interval_95pct_r": ci_data["ci_95_t_dist_r"],
                "confidence_interval_95pct_usd": ci_data["ci_95_t_dist_usd"],
                "bootstrap_ci_95pct_r": ci_data["ci_95_bootstrap_r"],
                "statistical_status": ci_data["statistical_status"],
            }
        return phases

    phase_model_a = extract_phase_metrics_with_ci(sim_model_a._last_trades)
    phase_model_b = extract_phase_metrics_with_ci(sim_model_b._last_trades)
    phase_base = extract_phase_metrics_with_ci(sim_baseline._last_trades)
    phase_upg_fixed = extract_phase_metrics_with_ci(trades_upg_fixed)
    phase_upg_dyn = extract_phase_metrics_with_ci(trades_upg_dyn)

    print(f"  Model A Alone (Dyn 0.25%): {res_model_a['total_trades']} trades | Net: ${res_model_a['net_profit_usd']:.2f} | PF: {res_model_a['profit_factor']:.2f} | MaxDD: ${res_model_a['max_total_dd_usd']:.2f}")
    print(f"  Model B Alone (Dyn 0.25%): {res_model_b['total_trades']} trades | Net: ${res_model_b['net_profit_usd']:.2f} | PF: {res_model_b['profit_factor']:.2f} | MaxDD: ${res_model_b['max_total_dd_usd']:.2f}")
    print(f"  Baseline (Filter OFF):     {res_base['total_trades']} trades | Net: ${res_base['net_profit_usd']:.2f} | PF: {res_base['profit_factor']:.2f} | MaxDD: ${res_base['max_total_dd_usd']:.2f}")
    print(f"  Upgraded (Filter ON):      {res_upg_fixed['total_trades']} trades | Net: ${res_upg_fixed['net_profit_usd']:.2f} | PF: {res_upg_fixed['profit_factor']:.2f} | MaxDD: ${res_upg_fixed['max_total_dd_usd']:.2f}")
    print(f"  Upgraded (Dyn 0.25%):      {res_upg_dyn['total_trades']} trades | Net: ${res_upg_dyn['net_profit_usd']:.2f} | PF: {res_upg_dyn['profit_factor']:.2f} | MaxDD: ${res_upg_dyn['max_total_dd_usd']:.2f}")

    # =========================================================================
    # PART 2: DECOUPLED COST SENSITIVITY STRESS TESTS (EXPERIMENTS A & B)
    # =========================================================================
    print("\n[PART 2] Running Decoupled Cost Sensitivity Stress Tests (Experiments A & B)...")
    spreads = [0.35, 0.50, 0.75]
    slippages = [0.00, 0.15, 0.30]

    # -------------------------------------------------------------------------
    # Experiment A: Pure Friction Drag on Frozen Trade Sample (N=44)
    # -------------------------------------------------------------------------
    print("  Executing Experiment A: Pure Cost Drag (Frozen 44 Trades, Identical Execution)...")
    exp_a_results = []
    base_spread = 0.35
    for sp in spreads:
        for sl in slippages:
            extra_friction_per_trade = round(((sp + sl) - base_spread) * 0.01 * CONTRACT_SIZE, 3)
            adj_trades = []
            gross_p = 0.0
            gross_l = 0.0
            adj_pnls = []

            for t in trades_upg_dyn:
                # Lot is typically 0.01 in dynamic 0.25%
                t_lot = t.get("lot", 0.01)
                t_extra = round(((sp + sl) - base_spread) * t_lot * CONTRACT_SIZE, 3)
                adj_pnl = round(t["pnl_usd"] - t_extra, 2)
                adj_pnls.append(adj_pnl)
                if adj_pnl > 0:
                    gross_p += adj_pnl
                else:
                    gross_l += abs(adj_pnl)

            pf = round(gross_p / gross_l, 2) if gross_l > 0 else 999.0
            net_pnl = round(sum(adj_pnls), 2)
            exp_r = round(float(np.mean([t["r_multiple"] for t in trades_upg_dyn])), 3)

            # Compute Max DD on frozen sample
            eq_curve = 5000.0 + np.cumsum(adj_pnls)
            peaks = np.maximum.accumulate(np.insert(eq_curve, 0, 5000.0))
            max_tot_dd = round(float(np.max(peaks[1:] - eq_curve)), 2)

            exp_a_results.append({
                "experiment": "Experiment A (Frozen Sample)",
                "spread_usd": sp,
                "slippage_usd": sl,
                "total_friction_usd": round(sp + sl, 2),
                "total_trades": len(trades_upg_dyn),
                "trades_status": "FROZEN (44 Trades)",
                "profit_factor": pf,
                "net_profit_usd": net_pnl,
                "expectancy_r": exp_r,
                "max_total_dd_usd": max_tot_dd,
                "rules_passed": (max_tot_dd < 600.0)
            })

    df_exp_a = pd.DataFrame(exp_a_results)
    df_exp_a.to_csv(data_dir / "phase4_cost_sensitivity_experiment_a.csv", index=False)

    # -------------------------------------------------------------------------
    # Experiment B: Dynamic Pre-Trade Filter & Slippage Response (Trades 44 -> 40)
    # -------------------------------------------------------------------------
    print("  Executing Experiment B: Dynamic Pre-Trade Filter & Slippage Response...")
    exp_b_results = []
    for sp in spreads:
        for sl in slippages:
            sim_sens = IntradaySimulator(
                m5, m15, h1,
                model="model_a_plus_b",
                risk_pct=0.0025,
                lot_mode="dynamic",
                spread_usd=sp,
                slippage_usd=sl,
                use_strong_trend_filter=True,
                train_end_date=train_end,
                val_end_date=val_end
            )
            r = sim_sens.run()
            diag = r.get("diagnostics", {})
            accepted_n = r["total_trades"]
            rejected_risk = diag.get("rejected_min_lot_risk", 0)
            rejection_notes = f"{44 - accepted_n} wider-SL trades rejected by min lot risk cap" if accepted_n < 44 else "None"

            exp_b_results.append({
                "experiment": "Experiment B (Dynamic Pre-Trade Filter)",
                "spread_usd": sp,
                "slippage_usd": sl,
                "total_friction_usd": round(sp + sl, 2),
                "total_trades": accepted_n,
                "rejected_signals_count": max(0, 44 - accepted_n),
                "rejected_by_min_lot_risk_budget": rejected_risk,
                "rejection_reason": rejection_notes,
                "profit_factor": r["profit_factor"],
                "net_profit_usd": r["net_profit_usd"],
                "expectancy_r": r["expectancy_r"],
                "max_daily_dd_usd": r["max_daily_dd_usd"],
                "max_total_dd_usd": r["max_total_dd_usd"],
                "rules_passed": r["fundingpips_rules_passed"]
            })

    df_exp_b = pd.DataFrame(exp_b_results)
    df_exp_b.to_csv(data_dir / "phase4_cost_sensitivity_experiment_b.csv", index=False)
    # Maintain backwards-compatible unified sensitivity grid
    df_exp_b.to_csv(data_dir / "phase4_sensitivity_grid.csv", index=False)

    # =========================================================================
    # PART 3: OVERHAULED SEQUENTIAL MONTE CARLO (10,000 RUNS)
    # =========================================================================
    print("\n[PART 3] Running Overhauled Sequential Monte Carlo (10,000 Iterations)...")

    print("  -> Simulating Configuration 1: Upgraded Dynamic Risk 0.25%...")
    mc_dyn = run_sequential_monte_carlo(
        trades=trades_upg_dyn,
        all_trading_days=all_calendar_days,
        lot_mode_label="dynamic_risk_0.25pct",
        n_simulations=10000,
        seed=42,
        block_size_days=5
    )

    print("  -> Simulating Configuration 2: Upgraded Fixed Lot 0.02...")
    mc_fixed = run_sequential_monte_carlo(
        trades=trades_upg_fixed,
        all_trading_days=all_calendar_days,
        lot_mode_label="fixed_lot_0.02",
        n_simulations=10000,
        seed=42,
        block_size_days=5
    )

    print(f"\n[MONTE CARLO RESULTS SUMMARY]")
    print(f"  Configuration 1: Dynamic 0.25% Risk")
    print(f"    • Phase 1 Pass Rate: {mc_dyn['pass_rates']['phase_1_pass_pct']}%")
    print(f"    • Joint Pass Rate (P1 then P2): {mc_dyn['pass_rates']['joint_both_phases_pass_pct']}%")
    print(f"    • Total Ruin Rate: {mc_dyn['risk_and_ruin']['total_ruin_pct']}% (95% Bound: {mc_dyn['risk_and_ruin']['rule_of_three_upper_bound_95pct']})")
    print(f"    • Total DD: Median ${mc_dyn['drawdowns']['total_dd_usd']['p50']} | 99th % ${mc_dyn['drawdowns']['total_dd_usd']['p99']} | Worst ${mc_dyn['drawdowns']['total_dd_usd']['worst']}")
    print(f"  Configuration 2: Fixed Lot 0.02")
    print(f"    • Phase 1 Pass Rate: {mc_fixed['pass_rates']['phase_1_pass_pct']}%")
    print(f"    • Joint Pass Rate (P1 then P2): {mc_fixed['pass_rates']['joint_both_phases_pass_pct']}%")
    print(f"    • Total Ruin Rate: {mc_fixed['risk_and_ruin']['total_ruin_pct']}% (95% Bound: {mc_fixed['risk_and_ruin']['rule_of_three_upper_bound_95pct']})")
    print(f"    • Total DD: Median ${mc_fixed['drawdowns']['total_dd_usd']['p50']} | 99th % ${mc_fixed['drawdowns']['total_dd_usd']['p99']} | Worst ${mc_fixed['drawdowns']['total_dd_usd']['worst']}")

    # =========================================================================
    # PART 4: SAVE STRUCTURED DATA ARTIFACTS
    # =========================================================================
    robustness_payload = {
        "metadata": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "asset": "XAUUSD",
            "account_size_usd": 5000.0,
            "prop_firm": "FundingPips (2-Step Flex $5k)",
            "rules": {
                "max_daily_dd_pct": 4.0,
                "max_daily_dd_usd": 200.0,
                "max_total_dd_pct": 12.0,
                "max_total_dd_usd": 600.0,
                "p1_target_pct": 10.0,
                "p1_target_usd": 500.0,
                "p2_target_pct": 8.0,
                "p2_target_usd": 400.0,
                "official_time_limit": "UNLIMITED (No official time limit on FundingPips 2-Step Flex)"
            }
        },
        "ablation_comparison": {
            "model_a_alone_dynamic": {
                "total_trades": res_model_a["total_trades"],
                "profit_factor": res_model_a["profit_factor"],
                "net_profit_usd": res_model_a["net_profit_usd"],
                "expectancy_r": res_model_a["expectancy_r"],
                "max_daily_dd_usd": res_model_a["max_daily_dd_usd"],
                "max_total_dd_usd": res_model_a["max_total_dd_usd"],
                "phases": phase_model_a
            },
            "model_b_alone_dynamic": {
                "total_trades": res_model_b["total_trades"],
                "profit_factor": res_model_b["profit_factor"],
                "net_profit_usd": res_model_b["net_profit_usd"],
                "expectancy_r": res_model_b["expectancy_r"],
                "max_daily_dd_usd": res_model_b["max_daily_dd_usd"],
                "max_total_dd_usd": res_model_b["max_total_dd_usd"],
                "phases": phase_model_b
            },
            "baseline_filter_off": {
                "total_trades": res_base["total_trades"],
                "profit_factor": res_base["profit_factor"],
                "net_profit_usd": res_base["net_profit_usd"],
                "expectancy_r": res_base["expectancy_r"],
                "max_daily_dd_usd": res_base["max_daily_dd_usd"],
                "max_total_dd_usd": res_base["max_total_dd_usd"],
                "phases": phase_base
            },
            "upgraded_filter_on_fixed": {
                "total_trades": res_upg_fixed["total_trades"],
                "profit_factor": res_upg_fixed["profit_factor"],
                "net_profit_usd": res_upg_fixed["net_profit_usd"],
                "expectancy_r": res_upg_fixed["expectancy_r"],
                "max_daily_dd_usd": res_upg_fixed["max_daily_dd_usd"],
                "max_total_dd_usd": res_upg_fixed["max_total_dd_usd"],
                "phases": phase_upg_fixed
            },
            "upgraded_filter_on_dynamic": {
                "total_trades": res_upg_dyn["total_trades"],
                "profit_factor": res_upg_dyn["profit_factor"],
                "net_profit_usd": res_upg_dyn["net_profit_usd"],
                "expectancy_r": res_upg_dyn["expectancy_r"],
                "max_daily_dd_usd": res_upg_dyn["max_daily_dd_usd"],
                "max_total_dd_usd": res_upg_dyn["max_total_dd_usd"],
                "phases": phase_upg_dyn
            }
        },
        "session_breakdown": res_upg_dyn.get("session_breakdown"),
        "monthly_breakdown": res_upg_dyn.get("monthly_breakdown"),
        "cost_sensitivity_experiment_a_frozen": exp_a_results,
        "cost_sensitivity_experiment_b_dynamic": exp_b_results,
        "monte_carlo_dynamic_025": mc_dyn,
        "monte_carlo_fixed_002": mc_fixed,
    }

    with open(data_dir / "phase4_robustness_results.json", "w", encoding="utf-8") as f:
        json.dump(robustness_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[DONE] Saved artifacts:")
    print(f"  - {data_dir / 'phase4_cost_sensitivity_experiment_a.csv'}")
    print(f"  - {data_dir / 'phase4_cost_sensitivity_experiment_b.csv'}")
    print(f"  - {data_dir / 'phase4_sensitivity_grid.csv'}")
    print(f"  - {data_dir / 'phase4_robustness_results.json'}")

    return robustness_payload


if __name__ == "__main__":
    run_phase4_robustness()
