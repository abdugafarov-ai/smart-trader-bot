# SMART TRADER BOT — MASTER PLAN v1.0
# PHASE 0: BASELINE & SCOPE FREEZE — FINAL AUDIT REPORT

**Repository:** [abdugafarov-ai/smart-trader-bot](https://github.com/abdugafarov-ai/smart-trader-bot)  
**Project:** Smart Trader Bot — XAUUSD Intraday ICT/SMC  
**Plan:** MASTER UPGRADE & CHALLENGE-READINESS PLAN v1.0  
**Phase:** Phase 0 — Baseline & Scope Freeze  
**Audit Completion Date:** 2026-11-01 14:00 UTC (01.11.2026)  
**Lead Quantitative Auditor:** Antigravity Autonomous Investigation Specialist  

---

## 1. Executive Summary & Purpose

The objective of **Phase 0 (Baseline & Scope Freeze)** is to freeze the real, unvarnished state of the Smart Trader Bot codebase, establish a 100% reproducible baseline, and document all configuration parameters, dependencies, datasets, architectural call chains, test suites, and empirical research findings before any modifications or enhancements begin.

**Strict Phase 0 Mandates Observed:**
- **Zero Modifications to Logic:** No trading logic, strategy rules, risk formulas, or configuration values were altered.
- **Safety Enforcement:** `AUTOTRADE_ENABLED=false` was strictly maintained across all modules and tests. No real, demo, or test trading orders were placed.
- **Non-Destructive Operations:** No files were overwritten, deleted, or reset. Git working tree was preserved in a clean state.
- **8 Dedicated Baseline Artifacts:** Created in `docs/` with explicit cryptographic hashes and evidence citations.

---

## 2. Phase 0 Final Verification & Compliance Matrix

| Verification Domain | Baseline Status | Concrete Evidence / Artifact |
|---|---|---|
| **Git Baseline** | **PASS** | Commit `ad1eea881e39205e56ec8703328e925a84e46d11`, branch `main`, working tree clean. Full log in `docs/PHASE0_GIT_BASELINE.txt`. |
| **Working Tree Cleanliness** | **PASS** | `git status`, `git diff`, and `git diff --cached` return zero modified or staged tracked files. |
| **Safety Circuit Breakers** | **PASS** | `AUTOTRADE_ENABLED=false`. All order dispatch logic blocked by research profile guards and unconfirmed broker checks. |
| **Configuration Audit** | **PASS** | Full parameter audit of `config.py`, `.env.example`, `challenge_config.json`. Active profile: `XAUUSD_INTRADAY`. |
| **Python Dependencies** | **PASS (Audited)** | Python 3.12.2, pip 26.0.1, 91 installed packages inventoried in `docs/PHASE0_DEPENDENCIES.md`. Identified 4 critical undeclared packages. |
| **MQL5 & MetaTrader 5** | **PASS (Audited)** | EA source `SmartTraderBridge.mq5` and compiled binary `SmartTraderBridge.ex5` (84,684 bytes) verified. Local MT5/MetaEditor absent. |
| **Dataset Manifest** | **PASS** | 5 gold OHLCV CSVs (Feb–Oct 2026, 50,000 M5 bars), SQLite DB, and research artifacts indexed in `docs/PHASE0_DATASET_MANIFEST.csv`. |
| **XAUUSD Broker Specs** | **UNVERIFIED (Logged)** | Marked `BROKER SPECIFICATION NOT VERIFIED` due to lack of local live MT5 terminal connection. Backtest values: 100 oz, $3.00/lot comm. |
| **Architecture Mapping** | **PASS** | Complete 6-tier Mermaid diagram and call-chain documentation generated in `docs/PHASE0_ARCHITECTURE.md`. |
| **Strategy Baseline** | **PASS** | Reconciled Model A, Model B, Model A+B, RR matrices, and Monte Carlo from JSON/CSV files in `docs/PHASE0_STRATEGY_BASELINE.md`. |
| **Automated Test Suite** | **PASS** | **124 tests executed: 124 PASS, 0 FAIL, 0 SKIP**. Runtime: 32.918s. Exit code: 0. Logged in `docs/PHASE0_TEST_BASELINE.md`. |
| **Issues Register** | **PASS** | 7 technical defects and research gaps cataloged without modification in `docs/PHASE0_ISSUES_REGISTER.md`. |

---

## 3. Git Baseline Audit

- **Current Branch:** `main` (synchronized with `origin/main`).
- **HEAD Commit SHA:** `ad1eea881e39205e56ec8703328e925a84e46d11`
- **Last Commit Message:** `docs(phase3): add Phase 3 research validation executive PDF report`
- **Previous Milestone Commit:** `d49844485aa5ef90b505d1bcc77eeeb20eaa6611` (`fix(core): normalize multi-timezone datetimes across trading window, rollover, and execution bridge`) exists as a direct parent in the commit graph.
- **Working Tree Status:** Clean (`nothing to commit, working tree clean`).
- **Local / Remote Branches:**
  - Local branches: `main` (HEAD), `backup/pre-audit-worker`, `checkpoint-before-critical-fixes`, `checkpoint-pre-review`, `feat-xauusd-ict-audit`.
  - Remote tracking: `origin/main`, `origin/feat-xauusd-ict-audit`.
- **Remote URL:** `https://abdugafarov-ai@github.com/abdugafarov-ai/smart-trader-bot.git` (no secrets exposed).

---

## 4. Configuration Baseline & Parameter Registry

The project configuration is governed by `config.py` (33,798 bytes), `.env.example` (1,671 bytes), and `challenge_config.json` (702 bytes).

### Parameter Inventory Table

| Parameter Name | Current Value | Primary Source | Consuming Components / Where Used |
|---|---|---|---|
| `ACTIVE_TRADING_PROFILE` | `"XAUUSD_INTRADAY"` | `config.py` (line 640) | `config.validate_config()`, `execution_bridge.py`, `auto_signals.py` |
| `AUTOTRADE_ENABLED` | `False` | `config.py` (line 65) / `.env` | `execution_bridge.py`, `bot/handlers.py` (autopilot toggle guard) |
| `AUTOTRADE_DEFAULT_RISK` | `0.25%` ($12.50 on $5k) | `config.py` (line 66) | `execution_bridge.py` (`calculate_lot()`), `/risk` command |
| `AUTOTRADE_MAX_RISK` | `0.50%` ($25.00 on $5k) | `config.py` (line 67) | `execution_bridge.py` (risk cap enforcement) |
| `AUTOTRADE_DEFAULT_LOT` | `0.01` lot | `config.py` (line 68) | `execution_bridge.py`, `SmartTraderBridge.mq5` (`InpFixedLot`) |
| `RESEARCH_XAUUSD_FIXED002` | Dict (Fixed 0.02, RR 1.5/2.0/2.5) | `config.py` (lines 579–623) | `scripts/intraday_gold_engine.py`, `scripts/phase3_research_suite.py` |
| `CHALLENGE_BROKER` | `"FundingPips"` | `challenge_config.json` | `config.py`, research reports, broker timezone mapper |
| `CHALLENGE_PLAN` | `"$5k · 2 Step Flex"` | `challenge_config.json` | Challenge rule evaluator, Monte Carlo simulator |
| `CHALLENGE_INITIAL_BALANCE` | `$5,000.00` | `challenge_config.json` | Balance base for risk budgets and equity calculations |
| `CHALLENGE_DAILY_LOSS_PCT` | `4.0%` ($200.00) | `challenge_config.json` | Official broker stop limit |
| `MAX_DAILY_DRAWDOWN_PCT` | `2.5%` ($125.00) | `config.py` (line 341) | Internal Bot Safety Circuit Breaker ($75 buffer to broker stop) |
| `CHALLENGE_MAX_LOSS_PCT` | `12.0%` ($600.00) | `challenge_config.json` | Broker static equity floor ($4,400.00) |
| `HARD_EQUITY_STOP_USD` | `$4,450.00` | `config.py` (line 522) | Emergency Panic Close Trigger ($50 buffer above broker floor) |
| `MAX_CONCURRENT_POSITIONS` | `1` | `config.py` (line 344) | Order slots check in `auto_signals.py` and `execution_bridge.py` |
| `COMMISSION_PER_LOT_USD` | `$3.00` ($1.50 per side) | `config.py` (line 534) | `calculate_lot()` friction deduction, backtest engine |
| `SLIPPAGE_BUFFER_USD` | `$0.20` (20 broker points) | `config.py` (line 535) | `calculate_lot()` conservative price buffer |
| `DEFAULT_SPREAD_BUFFER_USD`| `$0.30` | `config.py` (line 536) | Fallback spread allowance when live ticks are unconfirmed |
| `MAX_SPREAD_PIPS` | `6.0` pips ($0.60) | `config.py` (line 286) | Spread spike guard in `auto_signals.py` |
| `MIN_SL_PIPS` (XAUUSD) | `$2.00` | `config.py` (line 377) | Minimum structural SL filter in `strategies/ict_smc.py` |
| `KILL_ZONES_UTC` | London (07:00-10:00), NY (12:00-15:00)| `config.py` (line 126) | DST-aware session timing filters in `auto_signals.py` |
| `IPDA_TIMING_BANS` | 15:45-16:00 UTC & 17:00-00:00 UTC | `config.py` (line 171) | Institutional pre-close & dead-zone liquidity bans |
| `ROLLOVER_WINDOW` | 20:30–21:45 UTC | `config.py` (line 401) | Banking rollover spread widening filter |
| `EOD_FLAT_CLOSE_UTC` | 20:40 UTC Mon–Thu, 19:30 UTC Fri | `config.py` (line 442) | Forced daily position liquidation before swap charges |
| `WEEKLY_WINDOW` | Mon 07:00 UTC – Fri 14:00 UTC | `config.py` (line 291) | Weekend gap and thin Monday morning liquidity filter |

---

## 5. Runtime & Python Dependency Baseline

- **Operating System:** Windows 11 Home/Pro (Build 26300) 64-bit
- **Python Version:** 3.12.2 (CPython 64-bit)
- **Pip Version:** 26.0.1
- **Interpreter Path:** `C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe`
- **Total Installed Packages:** 91 packages cataloged via `pip list` in `docs/PHASE0_DEPENDENCIES.md`.

### Critical Dependency Audit Findings:
1. **Omitted Production Dependencies:** Four essential packages used directly in production code are missing from `requirements.txt`:
   - `fastapi` (`0.139.2`) — Required for the MT5 HTTP bridge server in `webapp/server.py`.
   - `uvicorn` (`0.51.0`) — ASGI server runner.
   - `pydantic` (`2.13.4`) — Data serialization and API models.
   - `numpy` (`2.4.4`) — Vectorized calculations in `strategies/ict_smc.py` and research suites.
2. **Version Drift:** `requirements.txt` specifies `pandas==2.2.2`, `aiogram==3.15.0`, and `matplotlib==3.8.4`, whereas the active environment runs `pandas 3.0.2`, `aiogram 3.30.0`, and `matplotlib 3.11.1`.
3. **No MetaTrader5 Python DLL:** The architecture does not rely on the `MetaTrader5` python package; it communicates with MT5 via the MQL5 WebRequest HTTP bridge.

---

## 6. MQL5 & MetaTrader 5 Baseline

- **Expert Advisor Source File:** `trading/ea/SmartTraderBridge.mq5` (82,797 bytes, 1,658 lines)
- **Pre-compiled Binary:** `trading/ea/SmartTraderBridge.ex5` (84,684 bytes, exists on disk)
- **Legacy MQL4 Bridge:** `trading/ea/SmartTraderBridge.mq4` (4,279 bytes, 114 lines)
- **Included MQL5 Libraries:** `<Trade\Trade.mqh>` (standard MetaQuotes trade execution library)
- **Core Input Parameters:**
  - `InpServerUrl`: `"http://127.0.0.1:8080"` (Bridge REST endpoint)
  - `InpPollInterval`: `3` seconds
  - `InpMagicNumber`: `888001`
  - `InpMaxOpenOrders`: `1` (strictly 1 active position for Gold)
  - `InpRiskPercent`: `0.25%`
  - `InpFixedLot`: `0.01` lot
  - `InpCommissionPerLot`: `$3.00`
  - `InpUseAutoBE`: `true` (Trigger at 1.0R, offset +35 points / +$0.35)
- **Local Compilation Environment:** MetaEditor and local MT5 terminal were **not found** in standard Windows directories. The live staging/production terminal is deployed on a Linux VPS under Wine (`/home/trader/.wine/drive_c/Program Files/MetaTrader 5`).

---

## 7. Dataset Baseline & Cryptographic Integrity

All datasets utilized by the research and historical backtesting engines were inspected, checked for nulls and duplicates, and cataloged in `docs/PHASE0_DATASET_MANIFEST.csv`:

| Dataset File | File Path | Record Count | Temporal Coverage | Duplicate / Nulls | SHA256 Hash |
|---|---|---|---|---|---|
| `candles_XAUUSD_M5.csv` | `data/gold_candles/candles_XAUUSD_M5.csv` | 50,000 bars | 2026-02-12 11:15 → 2026-10-27 11:05 | 0 Dups / 0 Nulls | `caf57988e7506383d46b2bcf175194c6cbfa630412dfeba9fa5cab386c7ea80f` |
| `candles_XAUUSD_M15.csv` | `data/gold_candles/candles_XAUUSD_M15.csv` | 35,000 bars | 2025-05-05 12:45 → 2026-10-27 11:00 | 0 Dups / 0 Nulls | `7e2f51c110186e8ffb989e407be83220521cb3023f046dea5e8cc24fe6f3ee43` |
| `candles_XAUUSD_H1.csv` | `data/gold_candles/candles_XAUUSD_H1.csv` | 15,000 bars | 2024-04-15 03:00 → 2026-10-27 11:00 | 0 Dups / 0 Nulls | `d39b0be023a5a38ecd8161558f9ade856d5b6c1bccd0a6dd93cbaebb5804242e` |
| `candles_XAUUSD_H4.csv` | `data/gold_candles/candles_XAUUSD_H4.csv` | 5,000 bars | 2023-08-21 04:00 → 2026-10-27 08:00 | 0 Dups / 0 Nulls | `f6798d9d1c054bb2ba03cbbd6a2d678d58b6819acc03fc90bf76a2b8ce04425b` |
| `candles_XAUUSD_D1.csv` | `data/gold_candles/candles_XAUUSD_D1.csv` | 2,000 bars | 2020-01-09 00:00 → 2026-10-27 00:00 | 0 Dups / 0 Nulls | `4210585ebf4f822caf008dfff0de7cf5ce847fb71ee4075dd02846876e65c0d2` |
| `intraday_suite_results.json` | `data/intraday_suite_results.json` | 84 experiments | 2026-02-12 → 2026-10-27 | 0 Dups / 0 Nulls | `1676cc6ef1d585290087d1ae833c9cde0d7e4b41beb78afe3854ac99b87a57c0` |
| `signals.db` | `data/signals.db` | 35 total rows (8 signals)| Historical Operational | 0 Nulls | `c723383e8b5d6ce858bc73132d88eef37f1392811c5962c750290f15f3ea2493` |

- **Data Provenance:** Candles were exported directly from the **FundingPips MT5 broker terminal** in native `Europe/Helsinki` server time. Independent Dukascopy tick validation timed out, logged as `INDEPENDENT DATA TEST NOT PERFORMED` in `docs/PHASE3_DATA_PROVENANCE.md`.

---

## 8. XAUUSD Broker Specifications Audit

In accordance with Section 8 guidelines, broker specifications are explicitly decomposed into confirmed broker telemetry versus configuration defaults:

| Specification Parameter | Live Broker Telemetry | Project Configuration Value | Verification Status |
|---|---|---|---|
| **Contract Size** | Unconfirmed via live socket | `100.0` oz | `BROKER SPECIFICATION NOT VERIFIED` (Assumed standard) |
| **Trade Tick Size** | Unconfirmed via live socket | `0.01` USD | `BROKER SPECIFICATION NOT VERIFIED` (Assumed standard) |
| **Trade Tick Value** | Unconfirmed via live socket | `$1.00` per lot ($0.01 × 100) | `BROKER SPECIFICATION NOT VERIFIED` |
| **Account Currency** | Unconfirmed via live socket | `"USD"` | `BROKER SPECIFICATION NOT VERIFIED` |
| **Minimum Volume (Lot)**| Unconfirmed via live socket | `0.01` lot | Configured default in `config.py` |
| **Maximum Volume (Lot)**| Unconfirmed via live socket | `0.50` lot (hard cap) | Configured default in `config.py` |
| **Volume Step** | Unconfirmed via live socket | `0.01` lot | Configured default in `config.py` |
| **Base Spread** | Dynamic ($0.35–$0.50 typical) | Buffer `$0.30` (Max `$0.60` / 6 pips) | Empirical backtest average |
| **Commission** | Quoted broker term | `$3.00` per lot round turn | Matches FundingPips official fee schedule |
| **Swap Rates** | Live broker table | Ignored (Intraday EOD Flat liquidates at 20:40 UTC) | Not applicable to intraday operations |
| **Digits** | Unconfirmed via live socket | `2` (e.g. 2650.50) | Assumed standard for gold |
| **Stop / Freeze Level** | Unconfirmed via live socket | Handled by structural buffer ($0.50) | `BROKER SPECIFICATION NOT VERIFIED` |

---

## 9. System Architecture & Component Mapping

The detailed end-to-end architecture is mapped in `docs/PHASE0_ARCHITECTURE.md`:
1. **Market Data:** MT5 Historical Bars $\rightarrow$ `market/data_fetcher.py` (closed candles only) $\rightarrow$ `market/indicators.py`.
2. **Strategy Analysis:** `strategies/ict_smc.py` determines D1 trend, H1 dealing range equilibrium, and evaluates Model A (Sweep MSS FVG) and Model B (Trend OB Pullback).
3. **Filter Pipeline:** `notifications/auto_signals.py` enforces Smart Weekly Window, IPDA timing bans, high-impact news blocks, London/NY Kill Zones, daily signal limits, and minimum SL ($2.00).
4. **Risk & Sizing:** `trading/execution_bridge.py` validates account equity against the $4,450 hard stop and daily loss limits, then calculates safe lot sizes ($0.25\%$ risk or $0.02$ fixed lot) factoring in spread, commission, and slippage buffers.
5. **Bridge Server:** `webapp/server.py` hosts FastAPI endpoints (`/api/v1/bridge/orders`, `/api/v1/bridge/report`).
6. **Execution & Management:** `SmartTraderBridge.mq5` polls orders via HTTP WebRequest, submits MT5 orders, manages Breakeven adjustments at 1.0R, and enforces EOD Flat liquidation at 20:40 UTC.

---

## 10. Baseline Strategy & Quantitative Research Results

All empirical research figures in `docs/PHASE3_STATISTICS.json`, `docs/PHASE3_MONTE_CARLO.json`, and `docs/PHASE3_COST_SENSITIVITY.csv` were audited and cross-verified:

### 10.1 Key Performance Metrics (Model A+B Fixed 0.02 Lot, RR 1:2.0)
- **Total Executed Trades:** 57 trades (Feb–Oct 2026)
- **Outcome Distribution:** 17 TP Wins (29.8%), 14 Breakevens (24.6%), 26 Losses (45.6%)
- **Non-Loss Rate:** **54.4%**
- **Net Profit:** **+$180.84**
- **Profit Factor:** **1.40**
- **Expectancy:** **+$3.17 per trade** (+0.115R)
- **Annualized Sharpe Ratio:** **2.34**
- **Median Max Drawdown:** **$126.43** (95th percentile: $258.93)
- **Maximum Consecutive Losses:** 5 trades

### 10.2 Scientific Statistical Significance Finding
Despite positive nominal metrics, a 10,000-sample circular block bootstrap ($b=5$ trades) yields a 95% Confidence Interval for Net Profit of **`[-$173.89, +$530.15]`** and Expectancy of **`[-$3.05, +$9.30]`**.
Because the confidence interval crosses zero, the hypothesis of zero statistical edge cannot be rejected at the 95% confidence level ($p > 0.05$).
**Scientific Verdict:** `STATISTICALLY INCONCLUSIVE`. No claims of proven long-term profitability are made.

### 10.3 Prop Firm Monte Carlo Simulation (FundingPips 2-Step Flex $5k)
- **10,000 Simulations:** Zero daily loss limit breaches (**0.00%**).
- **Max Drawdown Breach Rate:** **0.08%** (safe margin of ~$245 to $4,400 floor).
- **Calendar Time Horizon:** Due to conservative trade frequency (57 trades in 220 days), joint pass probability within 60 days is **0.00%**; median trading days to pass both phases is **443 days** (~1.7 years).

---

## 11. Automated Test Baseline

- **Exact Execution Command:**
  ```powershell
  python -m unittest discover -s tests -p "test_*.py"
  ```
- **Execution Timestamp:** 2026-11-01 14:00 UTC
- **Execution Time:** 32.918 seconds
- **Exit Code:** `0`
- **Result:** **124 tests run, 124 passed, 0 failed, 0 skipped.**
- **Modules Verified:** All 13 test suites passed cleanly (`test_audit_critical_defects`, `test_closed_candle_protection`, `test_comprehensive_audit`, `test_controlled_research_audit`, `test_critical_fixes`, `test_fvg_and_ob`, `test_intraday_time_and_sessions`, `test_missing_data_rejection`, `test_phase3_technical_stress`, `test_risk_and_lot_sizing`, `test_state_restoration`, `test_swings_and_structure`, `test_trade_history_journal`).
- **Safety Assurance:** Verified zero order placement, zero network sockets opened, and zero state mutation during testing.

---

## 12. Existing Issues Register

Seven issues were formally documented in `docs/PHASE0_ISSUES_REGISTER.md` for resolution in future phases:
1. **ISSUE-01 (High):** `requirements.txt` lacks `fastapi`, `uvicorn`, `pydantic`, `numpy`, and exhibits version drift. (Target: Phase 1)
2. **ISSUE-02 (Medium):** `.env` contains legacy 16-pair Forex/H4 configuration conflicting with `config.py` XAUUSD profile. (Target: Phase 1)
3. **ISSUE-03 (High):** Local MT5 terminal absent; live broker symbol specifications unverified locally. (Target: Phase 4/5)
4. **ISSUE-04 (High):** Sample size $N=10$ to $75$ trades yields statistically inconclusive 95% bootstrap intervals. (Target: Phase 2/3)
5. **ISSUE-05 (Medium):** Single-source broker data; independent Dukascopy tick feed timed out. (Target: Phase 2/3)
6. **ISSUE-06 (Low):** Redundant duplicate file `intraday_suite_results.json` in both `data/` and `docs/`. (Target: Phase 1)
7. **ISSUE-07 (Medium):** Ultra-slow challenge pass trajectory (median 443 trading days) due to low trade frequency. (Target: Phase 2/3)

---

## 13. Scope Freeze Declaration & Phase 0 Completion

Phase 0 of the MASTER UPGRADE & CHALLENGE-READINESS PLAN v1.0 is **OFFICIALLY COMPLETE**.

All eight required Phase 0 documentation artifacts have been compiled, cryptographically cross-verified, and saved in `docs/`:
1. `docs/PHASE0_BASELINE_REPORT.md` (This document)
2. `docs/PHASE0_GIT_BASELINE.txt`
3. `docs/PHASE0_DEPENDENCIES.md`
4. `docs/PHASE0_DATASET_MANIFEST.csv`
5. `docs/PHASE0_ARCHITECTURE.md`
6. `docs/PHASE0_STRATEGY_BASELINE.md`
7. `docs/PHASE0_TEST_BASELINE.md`
8. `docs/PHASE0_ISSUES_REGISTER.md`

**Status:** The current project state is frozen, verified, and ready for transition to Phase 1.
