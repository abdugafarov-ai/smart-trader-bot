# Legacy MT4 Bridge Quarantine Notice

## Deprecation & Quarantine Rationale

As part of the **Smart Trader Bot Master Upgrade & Challenge-Readiness Plan v1.0 (Phase 5: EA Audit & Institutional Refactoring)**, `SmartTraderBridge.mq4` has been permanently quarantined from active deployment paths.

### Critical Safety & Architecture Deficiencies in Legacy MQL4 EA:

1. **Hardcoded Fixed Lot (0.03 Lots):**
   - Line 15 specified `input double InpFixedLot = 0.03;`.
   - Hardcoded lot sizing completely bypasses the dynamic risk engine (`0.25% - 0.50%` per trade), exposing prop firm accounts to catastrophic drawdown on varying SL distances. Under FundingPips $5,000 challenge rules, a $25 gold stop with 0.03 lots would risk $75 (1.5% in a single trade), violating risk parameters.

2. **Omission of Target Asset (XAUUSD):**
   - Line 57 defined `pairs[] = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "EURAUD"}`.
   - Gold (`XAUUSD`) was missing from the symbol whitelist, causing silent execution failure.

3. **Synchronous Polling without Ticket / State Reconciliation:**
   - Used primitive `WebRequest` polling on an unindexed timer without institutional order lifecycle tracking, async execution queues, or fail-safe heartbeat telemetry.

4. **Zero Prop Risk Engine Compliance:**
   - Lacks daily drawdown tracking relative to 00:00 EET equity.
   - Lacks consecutive loss scaling or throttling.
   - Lacks stop-loss widening validation or breakeven / partial close automation.

### Current Production Standard:
All live trading and bridge telemetry must strictly utilize **MetaTrader 5 (`SmartTraderBridge.mq5`)** communicating with `trading/execution_bridge.py` under institutional risk management and prop firm challenge rules.
