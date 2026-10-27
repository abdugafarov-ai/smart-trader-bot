"""
Tier-1 Quantitative Prop Desk Monte Carlo Stress Test & Risk of Ruin Engine
Institutional Model for FundingPips $5,000 2-Step Flex Challenge:
- Initial Capital: $5,000.00
- Risk per Trade: exactly 0.50% ($25.00 fixed initial risk)
- Daily Loss Limit: $200.00 (4.00%)
- Max Drawdown Limit: $600.00 (12.00% / Equity floor $4,400.00)
- Horizon: 100 trades
- Iterations: 10,000 iterations per matrix cell
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Any


def run_monte_carlo(
    n_simulations: int = 10000,
    n_trades: int = 100,
    initial_balance: float = 5000.0,
    risk_pct: float = 0.5,             # 0.5% per trade
    daily_loss_limit_usd: float = 200.0, # 4.0%
    max_loss_limit_usd: float = 600.0,   # 12.0% ($4,400 static floor)
    seed: int = 42
):
    np.random.seed(seed)
    risk_usd_fixed = initial_balance * (risk_pct / 100.0)  # $25.00

    win_rates = [0.40, 0.425, 0.45, 0.475, 0.50]
    rr_ratios = [1.5, 1.75, 2.0]

    results = []

    print("=" * 115)
    print("🚀 TIER-1 QUANTITATIVE PROP DESK MONTE CARLO STRESS TEST (10,000 ITERATIONS PER CELL)")
    print(f"Capital: ${initial_balance:,.2f} | Risk/Trade: {risk_pct:.2f}% (${risk_usd_fixed:.2f}) | Horizon: {n_trades} Trades")
    print(f"Daily Limit: ${daily_loss_limit_usd:,.2f} (4.0%) | Max Total Loss: ${max_loss_limit_usd:,.2f} (12.0% floor ${initial_balance - max_loss_limit_usd:,.2f})")
    print("=" * 115)

    for wr in win_rates:
        for rr in rr_ratios:
            # 1. Exact Mathematical Expectancy
            # E(R) = (WR * RR) - ((1 - WR) * 1.0)
            exp_r = (wr * rr) - ((1.0 - wr) * 1.0)
            exp_usd_per_trade = exp_r * risk_usd_fixed
            exp_usd_100_trades = exp_usd_per_trade * n_trades
            profit_factor = (wr * rr) / (1.0 - wr) if wr < 1.0 else 999.0

            # 2. Monte Carlo 10,000 Iterations
            breached_max_dd_count = 0
            breached_daily_unconstrained_count = 0
            breached_daily_guarded_count = 0
            max_drawdowns_usd = []
            final_balances = []
            max_consecutive_losses = []

            for sim in range(n_simulations):
                # Binomial trade outcomes: 1 = Win, 0 = Loss
                outcomes = np.random.binomial(1, wr, n_trades)
                pnl_trades = np.where(outcomes == 1, risk_usd_fixed * rr, -risk_usd_fixed)

                # Equity Curve
                equity_curve = initial_balance + np.cumsum(pnl_trades)
                peak = np.maximum.accumulate(np.insert(equity_curve, 0, initial_balance))
                drawdowns = peak[1:] - equity_curve
                max_dd = np.max(drawdowns)
                max_drawdowns_usd.append(max_dd)
                final_balances.append(equity_curve[-1])

                # Max Total Loss Breach ($600 / equity <= $4,400)
                if np.min(equity_curve) <= (initial_balance - max_loss_limit_usd):
                    breached_max_dd_count += 1

                # Daily Breach - Model A: Unconstrained intraday flow (up to 8-10 trades/day)
                # Test whether any cluster of daily trades within 100 trades loses >= $200 (>= 8 consecutive losses)
                day_start = 0
                hit_daily_unconstrained = False
                while day_start < n_trades:
                    # Intraday cluster: 2 to 10 trades per day
                    d_count = np.random.randint(2, 11)
                    d_end = min(day_start + d_count, n_trades)
                    if np.sum(pnl_trades[day_start:d_end]) <= -daily_loss_limit_usd:
                        hit_daily_unconstrained = True
                    day_start = d_end

                if hit_daily_unconstrained:
                    breached_daily_unconstrained_count += 1

                # Daily Breach - Model B: Guarded Prop Desk (Bot's 3-SL lock & 4 max trades/day)
                # With 3-SL lock, max daily loss is 3 * $25 = $75.00 (< $200.00)
                day_start_g = 0
                hit_daily_guarded = False
                while day_start_g < n_trades:
                    d_count_g = min(4, n_trades - day_start_g)
                    day_trades = pnl_trades[day_start_g:day_start_g + d_count_g]
                    # Apply 3 consecutive SL guard
                    consec = 0
                    day_pnl_g = 0.0
                    for tr in day_trades:
                        if tr < 0:
                            consec += 1
                        else:
                            consec = 0
                        day_pnl_g += tr
                        if consec >= 3:
                            break  # 12h cooldown lock triggers
                    if day_pnl_g <= -daily_loss_limit_usd:
                        hit_daily_guarded = True
                    day_start_g += d_count_g

                if hit_daily_guarded:
                    breached_daily_guarded_count += 1

                # Consecutive Loss Runs
                cur_streak = 0
                max_streak = 0
                for o in outcomes:
                    if o == 0:
                        cur_streak += 1
                        if cur_streak > max_streak:
                            max_streak = cur_streak
                    else:
                        cur_streak = 0
                max_consecutive_losses.append(max_streak)

            prob_ruin_max_dd = (breached_max_dd_count / n_simulations) * 100.0
            prob_ruin_daily_uncon = (breached_daily_unconstrained_count / n_simulations) * 100.0
            prob_ruin_daily_guarded = (breached_daily_guarded_count / n_simulations) * 100.0

            p95_max_dd = np.percentile(max_drawdowns_usd, 95)
            p99_max_dd = np.percentile(max_drawdowns_usd, 99)
            median_max_dd = np.median(max_drawdowns_usd)
            median_final_bal = np.median(final_balances)
            p95_loss_streak = np.percentile(max_consecutive_losses, 95)

            results.append({
                "win_rate": f"{wr*100:.1f}%",
                "rr_ratio": f"1:{rr:.2f}",
                "expectancy_r": exp_r,
                "exp_100_usd": exp_usd_100_trades,
                "profit_factor": profit_factor,
                "prob_max_dd_breach": prob_ruin_max_dd,
                "prob_daily_unconstrained": prob_ruin_daily_uncon,
                "prob_daily_guarded": prob_ruin_daily_guarded,
                "median_max_dd_usd": median_max_dd,
                "p95_max_dd_usd": p95_max_dd,
                "p99_max_dd_usd": p99_max_dd,
                "p95_loss_streak": int(p95_loss_streak),
                "median_final_bal_usd": median_final_bal,
            })

    df_res = pd.DataFrame(results)

    print("\n" + "=" * 125)
    print("📊 1. PRIMARY MONTE CARLO RISK & EXPECTANCY MATRIX (10,000 SIMULATIONS × 100 TRADES)")
    print("=" * 125)
    header = (
        f"{'WinRate':<8} | {'R:R':<6} | {'Exp(R)':<7} | {'Exp/100':<10} | {'PF':<5} | "
        f"{'Max Loss Ruin':<14} | {'Daily Breach (Unc)':<18} | {'Daily Breach (Guard)':<20} | {'P95 Max DD':<11} | {'P95 Streak'}"
    )
    print(header)
    print("-" * 125)
    for _, r in df_res.iterrows():
        print(
            f"{r['win_rate']:<8} | {r['rr_ratio']:<6} | {r['expectancy_r']:+.3f}R | "
            f"{r['exp_100_usd']:+9.2f}$ | {r['profit_factor']:4.2f} | "
            f"{r['prob_max_dd_breach']:6.2f}%       | {r['prob_daily_unconstrained']:6.2f}%            | "
            f"{r['prob_daily_guarded']:6.2f}% (LOCKED)   | ${r['p95_max_dd_usd']:<9.2f} | {r['p95_loss_streak']} losses"
        )
    print("=" * 125)

    # 3. Partial Close Runner Simulation (Tier-1 Institutional Model)
    # Model:
    # 50% closed at Opposing Liquidity Pool (TP1 @ 1.0R -> +0.50R).
    # When TP1 is hit, SL is moved behind Protected Swing Low/High (Auto-BE).
    # In 65% of winning trades, the 50% runner reaches Final TP (2.0R -> +1.00R, total trade +1.50R).
    # In 35% of winning trades, runner reverses and gets stopped at BE + buffer (+0.08R, total trade +0.54R).
    # Expected payout per winning trade: 0.65 * 1.50R + 0.35 * 0.54R = 0.975 + 0.189 = 1.164R.
    # Losing trade hits SL before 1.0R -> -1.00R (-$25.00).
    print("\n" + "=" * 125)
    print("🎯 2. TIER-1 INSTITUTIONAL MODEL: 50% PARTIAL CLOSE @ OPPOSING LIQUIDITY + PROTECTED RUNNER")
    print("=" * 125)
    print(f"{'WinRate':<8} | {'Model':<22} | {'Exp(R)':<7} | {'Exp/100':<10} | {'Max Loss Ruin':<14} | {'Daily Breach (Guard)':<20} | {'P95 Max DD'}")
    print("-" * 125)
    for wr in [0.40, 0.425, 0.45, 0.475, 0.50]:
        breached_max = 0
        p95_dds = []
        for _ in range(n_simulations):
            outcomes = np.random.binomial(1, wr, n_trades)
            runner_wins = np.random.binomial(1, 0.65, n_trades)
            win_payouts = np.where(runner_wins == 1, 1.50, 0.54)
            pnl_r = np.where(outcomes == 1, win_payouts, -1.0)
            pnl_usd = pnl_r * risk_usd_fixed

            eq = initial_balance + np.cumsum(pnl_usd)
            peak = np.maximum.accumulate(np.insert(eq, 0, initial_balance))
            dd = peak[1:] - eq
            p95_dds.append(np.max(dd))

            if np.min(eq) <= (initial_balance - max_loss_limit_usd):
                breached_max += 1

        exp_r_part = (wr * 1.164) - ((1.0 - wr) * 1.0)
        exp_usd_100 = exp_r_part * risk_usd_fixed * n_trades
        ruin_pct = (breached_max / n_simulations) * 100.0
        p95_dd_val = np.percentile(p95_dds, 95)
        print(
            f"{wr*100:.1f}%    | 50% TP1 + Runner BE    | {exp_r_part:+.3f}R | "
            f"{exp_usd_100:+9.2f}$ | {ruin_pct:6.2f}%       | 0.00% (LOCKED)       | ${p95_dd_val:<9.2f}"
        )
    print("=" * 125)

    return df_res


if __name__ == "__main__":
    run_monte_carlo()
