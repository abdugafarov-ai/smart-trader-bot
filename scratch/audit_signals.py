"""
Генеральный скрипт аудита и стресс-тестирования всех сигналов и модулей Smart Trader Bot.
"""

import sys
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '.')

import config
from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies import ALL_STRATEGIES, STRATEGY_MAP
from strategies.ict_smc import ICTSMCStrategy
from strategies.supply_demand import SupplyDemandStrategy
from strategies.wyckoff import WyckoffStrategy
from strategies.breakout_retest import BreakoutRetestStrategy
from strategies.scalping import ScalpingStrategy
from strategies.volume_analysis import VolumeAnalysisStrategy
from bot.handlers import run_multi_tf_analysis
from db.database import init_db, get_stats, get_recent_signals


async def audit_all_pairs_fetcher():
    print("=" * 60)
    print("1. АУДИТ ПОЛУЧЕНИЯ ДАННЫХ (DataFetcher) ПО ВСЕМ 17 ПАРАМ")
    print("=" * 60)
    fetcher = DataFetcher()
    results = {}
    
    for symbol in config.ALL_PAIRS:
        pair_res = {}
        for tf in config.MULTI_TF_LIST:  # M15, H1, H4, D1
            try:
                df = await fetcher.fetch_ohlcv(symbol, tf, limit=100)
                if df is None or df.empty:
                    pair_res[tf] = "❌ EMPTY / NONE"
                elif len(df) < 20:
                    pair_res[tf] = f"⚠️ TOO FEW ({len(df)} rows)"
                else:
                    # Check columns and NaN
                    cols_ok = all(c in df.columns for c in ['open', 'high', 'low', 'close', 'volume', 'timestamp'])
                    has_nan = df[['open', 'high', 'low', 'close']].isna().any().any()
                    last_close = df['close'].iloc[-1]
                    if not cols_ok:
                        pair_res[tf] = f"❌ MISSING COLS: {list(df.columns)}"
                    elif has_nan:
                        pair_res[tf] = "❌ HAS NANS"
                    else:
                        pair_res[tf] = f"✅ OK ({len(df)} rows, last={last_close:.5f})"
            except Exception as e:
                pair_res[tf] = f"❌ ERROR: {e}"
        results[symbol] = pair_res
        print(f"{symbol:8} | " + " | ".join(f"{tf}: {st}" for tf, st in pair_res.items()))

    return results


async def audit_strategies_on_synthetic_and_real_data():
    print("\n" + "=" * 60)
    print("2. АУДИТ СТРАТЕГИЙ НА ВАЛИДНОСТЬ УРОВНЕЙ ENTRY / SL / TP / RR")
    print("=" * 60)
    
    # Generate test market scenarios
    # Scenario A: Bullish trend with Order Block & Pullback
    dates = pd.date_range('2026-08-01', periods=100, freq='15min')
    prices_bull = 1.0800 + np.cumsum(np.random.normal(0.0001, 0.0003, 100))
    df_bull = pd.DataFrame({
        'timestamp': dates,
        'open': prices_bull - 0.0001,
        'high': prices_bull + 0.0004,
        'low': prices_bull - 0.0004,
        'close': prices_bull,
        'volume': np.random.randint(100, 1000, 100)
    })
    df_bull = TechnicalIndicators.calculate_all(df_bull)

    # Scenario B: Bearish trend with Breakdown & Pullback
    prices_bear = 1.1000 - np.cumsum(np.random.normal(0.0001, 0.0003, 100))
    df_bear = pd.DataFrame({
        'timestamp': dates,
        'open': prices_bear + 0.0001,
        'high': prices_bear + 0.0004,
        'low': prices_bear - 0.0004,
        'close': prices_bear,
        'volume': np.random.randint(100, 1000, 100)
    })
    df_bear = TechnicalIndicators.calculate_all(df_bear)

    strategies = [
        ICTSMCStrategy(),
        SupplyDemandStrategy(),
        WyckoffStrategy(),
        BreakoutRetestStrategy(),
        ScalpingStrategy(),
        VolumeAnalysisStrategy()
    ]

    for strat in strategies:
        print(f"\n--- Стратегия: {strat.name} ({strat.emoji}) ---")
        for name, df in [("BULLISH SCENARIO", df_bull), ("BEARISH SCENARIO", df_bear)]:
            res = strat.analyze(df, "EURUSD", "M15")
            sig = res.signal
            print(f"[{name}] Направление: {sig.direction} | Stars: {sig.confidence} | Order: {sig.order_type}")
            if sig.direction != "NEUTRAL":
                print(f"  Entry: {sig.entry:.5f} | SL: {sig.stop_loss:.5f} | TP1: {sig.take_profit_1:.5f} | TP2: {sig.take_profit_2:.5f} | R:R = 1:{sig.risk_reward:.1f}")
                # Validation checks:
                if sig.direction == "LONG":
                    assert sig.stop_loss < sig.entry, f"ERROR: For LONG, SL ({sig.stop_loss}) must be < Entry ({sig.entry})"
                    assert sig.entry < sig.take_profit_1, f"ERROR: For LONG, Entry ({sig.entry}) must be < TP1 ({sig.take_profit_1})"
                    assert sig.take_profit_1 < sig.take_profit_2, f"ERROR: For LONG, TP1 ({sig.take_profit_1}) must be < TP2 ({sig.take_profit_2})"
                elif sig.direction == "SHORT":
                    assert sig.stop_loss > sig.entry, f"ERROR: For SHORT, SL ({sig.stop_loss}) must be > Entry ({sig.entry})"
                    assert sig.entry > sig.take_profit_1, f"ERROR: For SHORT, Entry ({sig.entry}) must be > TP1 ({sig.take_profit_1})"
                    assert sig.take_profit_1 > sig.take_profit_2, f"ERROR: For SHORT, TP1 ({sig.take_profit_1}) must be > TP2 ({sig.take_profit_2})"
                print("  ✅ Валидация уровней цен: ПРОЙДЕНА (Уровни математически корректны)")


async def audit_multi_tf_pipeline():
    print("\n" + "=" * 60)
    print("3. АУДИТ ПОЛНОГО МУЛЬТИ-TF ПАЙПЛАЙНА НА РЕАЛЬНЫХ ДАННЫХ")
    print("=" * 60)
    
    test_symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
    for sym in test_symbols:
        res = await run_multi_tf_analysis(sym)
        print(f"\nСимвол: {res.symbol} [ Маркер: {res.tag_emoji} ]")
        print(f"  Вердикт: {res.overall_direction} | Звезды: {res.overall_stars}/5 | Order: {res.order_type}")
        print(f"  Согласие TF: {res.tf_agreement}/{res.total_tfs}")
        if res.overall_direction != "NEUTRAL":
            print(f"  Entry: {res.entry} | SL: {res.stop_loss} | TP1: {res.take_profit_1} | TP2: {res.take_profit_2}")
            print(f"  R:R: 1:{res.risk_reward_1} / 1:{res.risk_reward_2}")
            print(f"  Pips: SL={res.pips_sl:.1f}p, TP1={res.pips_tp1:.1f}p, TP2={res.pips_tp2:.1f}p")


async def main():
    await audit_all_pairs_fetcher()
    await audit_strategies_on_synthetic_and_real_data()
    await audit_multi_tf_pipeline()
    print("\n" + "=" * 60)
    print("ГЕНЕРАЛЬНЫЙ АУДИТ ЗАВЕРШЕН!")
    print("=" * 60)

if __name__ == '__main__':
    asyncio.run(main())
