# Phase 6: Offline Historical Replay & Decision Matrix Report

**Target Asset:** XAUUSD (Gold) Intraday Trading (H1 $\to$ M15 $\to$ M5)  
**Prop Firm Specification:** FundingPips $5,000 2-Step Flex  
**Evaluation Harness:** `scripts/run_demo_forward_simulation.py`  
**Classification:** **Offline Historical Replay & Execution Stress Simulation**  
**Date of Run:** 2026-11-01 16:51:47 UTC  
**Offline Replay Verdict:** **GO (OFFLINE HISTORICAL REPLAY PASS / REAL DEMO FORWARD PENDING)**  
**Overall Phase 6 Status:** **`PARTIAL (OFFLINE REPLAY: PASS / REAL DEMO FORWARD: REAL_DEMO_FORWARD_NOT_PERFORMED)`**  

---

## 1. Executive Summary & Distinction: Replay vs Live Demo

This report documents an **offline historical replay and intraday stress simulation** executed across 5 historical market sessions from October 2026.
- **Offline Historical Replay:** Replays historical bars with synthetic spread models, simulated latency, SL anti-widening enforcement, and synthetic loss streak injection.
- **Real Demo Forward (Live Observation):** **`REAL_DEMO_FORWARD_NOT_PERFORMED`**. No live forward market observation has been conducted yet with live MT5 broker ticks, real-time bid/ask quotes, and live network polling.
- **Safety Invariant:** **AUTOTRADE_ENABLED=false** was strictly preserved. Zero live orders have been placed.

---

## 2. Session-by-Session Performance Breakdown (October 2026 Historical Replay)

| Session | Date | Day Type | Start Balance | End Balance | Net PnL | Max Session DD | Circuit Breaker |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 2026-10-21 | Normal Trading | $5,000.00 | $5,012.21 | $+12.21 | $0.00 | Active |
| 2 | 2026-10-22 | Normal Trading | $5,012.21 | $5,006.46 | $-5.75 | $9.12 | Active |
| 3 | 2026-10-23 | Normal Trading | $5,006.46 | $5,018.68 | $+12.22 | $0.00 | Active |
| 4 | 2026-10-26 | Stress Test | $5,018.68 | $4,981.18 | $-37.50 | $37.50 | PAUSED (3 Losses) |
| 5 | 2026-10-27 | Normal Trading | $4,981.18 | $4,981.18 | $+0.00 | $0.00 | Active |

---

## 3. Definitive Phase 6 Decision Matrix

### 3.1 Offline Historical Replay Rules (8 / 8 PASS)

| Verification Criterion | Broker / Plan Threshold | Observed Performance | Evaluation Status |
| :--- | :---: | :---: | :---: |
| **FundingPips Max Daily Drawdown (< $200.00 / 4.0%)** | 200.0 | 37.5 | **PASS** |
| **FundingPips Max Total Drawdown (< $600.00 / 12.0%)** | 600.0 | 39.54 | **PASS** |
| **Hard Equity Floor ($4,400.00 broker limit, $4,450.00 hard stop)** | 4400.0 | 4981.18 | **PASS** |
| **Zero Overnight / Weekend Rollover Exposure (EOD Flat Enforcement)** | N/A | Verified | **PASS** |
| **Stop Loss Anti-Widening Enforcement (100% rejection rate)** | N/A | Verified | **PASS** |
| **MT5 Deal Ticket Deduplication & Idempotency** | N/A | Verified | **PASS** |
| **Circuit Breaker Response (Dynamic Risk Throttling & Daily Pause)** | N/A | 3 events captured (Streak throttling & lockout verified) | **PASS** |
| **Safety Invariant: AUTOTRADE_ENABLED=false (Zero Live Orders)** | N/A | Verified | **PASS** |

### 3.2 Real Demo Forward Market Observation Status

| Verification Criterion | Observation Mode | Observed Status | Evaluation Status |
| :--- | :---: | :---: | :---: |
| **Real Demo Forward Observation (3–5 Live Sessions)** | SIGNALS_ONLY (Zero Orders) | REAL_DEMO_FORWARD_NOT_PERFORMED (Offline historical replay only; live MT5 feed observation pending) | **`REAL_DEMO_FORWARD_NOT_PERFORMED`** |

---

## 4. Key Engineering & Operational Observations

1. **Circuit Breaker Responsiveness:**
   - In Session 4, three consecutive losses were processed. The risk engine automatically scaled risk from **0.25% $\to$ 0.1875% $\to$ 0.125%**, engaged `consecutive_loss_paused=True`, and rejected subsequent entries for the rest of the day.
2. **Stop Loss Integrity:**
   - All auto-BE transitions succeeded cleanly. All simulated attempts to widen stop losses were rejected (`SL WIDENING REJECTED`).
3. **Execution Safety Invariant:**
   - `config.AUTOTRADE_ENABLED` remained `false`. Zero live orders were sent.

## 5. Next Required Step for Full Phase 6 Closure
Execute 3–5 real-time market sessions on a live demo MT5 account strictly in **signals-only mode**:
- Stream live MT5 tick feed (actual Bid/Ask and floating spread).
- Record live signal timestamps, structural SL/TP, and pre-trade gate rejections.
- Maintain `AUTOTRADE_ENABLED=false` (zero orders sent).
- Verify connection watchdog and heartbeat recovery over 3–5 full trading days.
