# SMART TRADER BOT — MASTER PLAN v1.0
# PHASE 1: STRATEGY & RISK DIAGNOSIS REPORT

**Repository:** `abdugafarov-ai/smart-trader-bot`  
**Audit Completion Date:** 2026-11-01  
**Lead Quantitative Specialist:** Antigravity Autonomous Systems  
**Evaluation Scope:** Baseline Signal Ledger ($N=57$ trades), MT5 Historical Candle Dataset (50,000 M5 bars Feb–Oct 2026), Risk Management Pipeline, Pre-Trade Execution Gates, and Issues Register (ISSUE-01 through ISSUE-07).

---

## 1. Executive Summary & Objective

The objective of **Phase 1 (Strategy & Risk Diagnosis)** is to uncover the empirical, mathematical, and algorithmic causes of underperformance in the Smart Trader Bot baseline, diagnose every failure mode across market regimes and execution channels, and establish a priority-ranked remediation blueprint.

### Baseline Summary Metrics (Model A+B Combined @ Fixed 0.02 Lot, RR 1:2.0):
- **Total Trades:** 57 trades over 220 calendar trading days (~7.1 trades/month)
- **Nominal Net P&L:** +$180.84 on $5,000 initial balance (+3.62%)
- **Profit Factor:** 1.40
- **Expectancy:** +$3.17 / trade (+0.114R)
- **Max Total Drawdown:** $164.62 (3.29% of account)
- **Max Daily Drawdown:** $60.22 (1.20% of account)
- **Win Rate (TP):** 29.8% (17 trades)
- **Breakeven (BE) Rate:** 24.6% (14 trades)
- **Loss Rate (SL):** 45.6% (26 trades)
- **Non-Loss Rate (TP + BE):** 54.4% (31 trades)
- **Scientific Verdict:** **STATISTICALLY INCONCLUSIVE** (10,000-run circular block bootstrap 95% Confidence Interval for Net P&L spans zero: `[-$173.89, +$530.15]`).

---

## 2. Granular Strategy Diagnosis: Signal Generation to Execution

### 2.1 Full Signal Chain Dissection
The bot's institutional trading pipeline executes through the following stages:
1. **HTF Market Bias Gate:** H1 20-bar equilibrium midpoint ($EQ = (High_{20} + Low_{20}) / 2$). If $Close_{H1} > EQ \rightarrow Bullish$, else $Bearish$.
2. **Session / Kill Zone Gate:** London Open KZ (08:00–11:00 Europe/London) and New York Open KZ (08:00–11:30 America/New_York) using DST-aware timezone conversions.
3. **Institutional Timing Bans:** Prohibits new entries 15 minutes prior to London close (15:45–16:00 UTC) and during the post-London dead zone (17:00–00:00 UTC).
4. **Economic News Guard:** Prohibits trading $\pm 30$ minutes surrounding high-impact USD macroeconomic releases (NFP, CPI, PPI, FOMC, GDP, PCE, ISM, Initial Jobless Claims).
5. **M15 POI & Liquidity Sweep Detection:**
   - **Model A:** Liquidity wick sweep of M15 swing high/low $\rightarrow$ displacement candle ($> 0.4 \times ATR$) $\rightarrow$ MSS $\rightarrow$ Fair Value Gap (FVG) creation $\rightarrow$ entry upon retest.
   - **Model B:** Retracement into unmitigated M15 Order Block situated strictly in Discount ($< 45\%$ dealing range) for longs or Premium ($> 55\%$) for shorts $\rightarrow$ M5 reversal confirmation.
6. **Pre-Trade Risk & Execution Bridge Gates:**
   - Telemetry check: `balance > 0`, `equity > 0`.
   - Broker specs check: contract size, tick size, tick value confirmed.
   - Live spread tolerance check: max 6.0 pips ($0.60/oz).
   - Min lot risk budget check: if minimum lot ($0.01$) dollar risk exceeds the allowed risk budget ($0.25\% = \$12.50$), order is rejected (`NO TRADE`).

---

## 3. Root Cause Analysis: The Three Confirmed Vulnerabilities

### Root Cause 1: Severe Drawdown in `STRONG_UPTREND` (-$60.22, PF 0.63)
A rigorous attribution of trades across zero-lookahead trend regimes revealed an acute vulnerability:

| Trend Regime | Scanned Bars | Trades | Wins (TP) | Breakevens (BE) | Losses (SL) | Win % (TP) | Non-Loss % | Profit Factor | Net P&L (USD) | Expectancy (USD) |
|---|---|---|---|---|---|---|---|---|---|---|
| **STRONG_UPTREND** | 2,069 | 15 | 3 | 3 | 9 | 20.0% | 40.0% | **0.63** | **-$60.22** | -$4.01 |
| **STRONG_DOWNTREND**| 2,197 | 20 | 5 | 5 | 10 | 25.0% | 50.0% | 1.14 | +$25.32 | +$1.27 |
| **WEAK_TREND** | 924 | 16 | 8 | 2 | 6 | 50.0% | 62.5% | **3.16** | **+$200.70** | +$12.54 |
| **SIDEWAY** | 290 | 6 | 1 | 4 | 1 | 16.7% | 83.3% | **1.74** | **+$15.04** | +$2.51 |

#### Empirical Finding:
In `STRONG_UPTREND`, the strategy lost -$60.22 with a disastrous 0.63 Profit Factor. 9 out of 15 trades hit full Stop Loss!  
Without `STRONG_UPTREND` losses, the remaining 42 trades produced **+$241.06** with a Profit Factor of **2.18**.

#### Mathematical & Algorithmic Cause:
1. **Unexhausted Retracement Cascade:** During parabolic H1 uptrends, gold pullbacks are violent and volatile. The baseline Model B relied on a simplistic 3-bar M5 trigger (`b_close > m5_prev3_high`). This entered long during active, multi-bar M15 downward cascades (`Close[-1] < Close[-2] < Close[-3]`) and heavy H1 drops ($> 0.70 \times ATR$), effectively catching falling knives before structural stabilization.
2. **Deceptive Micro-Discount:** In a multi-day uptrend, calculating discount on a narrow 15-bar M15 slice resulted in buying at the local high of the daily move, rather than genuine structural equilibrium.

### Root Cause 2: Naive Microscopic M5 Trigger in Model B
In `scripts/intraday_gold_engine.py`:
```python
if b_close > b_open and b_low <= ob_h and b_close >= ob_l and b_close > m5_prev3_high:
    sl = min(ob_l, b_low) - 0.50
```
Checking only `m5_prev3_high` (the highest high of the previous 15 minutes) is not a true Market Structure Shift (MSS). In institutional SMC, a structure shift requires breaking a confirmed fractal pivot high with closed-candle body momentum.

### Root Cause 3: Static Lot Sizing & Absence of Dynamic Risk Scaling
- Fixed 0.02 lot sizing never scales down when the account suffers consecutive losses.
- During a 3-loss streak, the account absorbs $3 \times \$20.00 = \$60.00$ of cumulative drawdown, forcing long recovery periods.
- Monte Carlo simulations indicated that a fixed 0.02 lot sizing required a median of **443 trading days** to achieve the +10% ($500) and +8% ($400) targets under FundingPips 2-Step Flex.

---

## 4. Diagnosis of Data, Statistics, and Operational Integrity

1. **Dataset Integrity:** The historical M5, M15, and H1 datasets (50,000 bars) from FundingPips MT5 are continuous and free of bar gaps during active trading hours. Timestamps accurately localise EET/EEST broker hours to UTC.
2. **Look-Ahead Bias Audit:** `np.searchsorted` with `side='right'` strictly indexes closed bars (`t_np - td_15m` and `t_np - td_1h`), guaranteeing zero forward-looking data leakage.
3. **Dependency Discrepancies (ISSUE-01):** `requirements.txt` was outdated, missing `fastapi`, `uvicorn`, `pydantic`, `numpy`, `reportlab`, and `websockets`.
4. **Configuration Pollution (ISSUE-02):** Active `.env` contained stale Forex pairs (`DEFAULT_SYMBOLS=EURUSD,GBPUSD...`) and `DEFAULT_TIMEFRAME=H4`.
5. **Redundant Artifacts (ISSUE-06):** Duplicate 945 KB `intraday_suite_results.json` existed in `data/` and `docs/`.
6. **Legacy MT4 EA Unquarantined:** `trading/ea/SmartTraderBridge.mq4` had hardcoded `InpFixedLot = 0.03` with zero risk management, presenting an operational hazard.

---

## 5. Priority-Ranked Remediation Roadmap

| Priority | Task ID | Description | Targeted Phase |
|---|---|---|---|
| **P0 (Critical)** | `FIX-01` | Implement **Strong Trend Structural Exhaustion Guard** to eliminate falling-knife entries in `STRONG_UPTREND`. | Phase 3 |
| **P0 (Critical)** | `FIX-02` | Implement **Dynamic Risk Scaling & Streak Pauses** in `ExecutionBridge` (reduce risk on streaks/drawdown, pause after 3 losses/day). | Phase 2 |
| **P0 (Critical)** | `FIX-03` | Quarantine legacy `SmartTraderBridge.mq4` to prevent unintended live execution. | Phase 5 |
| **P1 (High)** | `FIX-04` | Synchronize risk controls, min-lot checks, and SL widening bans in `SmartTraderBridge.mq5`. | Phase 2 |
| **P1 (High)** | `FIX-05` | Update `requirements.txt` and clean `.env` to eliminate configuration drift (ISSUE-01, ISSUE-02). | Phase 1 |
| **P2 (Medium)** | `FIX-06` | Conduct full Train/Validation/OOS and 10,000-run Monte Carlo block bootstrap on upgraded logic. | Phase 4 |
| **P2 (Medium)** | `FIX-07` | Compile comprehensive Old vs New verification matrix and deliver final GO / NO-GO assessment. | Phase 6 |
