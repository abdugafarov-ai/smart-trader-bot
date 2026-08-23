import pandas as pd
import numpy as np
from .base import BaseStrategy, StrategySignal, StrategyResult

class BreakoutRetestStrategy(BaseStrategy):
    name = "Breakout & Retest"
    short_name = "breakout_retest"
    emoji = "🚀"

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        df = df.copy()
        if "atr" not in df.columns:
            df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
            df['atr'] = df['tr'].rolling(14).mean()
            
        atr_value = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else df['close'].iloc[-1] * 0.01
        
        # Вычисление Swing High / Low для уровней
        df['swing_high'] = df['high'] == df['high'].rolling(window=5, center=True).max()
        df['swing_low'] = df['low'] == df['low'].rolling(window=5, center=True).min()
        
        direction = "NEUTRAL"
        confidence = 0
        details = []
        
        # Ищем пробой ближайших уровней и ретест
        recent_highs = df[df['swing_high']]['high'].tail(3).values
        recent_lows = df[df['swing_low']]['low'].tail(3).values
        
        current_close = df['close'].iloc[-1]
        current_low = df['low'].iloc[-1]
        current_high = df['high'].iloc[-1]
        
        # Проверка ретеста
        # Цена пробила уровень несколько свечей назад и сейчас вернулась к нему
        is_bullish_retest = False
        is_bearish_retest = False
        
        for high_lvl in recent_highs:
            # Пробили вверх, вернулись к уровню
            if df['close'].iloc[-5:-1].max() > high_lvl and current_low <= high_lvl * 1.002 and current_close >= high_lvl * 0.998:
                is_bullish_retest = True
                details.append(f"📈 Замечен успешный ретест пробитого сопротивления: {high_lvl:.2f}")
                break
                
        for low_lvl in recent_lows:
            # Пробили вниз, вернулись к уровню
            if df['close'].iloc[-5:-1].min() < low_lvl and current_high >= low_lvl * 0.998 and current_close <= low_lvl * 1.002:
                is_bearish_retest = True
                details.append(f"📉 Замечен успешный ретест пробитой поддержки: {low_lvl:.2f}")
                break
        
        if is_bullish_retest:
            direction = "LONG"
            confidence = 4
            details.append("✅ Качественный паттерн Breakout & Retest. Отличное подтверждение уровня.")
        elif is_bearish_retest:
            direction = "SHORT"
            confidence = 4
            details.append("✅ Качественный паттерн Breakdown & Retest. Отличное подтверждение уровня.")
            
        if direction != "NEUTRAL":
            entry, sl, tp1, tp2 = self._calc_entry_sl_tp(df, direction, atr_value)
            rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0.0
            details.append(f"📍 Вход: {entry:.5f} | 🛑 Стоп: {sl:.5f} | 🎯 Цель: {tp1:.5f} | R:R = 1:{rr:.1f}")
            
            signal = StrategySignal(
                direction=direction, confidence=confidence,
                entry=entry, stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=rr, details=details
            )
        else:
            signal = StrategySignal(direction="NEUTRAL", confidence=0, details=["Ожидание пробоя или ретеста ключевых уровней."])

        return self._make_result(signal, details)
