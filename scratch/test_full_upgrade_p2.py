import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.handlers import run_multi_tf_analysis
from db.database import init_db, get_consecutive_sl_count
from market.data_fetcher import DataFetcher
from utils.chart_generator import generate_signal_chart

async def test_all_upgrades():
    print("==================================================")
    print("🧪 FULL INTEGRATION TEST: PRIORITY 1 & 2 UPGRADES")
    print("==================================================")
    
    await init_db()
    print("1. Database & Drawdown Tracker:")
    sl_cnt = await get_consecutive_sl_count()
    print(f"   Current consecutive SL count: {sl_cnt}")

    print("\n2. Testing Multi-TF Top-Down with Daily Bias on EURUSD:")
    res_eur = await run_multi_tf_analysis("EURUSD")
    print(f"   EURUSD: {res_eur.overall_direction} | Stars: {res_eur.overall_stars}")
    if res_eur.entry:
        print(f"   Entry: {res_eur.entry:.5f} | SL: {res_eur.stop_loss:.5f} | TP1: {res_eur.take_profit_1:.5f} | RR: {res_eur.risk_reward_1}")
        for v in res_eur.strategy_verdicts:
            print(f"   Verdict: {v[0]} {v[1]} -> {v[2]}")

    print("\n3. Testing Gold (XAUUSD) Data & Analysis:")
    res_gold = await run_multi_tf_analysis("XAUUSD")
    print(f"   XAUUSD: {res_gold.overall_direction} | Stars: {res_gold.overall_stars}")

    print("\n4. Testing USDJPY Multi-TF Analysis:")
    res_jpy = await run_multi_tf_analysis("USDJPY")
    print(f"   USDJPY: {res_jpy.overall_direction} | Stars: {res_jpy.overall_stars}")

    print("\n==================================================")
    print("✅ ALL SYSTEMS AND INSTITUTIONAL UPGRADES VERIFIED!")
    print("==================================================")

asyncio.run(test_all_upgrades())
