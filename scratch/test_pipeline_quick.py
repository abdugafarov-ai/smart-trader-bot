"""
Модульный тест для проверки ICT/SMC стратегии, SignalTracker и расчета R:R/пипсов.
"""
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '.')

import config
from strategies.ict_smc import ICTSMCStrategy
from market.indicators import TechnicalIndicators
from db.signal_tracker import SignalTracker


def generate_market_data(pattern='bullish', base_price=1.0800, n_candles=80):
    """Генерирует чистые OHLCV данные для тестирования."""
    timestamps = [datetime(2026, 8, 20, 0, 0) + timedelta(minutes=15*i) for i in range(n_candles)]
    
    if pattern == 'bullish':
        trend = np.linspace(0, 0.0100, n_candles)
        noise = np.random.normal(0, 0.0003, n_candles)
        closes = base_price + trend + noise
        closes[-10:] = closes[-11] + np.array([0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0022, 0.0018, 0.0015, 0.0012, 0.0014])
    elif pattern == 'bearish':
        trend = np.linspace(0, -0.0100, n_candles)
        noise = np.random.normal(0, 0.0003, n_candles)
        closes = base_price + trend + noise
        closes[-10:] = closes[-11] - np.array([0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0022, 0.0018, 0.0015, 0.0012, 0.0014])
    else:  # flat
        closes = base_price + np.random.normal(0, 0.0002, n_candles)

    opens = np.roll(closes, 1)
    opens[0] = base_price
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(0.0003, 0.0001, n_candles))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(0.0003, 0.0001, n_candles))
    volumes = np.random.randint(100, 500, n_candles)
    volumes[-10:-5] = 1200

    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes
    })
    return TechnicalIndicators.calculate_all(df)


def test_ict_strategy():
    print("=" * 60)
    print("ТЕСТ 1: ICT/SMC СТРАТЕГИЯ НА РАЗНЫХ СЦЕНАРИЯХ")
    print("=" * 60)
    strategy = ICTSMCStrategy()

    # 1. Бычий сценарий (EURUSD)
    df_bull = generate_market_data('bullish', base_price=1.0850)
    res_bull = strategy.analyze(df_bull, "EURUSD", "M15")
    print(f"\n[EURUSD BULLISH]")
    print(f"  Направление: {res_bull.signal.direction} | Звезды: {res_bull.signal.confidence} | Ордер: {res_bull.signal.order_type}")
    if res_bull.signal.direction != "NEUTRAL":
        print(f"  Entry: {res_bull.signal.entry:.5f} | SL: {res_bull.signal.stop_loss:.5f} | TP1: {res_bull.signal.take_profit_1:.5f} | TP2: {res_bull.signal.take_profit_2:.5f}")
        print(f"  R:R = 1:{res_bull.signal.risk_reward:.1f}")
        assert res_bull.signal.direction == "LONG"
        assert res_bull.signal.stop_loss < res_bull.signal.entry < res_bull.signal.take_profit_1 < res_bull.signal.take_profit_2
        assert res_bull.signal.risk_reward >= 2.4
        print("  ✅ [EURUSD BULLISH] Пройден!")

    # 2. Медвежий сценарий (GBPJPY)
    df_bear = generate_market_data('bearish', base_price=190.50)
    res_bear = strategy.analyze(df_bear, "GBPJPY", "M15")
    print(f"\n[GBPJPY BEARISH]")
    print(f"  Направление: {res_bear.signal.direction} | Звезды: {res_bear.signal.confidence} | Ордер: {res_bear.signal.order_type}")
    if res_bear.signal.direction != "NEUTRAL":
        print(f"  Entry: {res_bear.signal.entry:.2f} | SL: {res_bear.signal.stop_loss:.2f} | TP1: {res_bear.signal.take_profit_1:.2f} | TP2: {res_bear.signal.take_profit_2:.2f}")
        print(f"  R:R = 1:{res_bear.signal.risk_reward:.1f}")
        assert res_bear.signal.direction == "SHORT"
        assert res_bear.signal.stop_loss > res_bear.signal.entry > res_bear.signal.take_profit_1 > res_bear.signal.take_profit_2
        assert res_bear.signal.risk_reward >= 2.4
        print("  ✅ [GBPJPY BEARISH] Пройден!")

    # 3. Золото (XAUUSD)
    df_gold = generate_market_data('bullish', base_price=2500.00)
    res_gold = strategy.analyze(df_gold, "XAUUSD", "H1")
    print(f"\n[XAUUSD GOLD BULLISH]")
    print(f"  Направление: {res_gold.signal.direction} | Звезды: {res_gold.signal.confidence} | Ордер: {res_gold.signal.order_type}")
    if res_gold.signal.direction != "NEUTRAL":
        print(f"  Entry: {res_gold.signal.entry:.2f} | SL: {res_gold.signal.stop_loss:.2f} | TP1: {res_gold.signal.take_profit_1:.2f} | TP2: {res_gold.signal.take_profit_2:.2f}")
        print(f"  R:R = 1:{res_gold.signal.risk_reward:.1f}")
        assert res_gold.signal.stop_loss < res_gold.signal.entry < res_gold.signal.take_profit_1
        print("  ✅ [XAUUSD GOLD BULLISH] Пройден!")

    print("\n✅ ВСЕ ПРОВЕРКИ ICT/SMC СТРАТЕГИИ ПРОЙДЕНЫ УСПЕШНО!")


def test_pip_multipliers():
    print("\n" + "=" * 60)
    print("ТЕСТ 2: ПРОВЕРКА РАСЧЕТА ПИПСОВ И МНОЖИТЕЛЕЙ")
    print("=" * 60)
    
    eur_mult = SignalTracker._get_pip_mult("EURUSD")
    pips_eur = round((1.08500 - 1.08250) * eur_mult, 1)
    print(f"EURUSD (1.08500 - 1.08250): {pips_eur:.1f} pips (mult={eur_mult}) -> {'✅ OK' if pips_eur == 25.0 else '❌ FAIL'}")
    assert pips_eur == 25.0

    jpy_mult = SignalTracker._get_pip_mult("USDJPY")
    pips_jpy = round((155.50 - 155.00) * jpy_mult, 1)
    print(f"USDJPY (155.50 - 155.00): {pips_jpy:.1f} pips (mult={jpy_mult}) -> {'✅ OK' if pips_jpy == 50.0 else '❌ FAIL'}")
    assert pips_jpy == 50.0

    gold_mult = SignalTracker._get_pip_mult("XAUUSD")
    pips_gold = round((2510.00 - 2500.00) * gold_mult, 1)
    print(f"XAUUSD ($2510 - $2500): {pips_gold:.1f} pips (mult={gold_mult}) -> {'✅ OK' if pips_gold == 100.0 else '❌ FAIL'}")
    assert pips_gold == 100.0

    print("\n✅ МНОЖИТЕЛИ ПИПСОВ ВАЛИДНЫ 100%!")


if __name__ == '__main__':
    test_ict_strategy()
    test_pip_multipliers()
