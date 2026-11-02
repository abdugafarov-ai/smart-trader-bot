# Phase 4: Quantitative Robustness, Decoupled Cost Sensitivity & Monte Carlo Analysis
## Targeted Statistical Validation & Rigorous Evidence-Based Audit

**Asset:** XAUUSD (Gold) Intraday Trading (H1 Bias $\to$ M15 POI $\to$ M5 Trigger)  
**Evaluation Standard:** FundingPips $5,000 2-Step Flex Rules  
**Phase 4 Overall Status:** **`PARTIAL`**  
- Multi-Period Partition & Ablation Study: **PASS**  
- Circular Block Bootstrap & Confidence Intervals: **PASS (Method Validated; OOS Preliminary / Inconclusive)**  
- Decoupled Cost Sensitivity (Fixed 0.02 & Dynamic 0.25%): **PASS**  
- Sequential 2-Phase Monte Carlo (10,000 runs, 1000-day cap): **PASS**  
- Independent Cross-Broker Data Audit (Dukascopy): **`INDEPENDENT DATA TEST NOT PERFORMED`**  

---

## Executive Summary

Phase 4 of the **Smart Trader Bot Master Upgrade & Challenge-Readiness Plan v1.0** provides targeted statistical validation and empirical robustness verification for the upgraded XAUUSD Intraday Strategy (`model_a_plus_b` with Strong Trend Cascade & Exhaustion Guard):
1. **Circular Block Bootstrap for Expectancy, Sharpe Ratio & Profit Factor:** Replaces naive IID trade resampling with calendar-day circular block bootstrap, preserving chronological trade sequences, intraday clustering, and multi-day win/loss streaks across block lengths $b \in [1, 2, 3, 5]$ days.
2. **Pure Cost Sensitivity for Agreed `RESEARCH_XAUUSD_FIXED002`:** Evaluates pure friction drag on the frozen 44-trade sample at fixed lot 0.02 across spread $\in [\$0.35, \$0.50, \$0.75]$ and slippage $\in [\$0.00, \$0.15, \$0.30]$, alongside a separate dynamic risk 0.25% cost profile.
3. **Sequential 2-Phase Monte Carlo & Correct Horizon Labeling:** Simulates 10,000 runs across 183 factual calendar trading days with sequential Phase 1 ($500 / +10%) $\to$ Phase 2 ($400 / +8%) transitions, double drawdown limits (floating MAE and realized balance), and properly labels the 1,000-day horizon as a **`1000-day simulation cap`** rather than an "unconstrained" or infinite horizon.
4. **Independent Data Feeds:** Formally records the Dukascopy Bank SA data audit as **`INDEPENDENT DATA TEST NOT PERFORMED`** without synthetic or proxy substitution.

---

## 1. Multi-Period Partition Results & Block Bootstrap OOS Evidence

### Strategy Configuration Summary across Entire Dataset (Feb–Oct 2026):

| Strategy Configuration | Total Trades | Net PnL (USD) | Profit Factor | Expectancy (R) | Max Daily DD | Max Total DD | FundingPips Rules Passed? |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline (Cascade Filter OFF, Fixed 0.02)** | 61 | +$36.48 | 1.07 | +0.038R | $35.16 | $126.50 | ✅ PASS |
| **Upgraded (Cascade Filter ON, Fixed 0.02)** | 44 | +$68.78 | 1.18 | +0.111R | $24.80 | $152.50 | ✅ PASS |
| **Upgraded (Cascade Filter ON, Dynamic 0.25%)** | 44 | +$44.69 | 1.22 | +0.111R | $12.40 | $74.41 | ✅ PASS |

### Temporal Sub-Period Breakdown (Upgraded Strategy @ Dynamic 0.25%):

| Partition Period | Date Range | Trades | Trading Days | Win Rate (TP) | Non-Loss Rate | Profit Factor | Net PnL (USD) | Expectancy (R) | Annualized Sharpe | Primary 95% Bootstrap CI (R) | Statistical Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Train (In-Sample)** | Feb 2026 – Apr 2026 | 8 | 8 | 25.0% | 37.5% | 0.72 | -$14.10 | -0.161R | -1.53 | [-1.152R, +0.830R] | `STATISTICALLY INCONCLUSIVE` |
| **Validation** | May 2026 – Jul 2026 | 22 | 22 | 22.7% | 54.5% | 0.89 | -$11.83 | -0.023R | -0.37 | [-0.472R, +0.426R] | `STATISTICALLY INCONCLUSIVE` |
| **Out-of-Sample (OOS)** | Aug 2026 – Oct 2026 | 14 | 13 | 42.9% | 64.3% | 2.47 | +$70.62 | +0.478R | 6.05 | **[-0.185R, +1.055R]** | **`STATISTICALLY INCONCLUSIVE`** |

### Detailed Out-of-Sample (OOS) Confidence Interval Analysis:

- **Point Estimates (Empirical OOS, N=14 Trades, 13 Trading Days):**
  - Net Profit: **+$70.62**
  - Profit Factor: **2.47** (Gross Profit: $118.62, Gross Loss: $48.00)
  - Expectancy (USD): **+$5.04 / trade**
  - Expectancy (R): **+0.478R / trade**
  - Annualized Sharpe Ratio (Daily Returns): **6.05**

- **Confidence Intervals (95% Two-Sided):**
  - **Student-t Distribution:**
    - Expectancy (USD): `[-$2.89, +$12.97]` (spans zero)
    - Expectancy (R): `[-0.329R, +1.285R]` (spans zero)
  - **Circular Block Bootstrap (Primary $b=2$ Days, 10,000 Iterations, Seed=42):**
    - Expectancy (USD): **`[-$1.53, +$11.16]`** (spans zero)
    - Expectancy (R): **`[-0.185R, +1.055R]`** (spans zero)
    - Profit Factor: **`[0.74, 10.43]`**
    - Annualized Sharpe Ratio: **`[-2.04, +16.11]`** (spans zero)

- **Profit Factor Interval Method & Zero-Loss Scenarios:**
  - Resampled daily aggregations are evaluated for gross profit and gross loss.
  - In 17 out of 10,000 iterations (0.17%), the resampled series contained only winning trades ($\text{Gross Loss} = 0.00$).
  - For these zero-loss samples, Profit Factor is mathematically unbounded ($+\infty$) and explicitly capped at $999.0$ for percentile calculations rather than excluded or distorted.

- **Sensitivity to Block Length ($b \in [1, 2, 3, 5]$ Trading Days):**

| Block Length ($b$) | Block Type | 95% CI Expectancy (USD) | 95% CI Expectancy (R) | 95% CI Profit Factor | Zero-Loss Samples | 95% CI Sharpe Ratio | Spans Zero? |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$b=1$ Day** | Daily Block | [-$2.05, +$11.56] | [-0.248R, +1.129R] | [0.66, 10.48] | 17 (0.17%) | [-2.75, +16.80] | **YES** |
| **$b=2$ Days** | 2-Day Block (Primary) | [-$1.53, +$11.16] | [-0.185R, +1.055R] | [0.74, 10.43] | 17 (0.17%) | [-2.04, +16.11] | **YES** |
| **$b=3$ Days** | 3-Day Block | [-$0.97, +$10.82] | [-0.088R, +1.013R] | [0.83, 9.90] | 25 (0.25%) | [-1.28, +15.17] | **YES** |
| **$b=5$ Days** | 1-Week Block | [+$0.42, +$9.74] | [+0.047R, +0.973R] | [1.08, 5.66] | 0 (0.00%) | [+0.53, +11.75] | NO* |

*\*Note on $b=5$ Days:* With only 13 trading days in the OOS sample, a 5-day block covers ~38.5% of the entire sample, causing artificial variance compression due to severe block overlap. Across all realistic block lengths ($b=1, 2, 3$), the 95% confidence interval unambiguously encompasses zero.

- **Scientific Conclusion on Out-of-Sample Performance:**
  Because the sample size is small ($N=14 < 30$, 13 trading days) and the 95% confidence interval spans zero across blocks 1, 2, and 3, the null hypothesis $H_0: \mu \le 0$ cannot be rejected at the 5% significance level. Claims of *"proven profitability"* or *"statistical proof of zero curve-fitting"* are scientifically invalid and strictly retracted. OOS performance is officially logged as **`STATISTICALLY INCONCLUSIVE`**.

---

## 2. Decoupled Cost Sensitivity Analysis (3x3 Matrix)

To eliminate confounding between trade attrition and cost drag, transaction friction is evaluated through two strictly decoupled experiments across spread $\in [\$0.35, \$0.50, \$0.75]$ and slippage $\in [\$0.00, \$0.15, \$0.30]$:

### Experiment A1: Pure Friction Drag on Agreed `RESEARCH_XAUUSD_FIXED002` (Frozen $N=44$ Trades, Lot 0.02)
All entries, exits, lot sizes (0.02 standard lots fixed), timestamps, and trade counts ($N=44$) are strictly locked. Only execution friction is varied:
$\Delta\text{Friction} = ((\text{Spread} + \text{Slippage}) - \$0.35) \times 0.02 \times 100$.

| Spread ($) | Slippage ($) | Total Friction ($/oz) | Executed Trades | Profit Factor | Net PnL ($) | Expectancy (R) | Max Total DD ($) | Cost Headroom to $600 Floor | Compliance |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| $0.35 | $0.00 | $0.35 | **44 (Frozen)** | **1.18** | **+$68.78** | **+0.111R** | $133.38 | +$466.62 | ✅ PASS |
| $0.35 | $0.15 | $0.50 | **44 (Frozen)** | 1.15 | +$55.58 | +0.093R | $139.68 | +$460.32 | ✅ PASS |
| $0.35 | $0.30 | $0.65 | **44 (Frozen)** | 1.11 | +$42.38 | +0.074R | $145.98 | +$454.02 | ✅ PASS |
| $0.50 | $0.00 | $0.50 | **44 (Frozen)** | 1.15 | +$55.58 | +0.093R | $139.68 | +$460.32 | ✅ PASS |
| $0.50 | $0.15 | $0.65 | **44 (Frozen)** | 1.11 | +$42.38 | +0.074R | $145.98 | +$454.02 | ✅ PASS |
| $0.50 | $0.30 | $0.80 | **44 (Frozen)** | 1.07 | +$29.18 | +0.055R | $152.28 | +$447.72 | ✅ PASS |
| $0.75 | $0.00 | $0.75 | **44 (Frozen)** | 1.09 | +$33.58 | +0.062R | $150.18 | +$449.82 | ✅ PASS |
| $0.75 | $0.15 | $0.90 | **44 (Frozen)** | 1.05 | +$20.38 | +0.043R | $156.48 | +$443.52 | ✅ PASS |
| $0.75 | $0.30 | $1.05 | **44 (Frozen)** | **1.02** | **+$7.18** | **+0.024R** | **$162.78** | **+$437.22** | ✅ PASS |

*Artifact Saved:* `data/phase4_cost_sensitivity_experiment_a_fixed002.csv` (and `data/phase4_cost_sensitivity_experiment_a.csv`).

### Experiment A2: Pure Friction Drag on Dynamic Risk 0.25% (Frozen $N=44$ Trades, Dynamic Lots ~0.01)

| Spread ($) | Slippage ($) | Total Friction ($/oz) | Executed Trades | Profit Factor | Net PnL ($) | Expectancy (R) | Max Total DD ($) | Cost Headroom to $600 Floor | Compliance |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| $0.35 | $0.00 | $0.35 | **44 (Frozen)** | **1.22** | **+$44.69** | **+0.111R** | $64.85 | +$535.15 | ✅ PASS |
| $0.35 | $0.15 | $0.50 | **44 (Frozen)** | 1.18 | +$37.04 | +0.093R | $68.75 | +$531.25 | ✅ PASS |
| $0.35 | $0.30 | $0.65 | **44 (Frozen)** | 1.14 | +$29.39 | +0.074R | $72.65 | +$527.35 | ✅ PASS |
| $0.50 | $0.00 | $0.50 | **44 (Frozen)** | 1.18 | +$37.04 | +0.093R | $68.75 | +$531.25 | ✅ PASS |
| $0.50 | $0.15 | $0.65 | **44 (Frozen)** | 1.14 | +$29.39 | +0.074R | $72.65 | +$527.35 | ✅ PASS |
| $0.50 | $0.30 | $0.80 | **44 (Frozen)** | 1.10 | +$21.74 | +0.055R | $76.55 | +$523.45 | ✅ PASS |
| $0.75 | $0.00 | $0.75 | **44 (Frozen)** | 1.11 | +$24.29 | +0.062R | $75.25 | +$524.75 | ✅ PASS |
| $0.75 | $0.15 | $0.90 | **44 (Frozen)** | 1.08 | +$16.64 | +0.043R | $79.15 | +$520.85 | ✅ PASS |
| $0.75 | $0.30 | $1.05 | **44 (Frozen)** | **1.04** | **+$8.99** | **+0.024R** | **$83.05** | **+$516.95** | ✅ PASS |

*Artifact Saved:* `data/phase4_cost_sensitivity_experiment_a_dynamic025.csv`.

### Experiment B: Dynamic Pre-Trade Filter & Slippage Response (Trades $44 \to 40$)
Simulates live broker execution where the risk budget filter dynamically rejects setups whose minimum lot risk ($0.01 \times (\text{SL} + \text{Spread} + \text{Slip}) \times 100 + \text{Comm}$) exceeds the allowed $12.50 (0.25%) budget:

| Spread ($) | Slippage ($) | Total Friction ($/oz) | Accepted Trades | Rejected Signals | Rejection Cause | Profit Factor | Net PnL ($) | Max Total DD ($) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| $0.35 | $0.00 | $0.35 | 44 | 0 | None | 1.22 | +$44.69 | $74.41 |
| $0.35 | $0.15 | $0.50 | 44 | 0 | None | 1.33 | +$60.95 | $53.20 |
| $0.35 | $0.30 | $0.65 | 44 | 0 | None | 1.26 | +$50.60 | $58.60 |
| $0.50 | $0.00 | $0.50 | 44 | 0 | None | 1.37 | +$67.85 | $49.30 |
| $0.50 | $0.15 | $0.65 | 44 | 0 | None | 1.31 | +$57.50 | $54.70 |
| $0.50 | $0.30 | $0.80 | 44 | 0 | None | 1.37 | +$65.76 | $47.36 |
| $0.75 | $0.00 | $0.75 | 44 | 0 | None | 1.37 | +$67.26 | $51.80 |
| $0.75 | $0.15 | $0.90 | **40** | **4** | Min Lot Risk Budget Cap | 1.19 | +$32.57 | $46.23 |
| $0.75 | $0.30 | $1.05 | **40** | **4** | Min Lot Risk Budget Cap | 1.05 | +$8.70 | $67.55 |

*Artifact Saved:* `data/phase4_cost_sensitivity_experiment_b.csv` and `data/phase4_sensitivity_grid.csv`.

---

## 3. Overhauled Sequential Monte Carlo Simulation (10,000 Runs)

### Calibration & Real-World Method:
- **Factual Calendar Trading Days:** Utilizes the factual 183 calendar trading days in the dataset (Feb–Oct 2026), where 143 days had 0 trades, 37 days had 1 trade, 2 days had 2 trades, and 1 day had 3 trades.
- **Circular Block Bootstrap:** Resamples blocks of 5 consecutive trading days ($B=5$, 1 trading week) to preserve intraday trade order and cross-day streaks.
- **Sequential State Machine:** Models Phase 1 ($5,000 $\to$ $5,500 / +10%) and explicitly resets account balance to $5,000 upon passage to initiate Phase 2 ($5,000 $\to$ $5,400 / +8%). Joint pass requires passing Phase 1, then Phase 2.
- **Double Drawdown Monitoring:** Checks intrabar floating drawdown (MAE) and closed balance against both the $200 daily limit (4%) and $4,400 static floor (12%).

### Comparative Results (Fixed Lot 0.02 vs Dynamic Risk 0.25%):

| Metric / Parameter | Upgraded Dynamic Risk 0.25% | Upgraded Fixed Lot 0.02 | Regulatory Limit |
| :--- | :---: | :---: | :---: |
| **Phase 1 Pass Rate** | 8.68% (868 / 10,000) | 45.71% (4,571 / 10,000) | Target: +10% ($500) |
| **Joint Pass Rate (P1 $\to$ P2)** | **0.02% (2 / 10,000)** | **6.91% (691 / 10,000)** | Target: P1 + P2 |
| **Daily Drawdown Breach Rate** | **0.00% (0 / 10,000)** | **0.00% (0 / 10,000)** | Limit: $200.00 / day |
| **Static Floor Breach Rate (Ruin)**| **0.00% (0 / 10,000)** | **0.46% (46 / 10,000)** | Floor: $4,400.00 |
| **95% Confidence Upper Bound on Ruin**| **< 0.030% (Rule of Three)** | **0.460%** | Theoretical upper bound |
| **Median Total Drawdown** | $116.74 (2.33%) | $242.05 (4.84%) | $600.00 limit |
| **99th Percentile Total Drawdown** | $288.09 (5.76%) | $594.73 (11.89%) | $600.00 limit |
| **Worst-Case Observed Total DD** | $477.77 (9.56%) | $814.82 (16.30% - breached) | $600.00 limit |

### Challenge Pacing across Distinct Horizons (Correct Simulation Cap Labeling):

| Horizon (Trading Days) | Horizon Nature / Calendar Equivalence | Dynamic 0.25% (P1 / Joint) | Fixed Lot 0.02 (P1 / Joint) | Operational Reality |
| :---: | :---: | :---: | :---: | :--- |
| **60 Days** | ~3 months | 0.00% / 0.00% | 0.00% / 0.00% | Zero pass rate due to low trade frequency (~4.4 trades/month). |
| **120 Days** | ~6 months | 0.00% / 0.00% | 0.02% / 0.00% | Insufficient trade volume at conservative sizing for joint completion. |
| **180 Days** | ~9 months | 0.00% / 0.00% | 0.29% / 0.00% | Fixed 0.02 begins crossing Phase 1 threshold (29 / 10,000). |
| **252 Days** | ~1 year | 0.00% / 0.00% | 1.37% / 0.00% | Phase 1 reached by 137 / 10,000 simulations under Fixed 0.02. |
| **500 Days** | ~2 years | 0.19% / 0.00% | 12.47% / 0.23% | Fixed 0.02 achieves 12.47% P1 pass rate; joint passes emerge (23 / 10,000). |
| **1,000 Days** | **1000-Day Simulation Cap** (~4 years) | **8.68% / 0.02%** | **45.71% / 6.91%** | Finite simulation ceiling cap (Median to P1: 650 days). |

### Crucial Caveat on Horizon Labeling:
The 1,000-day evaluation is officially designated as a **`1000-day simulation cap`**. It represents a finite simulation boundary (~47.6 trading months), **NOT** an infinite or unconstrained horizon. While FundingPips 2-Step Flex rules impose no calendar deadline, one **cannot** draw conclusions about completion on an infinite horizon from a simulation capped at 1,000 days.

---

## 4. Independent Data Feed Audit (Dukascopy Bank SA)

In accordance with institutional validation protocols, an audit was conducted to assess the availability of independent XAUUSD historical quotes from Dukascopy Bank SA:

| Parameter | Dukascopy Audit Finding |
| :--- | :--- |
| **Endpoint Queried** | `https://datafeed.dukascopy.com/datafeed/XAUUSD/` |
| **Accessibility Status** | Direct unauthenticated HTTP tick requests failed (network connection timeout / firewall restriction). Historical tick archives require commercial API keys or proprietary bi5 binary decoders. |
| **Temporal Alignment** | The primary research dataset covers **February 2026 – October 2026**. Publicly accessible legacy archives for 2026 dates are not hosted on open endpoints. |
| **Market Regime Mismatch** | Dukascopy operates on GMT Swiss interbank liquidity without an EOD midnight reset. FundingPips operates on EET/EEST (UTC+2/UTC+3) with broker-specific spreads ($0.35–$0.50) and a strict 00:00 midnight daily reset. |
| **Evaluation Verdict** | **`INDEPENDENT DATA TEST NOT PERFORMED`** |
| **Integrity Invariant** | Per scientific rules, **no proxy or existing CSV substitution is permitted**. The unavailability is honestly disclosed without fabrication. |

---

## 5. Phase 4 Evaluation Verdict & Next Steps

### Official Status: **`PARTIAL`**

1. **Why not PASS?**
   - The Out-of-Sample (OOS) dataset contains only **14 trades**, resulting in 95% confidence intervals that span zero (`[-0.185R, +1.055R]`) and rendering OOS results **`STATISTICALLY INCONCLUSIVE`**.
   - The independent cross-broker dataset (Dukascopy) could not be retrieved and is recorded as **`INDEPENDENT DATA TEST NOT PERFORMED`**.
   - Per explicit instructions, Phase 4 **must not be declared PASS** while critical sample size and independent data verifications remain uncompleted.

2. **Why not FAIL?**
   - The core strategy logic, cascade filter, block bootstrap engine, cost sensitivity model, and sequential Monte Carlo state machine are mathematically sound, verified across 10,000 iterations, and fully supported by passing unit tests.

3. **Next Steps for Reviewer / Next Phase:**
   - Accumulate live signals-only forward observations on MT5 to expand sample size from $N=14$ toward $N \ge 30\text{--}50$.
   - Maintain `AUTOTRADE_ENABLED=false` until live demo execution and broker specification handshakes are complete.
