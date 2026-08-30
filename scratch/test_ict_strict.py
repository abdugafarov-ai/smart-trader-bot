import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies.ict_smc import ICTSMCStrategy

async def test_ict():
    print("=== TESTING ICT/SMC STRATEGY STRICT NO-FALLBACK LOGIC ===")
    fetcher = DataFetcher()
    strategy = ICTSMCStrategy()
    
    pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD']
    for sym in pairs:
        for tf in ['M15', 'H1', 'H4']:
            df = await fetcher.fetch_ohlcv(sym, tf, limit=100)
            if df is None or df.empty:
                continue
            df = TechnicalIndicators.calculate_all(df)
            res = strategy.analyze(df, sym, tf)
            sig = res.signal
            print(f"{sym} [{tf}]: dir={sig.direction}, stars={sig.confidence}, order={sig.order_type}")
            if sig.direction != "NEUTRAL":
                print(f"   Entry: {sig.entry:.5f}, SL: {sig.stop_loss:.5f}, TP1: {sig.take_profit_1:.5f}, RR: {sig.risk_reward}")
                print(f"   Details: {sig.details}")
            else:
                reason = sig.details[0] if sig.details else "Neutral"
                print(f"   Neutral reason: {reason}")

asyncio.run(test_ict())
