# SMART TRADER BOT — EXISTING ISSUES REGISTER

**Audit Date:** 2026-11-01 14:00 UTC  
**Phase:** 0 — Baseline & Scope Freeze  
**Policy:** Inventory and preserve all existing anomalies without applying any code or configuration fixes during Phase 0.

---

## 1. Issues Summary Table

| Issue ID | Subsystem / Component | Issue Description | Criticality | Impact Domain | Recommended Phase | Status |
|---|---|---|---|---|---|---|
| **ISSUE-01** | Python Environment / Dependencies | `requirements.txt` is obsolete; lacks `fastapi`, `uvicorn`, `pydantic`, `numpy`, and has version drift on 7 packages | **High** | Reproducibility & Deployment Reliability | Phase 1 | **CONFIRMED** |
| **ISSUE-02** | Project Configuration / Runtime | `.env` contains legacy multi-pair Forex defaults (`H4`, 16 Forex pairs) that conflict with `config.py` XAUUSD profile | **Medium** | Configuration Integrity & Operator Clarity | Phase 1 | **CONFIRMED** |
| **ISSUE-03** | MT5 & Broker Infrastructure | Local MT5 terminal/MetaEditor is absent; live broker specifications (`SYMBOL_*`) cannot be verified locally | **High** | Operational Safety & Live Execution Readiness | Phase 4 / 5 | **CONFIRMED** |
| **ISSUE-04** | Quantitative Research / Statistics | Backtest sample size ($N=10$ to $75$ trades) yields 95% bootstrap confidence intervals spanning zero | **High** | Statistical Credibility & Risk of Overfitting | Phase 2 / 3 | **CONFIRMED** |
| **ISSUE-05** | Dataset Provenance / Feeds | Single-source MT5 historical dataset; independent Dukascopy tick feed connection failed (`UNVERIFIED`) | **Medium** | Historical Data Integrity & Cross-Validation | Phase 2 / 3 | **CONFIRMED** |
| **ISSUE-06** | Repository Hygiene / Artifacts | Bit-for-bit duplicate of `intraday_suite_results.json` exists in both `data/` and `docs/` | **Low** | Disk Hygiene & Repository Redundancy | Phase 1 | **CONFIRMED** |
| **ISSUE-07** | Prop Challenge / Monte Carlo | Trade frequency (57 trades in 8 months) results in an estimated median of 443 trading days to pass 2-Step Flex | **Medium** | Practical Feasibility & Opportunity Cost | Phase 2 / 3 | **CONFIRMED** |

---

## 2. Detailed Technical Issue Dossiers

### ISSUE-01: Dependency Discrepancy & Missing Core Packages in `requirements.txt`
- **Component:** `requirements.txt` / Runtime Environment
- **Detailed Description:** `requirements.txt` was last updated on 2026-09-27. It omits four mission-critical dependencies that are directly imported by the production code: `fastapi` (imported in `webapp/server.py`), `uvicorn` (ASGI runtime), `pydantic` (request validation), and `numpy` (used across `strategies/ict_smc.py` and `scripts/intraday_gold_engine.py`). Furthermore, 7 packages have substantial version drift (e.g., `aiogram` 3.15 vs 3.30, `pandas` 2.2.2 vs 3.0.2).
- **Evidence:** 
  - `requirements.txt` lines 1–12
  - `webapp/server.py` line 14: `from fastapi import FastAPI, HTTPException...`
  - `strategies/ict_smc.py` line 18: `import numpy as np`
  - `pip list` inventory captured in `docs/PHASE0_DEPENDENCIES.md`
- **Criticality:** **High**
- **Impact:** Any automated CI/CD pipeline or fresh developer checkout attempting `pip install -r requirements.txt` will immediately crash on startup.
- **Recommended Phase:** Phase 1 (Update `requirements.txt` and generate `requirements.lock`).

---

### ISSUE-02: Configuration Discrepancy Between `.env` and `config.py`
- **Component:** `config.py` / `.env`
- **Detailed Description:** The active `.env` file on disk defines:
  ```ini
  DEFAULT_TIMEFRAME=H4
  DEFAULT_SYMBOLS=EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD,EURGBP,EURJPY,GBPJPY,EURAUD,GBPAUD,EURCHF,CADJPY,AUDCAD,AUDNZD
  ```
  However, `config.py` hard-bans all 16 of these Forex pairs (`BANNED_PAIRS`) and sets `ACTIVE_TRADING_PROFILE="XAUUSD_INTRADAY"`. While python code safely prioritizes `config.py`, the presence of stale `.env` variables creates operator confusion and misleads deployment scripts.
- **Evidence:**
  - `.env` line 5 (`DEFAULT_TIMEFRAME=H4`), line 6 (`DEFAULT_SYMBOLS=...`)
  - `config.py` line 41 (`BANNED_PAIRS = [...]`), line 640 (`ACTIVE_TRADING_PROFILE = ...`)
- **Criticality:** **Medium**
- **Impact:** Potential operator error during manual inspections; `.env` does not reflect actual institutional XAUUSD Intraday operation.
- **Recommended Phase:** Phase 1 (Synchronize `.env` with `.env.example`).

---

### ISSUE-03: Local MT5 Terminal & Live Broker Specification Unverified
- **Component:** MetaTrader 5 / MQL5 Bridge (`trading/execution_bridge.py`, `trading/ea/SmartTraderBridge.mq5`)
- **Detailed Description:** On the local development environment, no MetaTrader 5 terminal process or installation was detected. As a consequence, live broker symbol specifications (`SYMBOL_TRADE_CONTRACT_SIZE`, `SYMBOL_TRADE_TICK_SIZE`, `SYMBOL_TRADE_TICK_VALUE`, dynamic spreads across London/NY sessions, freeze levels, and swap fees) cannot be queried via active broker connection. All backtests operate on assumed/config parameters (`contract_size=100.0`, `point=0.01`).
- **Evidence:**
  - PowerShell process audit returned 0 MT5 processes.
  - `Program Files` scan found no local MT5 installation.
  - Per project guidelines, status is logged as `BROKER SPECIFICATION NOT VERIFIED`.
- **Criticality:** **High**
- **Impact:** Real order sizing relies on runtime confirmation (`require_confirmed_specs=True`). If the live terminal transmits unexpected contract sizes (e.g. 10 oz mini gold vs 100 oz standard gold), the bot must block execution to prevent catastrophic lot miscalculation.
- **Recommended Phase:** Phase 4 / Phase 5 (VPS & MT5 Deployment Verification).

---

### ISSUE-04: Statistical Inconclusiveness of Current Backtest Sample Size
- **Component:** Quantitative Research (`docs/PHASE3_STATISTICS.json`, `scripts/phase3_research_suite.py`)
- **Detailed Description:** Due to strict intraday filtering (London/NY Open Kill Zones, High-Impact News blocks, EOD Flat cutoff, minimum SL $2.00), the historical sample over 8 months yielded only 10 to 75 trades across models ($N=57$ trades for Model A+B). A 10,000-iteration circular block bootstrap demonstrated that the 95% confidence intervals for expectancy encompass negative values (e.g., Model A+B RR 1:2.0 Expectancy 95% CI is `[-$3.05, +$9.30]`).
- **Evidence:**
  - `docs/PHASE3_STATISTICS.json` models metadata:
    `scientific_verdict: STATISTICALLY INCONCLUSIVE (95% CI includes zero or negative values)`
    `null_hypothesis_rejected: False`
- **Criticality:** **High**
- **Impact:** Backtest profitability cannot be asserted as statistically robust or guaranteed against random walk market noise.
- **Recommended Phase:** Phase 2 / Phase 3 (Expand historical evaluation window or optimize signal capture).

---

### ISSUE-05: Single-Source Historical Data & Inaccessible Dukascopy Independent Feed
- **Component:** Historical Data Feeds (`docs/PHASE3_DATA_PROVENANCE.md`, `data/gold_candles/`)
- **Detailed Description:** All backtesting is grounded exclusively in single-broker candle exports (`FundingPips MT5`). An attempt to independently validate candle integrity against Dukascopy Bank SA tick feeds timed out / was blocked by network firewall restrictions (`URLError: handshake timed out / connection refused`).
- **Evidence:**
  - `docs/PHASE3_DATA_PROVENANCE.md` Section 1: `INDEPENDENT DATA TEST NOT PERFORMED`.
- **Criticality:** **Medium**
- **Impact:** Inability to cross-verify whether specific candle wicks, spreads, or slippage artifacts are unique to FundingPips liquidity feeds.
- **Recommended Phase:** Phase 2 / Phase 3.

---

### ISSUE-06: Redundant Duplicate Result File in Repository
- **Component:** File Storage & Git Repository
- **Detailed Description:** The 945 KB result file `intraday_suite_results.json` exists in duplicate at `data/intraday_suite_results.json` and `docs/intraday_suite_results.json`. The SHA256 hashes are identical (`1676cc6ef1d585290087d1ae833c9cde0d7e4b41beb78afe3854ac99b87a57c0`). Because `data/` is ignored by `.gitignore` and `docs/` is tracked by git, this creates double disk footprint.
- **Evidence:**
  - SHA256 match confirmed in `scratch/audit_inspect.py`.
- **Criticality:** **Low**
- **Impact:** Storage redundancy; minor confusion regarding canonical artifact location.
- **Recommended Phase:** Phase 1 (Clean up redundant path).

---

### ISSUE-07: Extremely Long Calendar Day Pass Horizon in Monte Carlo Simulation
- **Component:** Prop Firm Risk Engineering (`docs/PHASE3_MONTE_CARLO.json`)
- **Detailed Description:** Daily calendar block bootstrap simulation indicates that passing Phase 1 ($500) and Phase 2 ($400) under the FundingPips 2-Step Flex program requires a median of 443 calendar trading days (~1.7 years) at fixed 0.02 lot sizing. The 60-day, 120-day, and 180-day pass rates are all 0.00%.
- **Evidence:**
  - `docs/PHASE3_MONTE_CARLO.json` `horizon_pass_rates` and `daily_block_bootstrap`.
- **Criticality:** **Medium**
- **Impact:** While capital preservation is outstanding (0.00% daily loss breach rate, 0.08% max drawdown breach rate), the capital growth velocity is too slow for typical prop trading expectations.
- **Recommended Phase:** Phase 2 / Phase 3 (Evaluation of dynamic compounding vs fixed lot sizing).
