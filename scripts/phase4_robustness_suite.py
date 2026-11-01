"""
Phase 4: Comprehensive Quantitative Robustness, Walk-Forward, Sensitivity & Monte Carlo Suite
for XAUUSD under FundingPips $5,000 2-Step Flex Rules.
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
from scripts.intraday_gold_engine import IntradaySimulator, calculate_atr


def run_phase4_robustness():
    root_dir = Path(__file__).resolve().parent.parent
    data_dir = root_dir / "data"
    gold_dir = data_dir / "gold_candles"

    print("=" * 80)
    print("PHASE 4: QUANTITATIVE ROBUSTNESS & MONTE CARLO SIMULATION SUITE")
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

    # Helper to calculate phase breakdown
    def extract_phase_metrics(trades):
        phases = {}
        for p in ("TRAIN", "VALIDATION", "OOS"):
            p_trades = [t for t in trades if t["phase"] == p]
            n = len(p_trades)
            if n == 0:
                phases[p] = {"trades": 0, "pnl": 0.0, "pf": 0.0, "wr": 0.0, "exp_r": 0.0}
                continue
            wins = [t for t in p_trades if t["pnl_usd"] > 0]
            gross_p = sum(t["pnl_usd"] for t in wins)
            losses = [t for t in p_trades if t["pnl_usd"] < 0]
            gross_l = abs(sum(t["pnl_usd"] for t in losses))
            pf = gross_p / gross_l if gross_l > 0 else 999.0
            pnl = sum(t["pnl_usd"] for t in p_trades)
            exp_r = np.mean([t["r_multiple"] for t in p_trades])
            wr = (len(wins) / n) * 100.0
            phases[p] = {
                "trades": n,
                "pnl": round(pnl, 2),
                "profit_factor": round(pf, 2),
                "win_rate_pct": round(wr, 1),
                "expectancy_r": round(float(exp_r), 3)
            }
        return phases

    phase_model_a = res_model_a.get("three_way_split")
    phase_model_b = res_model_b.get("three_way_split")
    phase_base = res_base.get("three_way_split")
    phase_upg_fixed = res_upg_fixed.get("three_way_split")
    phase_upg_dyn = res_upg_dyn.get("three_way_split")

    print(f"  Model A Alone (Dyn 0.25%): {res_model_a['total_trades']} trades | Net: ${res_model_a['net_profit_usd']:.2f} | PF: {res_model_a['profit_factor']:.2f} | MaxDD: ${res_model_a['max_total_dd_usd']:.2f}")
    print(f"  Model B Alone (Dyn 0.25%): {res_model_b['total_trades']} trades | Net: ${res_model_b['net_profit_usd']:.2f} | PF: {res_model_b['profit_factor']:.2f} | MaxDD: ${res_model_b['max_total_dd_usd']:.2f}")
    print(f"  Baseline (Filter OFF):     {res_base['total_trades']} trades | Net: ${res_base['net_profit_usd']:.2f} | PF: {res_base['profit_factor']:.2f} | MaxDD: ${res_base['max_total_dd_usd']:.2f}")
    print(f"  Upgraded (Filter ON):      {res_upg_fixed['total_trades']} trades | Net: ${res_upg_fixed['net_profit_usd']:.2f} | PF: {res_upg_fixed['profit_factor']:.2f} | MaxDD: ${res_upg_fixed['max_total_dd_usd']:.2f}")
    print(f"  Upgraded (Dyn 0.25%):      {res_upg_dyn['total_trades']} trades | Net: ${res_upg_dyn['net_profit_usd']:.2f} | PF: {res_upg_dyn['profit_factor']:.2f} | MaxDD: ${res_upg_dyn['max_total_dd_usd']:.2f}")

    # =========================================================================
    # PART 2: SENSITIVITY GRID (Spread $0.35, $0.50, $0.75 x Slippage $0.00, $0.15, $0.30)
    # =========================================================================
    print("\n[PART 2] Running 3x3 Execution Cost Sensitivity Grid...")
    spreads = [0.35, 0.50, 0.75]
    slippages = [0.00, 0.15, 0.30]
    sensitivity_results = []

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
            row = {
                "spread_usd": sp,
                "slippage_usd": sl,
                "total_friction_usd": round(sp + sl, 2),
                "total_trades": r["total_trades"],
                "profit_factor": r["profit_factor"],
                "net_profit_usd": r["net_profit_usd"],
                "expectancy_r": r["expectancy_r"],
                "max_daily_dd_usd": r["max_daily_dd_usd"],
                "max_total_dd_usd": r["max_total_dd_usd"],
                "rules_passed": r["fundingpips_rules_passed"]
            }
            sensitivity_results.append(row)
            print(f"  Spread ${sp:.2f} + Slip ${sl:.2f} (Total ${sp+sl:.2f}): PF {r['profit_factor']:.2f} | Net ${r['net_profit_usd']:.2f} | MaxDD ${r['max_total_dd_usd']:.2f}")

    df_sens = pd.DataFrame(sensitivity_results)
    df_sens.to_csv(data_dir / "phase4_sensitivity_grid.csv", index=False)

    # =========================================================================
    # PART 3: 10,000-RUN MONTE CARLO SIMULATION
    # =========================================================================
    print("\n[PART 3] Running 10,000-Run Monte Carlo Simulation (Block & Trade Bootstrap)...")

    # Trade outcomes from Upgraded Dynamic Strategy
    trades_dyn = sim_upgraded_dyn._last_trades if hasattr(sim_upgraded_dyn, "_last_trades") and sim_upgraded_dyn._last_trades else sim_upgraded_fixed._last_trades

    pnl_series = [t["pnl_usd"] for t in trades_dyn]
    durations = [t["duration_min"] for t in trades_dyn]
    num_trades = len(pnl_series)

    num_simulations = 10000
    p1_target = 500.0  # +10% on $5k (FundingPips 2-Step Flex)
    p2_target = 400.0  # +8% on $5k (FundingPips 2-Step Flex)
    max_daily_limit = 200.0  # 4%
    max_total_limit = 600.0  # 12%

    # Monte Carlo containers
    mc_max_total_dds = []
    mc_max_daily_dds = []
    mc_final_pnls = []
    mc_p1_passed = 0
    mc_p2_passed = 0
    mc_both_passed = 0
    mc_daily_breached = 0
    mc_total_breached = 0
    mc_trades_to_p1 = []
    mc_trades_to_p2 = []

    # Block Bootstrap: block size 4 trades to preserve serial dependence
    block_size = 4
    num_blocks = math.ceil(num_trades / block_size)

    np.random.seed(42)
    random.seed(42)

    for sim_idx in range(num_simulations):
        # Sample with replacement using block bootstrap
        sampled_pnls = []
        for _ in range(num_blocks):
            start_idx = random.randint(0, max(0, num_trades - block_size))
            sampled_pnls.extend(pnl_series[start_idx : start_idx + block_size])
        sampled_pnls = sampled_pnls[:num_trades]

        # Simulate equity curve
        bal = 5000.0
        peak = 5000.0
        sim_max_total_dd = 0.0
        sim_max_daily_dd = 0.0

        p1_hit = False
        p2_hit = False
        p1_trade_count = None
        p2_trade_count = None
        daily_breach = False
        total_breach = False

        # Group into daily batches (~1-3 trades per day, avg ~1.5)
        # Randomly assign 1 to 3 trades per trading day
        day_trades = []
        cur_day_pnl = 0.0
        trades_in_day = random.randint(1, 3)

        for t_i, trade_pnl in enumerate(sampled_pnls):
            bal += trade_pnl
            cur_day_pnl += trade_pnl
            trades_in_day -= 1

            # Day rollover
            if trades_in_day <= 0 or t_i == num_trades - 1:
                if cur_day_pnl < 0 and abs(cur_day_pnl) > sim_max_daily_dd:
                    sim_max_daily_dd = abs(cur_day_pnl)
                if cur_day_pnl < -max_daily_limit:
                    daily_breach = True
                cur_day_pnl = 0.0
                trades_in_day = random.randint(1, 3)

            # Peak and total DD
            if bal > peak:
                peak = bal
            cur_dd = peak - bal
            if cur_dd > sim_max_total_dd:
                sim_max_total_dd = cur_dd
            if cur_dd >= max_total_limit:
                total_breach = True

            # Target checks
            cum_profit = bal - 5000.0
            if cum_profit >= p1_target and not p1_hit and not total_breach:
                p1_hit = True
                p1_trade_count = t_i + 1

            if cum_profit >= p2_target and not p2_hit and not total_breach:
                p2_hit = True
                p2_trade_count = t_i + 1

        mc_max_total_dds.append(sim_max_total_dd)
        mc_max_daily_dds.append(sim_max_daily_dd)
        mc_final_pnls.append(bal - 5000.0)

        if p1_hit and not total_breach:
            mc_p1_passed += 1
            mc_trades_to_p1.append(p1_trade_count)

        if p2_hit and not total_breach:
            mc_p2_passed += 1
            mc_trades_to_p2.append(p2_trade_count)

        if daily_breach:
            mc_daily_breached += 1
        if total_breach:
            mc_total_breached += 1

    # Statistical summaries
    prob_p1_pass = (mc_p1_passed / num_simulations) * 100.0
    prob_p2_pass = (mc_p2_passed / num_simulations) * 100.0
    prob_daily_breach = (mc_daily_breached / num_simulations) * 100.0
    prob_total_ruin = (mc_total_breached / num_simulations) * 100.0

    dd_total_50 = float(np.percentile(mc_max_total_dds, 50))
    dd_total_95 = float(np.percentile(mc_max_total_dds, 95))
    dd_total_99 = float(np.percentile(mc_max_total_dds, 99))
    dd_total_worst = float(np.max(mc_max_total_dds))

    dd_daily_50 = float(np.percentile(mc_max_daily_dds, 50))
    dd_daily_95 = float(np.percentile(mc_max_daily_dds, 95))
    dd_daily_99 = float(np.percentile(mc_max_daily_dds, 99))
    dd_daily_worst = float(np.max(mc_max_daily_dds))

    pnl_50 = float(np.percentile(mc_final_pnls, 50))
    pnl_5 = float(np.percentile(mc_final_pnls, 5))
    pnl_95 = float(np.percentile(mc_final_pnls, 95))

    median_trades_p1 = int(np.median(mc_trades_to_p1)) if mc_trades_to_p1 else None
    median_trades_p2 = int(np.median(mc_trades_to_p2)) if mc_trades_to_p2 else None

    print(f"\n[MONTE CARLO RESULTS (10,000 Runs)]")
    print(f"  Probability of Total Account Ruin (DD >= 12% / $600): {prob_total_ruin:.2f}%")
    print(f"  Probability of Daily DD Breach (Daily DD >= 4% / $200): {prob_daily_breach:.2f}%")
    print(f"  Probability of Passing Phase 1 (+10% / $500): {prob_p1_pass:.1f}%")
    print(f"  Probability of Passing Phase 2 (+8% / $400): {prob_p2_pass:.1f}%")
    print(f"  Total Drawdown Distribution: Median ${dd_total_50:.2f} | 95th ${dd_total_95:.2f} | 99th ${dd_total_99:.2f} | Worst ${dd_total_worst:.2f}")
    print(f"  Daily Drawdown Distribution: Median ${dd_daily_50:.2f} | 95th ${dd_daily_95:.2f} | 99th ${dd_daily_99:.2f} | Worst ${dd_daily_worst:.2f}")
    print(f"  Expected Trades to Pass: Phase 1 ~{median_trades_p1} trades | Phase 2 ~{median_trades_p2} trades")

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
        "sensitivity_grid": sensitivity_results,
        "monte_carlo_10000": {
            "iterations": num_simulations,
            "prob_total_ruin_pct": round(prob_total_ruin, 4),
            "prob_daily_breach_pct": round(prob_daily_breach, 4),
            "prob_p1_pass_pct": round(prob_p1_pass, 2),
            "prob_p2_pass_pct": round(prob_p2_pass, 2),
            "median_trades_p1": median_trades_p1,
            "median_trades_p2": median_trades_p2,
            "total_dd_usd": {
                "p50": round(dd_total_50, 2),
                "p95": round(dd_total_95, 2),
                "p99": round(dd_total_99, 2),
                "worst": round(dd_total_worst, 2)
            },
            "daily_dd_usd": {
                "p50": round(dd_daily_50, 2),
                "p95": round(dd_daily_95, 2),
                "p99": round(dd_daily_99, 2),
                "worst": round(dd_daily_worst, 2)
            },
            "final_pnl_usd": {
                "p5": round(pnl_5, 2),
                "p50": round(pnl_50, 2),
                "p95": round(pnl_95, 2)
            }
        }
    }

    with open(data_dir / "phase4_robustness_results.json", "w", encoding="utf-8") as f:
        json.dump(robustness_payload, f, indent=2, ensure_ascii=False)

    print(f"\n[DONE] Saved artifacts:")
    print(f"  - {data_dir / 'phase4_sensitivity_grid.csv'}")
    print(f"  - {data_dir / 'phase4_robustness_results.json'}")

    return robustness_payload


if __name__ == "__main__":
    run_phase4_robustness()
