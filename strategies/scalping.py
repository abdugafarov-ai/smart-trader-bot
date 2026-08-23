import pandas as pd
import numpy as np
from .base import BaseStrategy, StrategySignal, StrategyResult

class ScalpingStrategy(BaseStrategy):
    name = "Scalping (EMA & RSI)"
    short_name = "scalping"
    emoji = "⚡"

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        df = df.copy()
        if "atr" not in df.columns:
            df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
            df['atr'] = df['tr'].rolling(14).mean()
            
        atr_value = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else df['close'].iloc[-1] * 0.01
        
        # Расчет индикаторов если их нет
        if 'ema_9' not in df.columns:
            df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        if 'ema_21' not in df.columns:
            df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
            
        if 'rsi' not in df.columns:
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
        direction = "NEUTRAL"
        confidence = 0
        details = []
        confluences = 0
        
        ema9 = df['ema_9'].iloc[-1]
        ema21 = df['ema_21'].iloc[-1]
        prev_ema9 = df['ema_9'].iloc[-2]
        prev_ema21 = df['ema_21'].iloc[-2]
        
        rsi = df['rsi'].iloc[-1]
        
        # EMA Crossover
        if prev_ema9 <= prev_ema21 and ema9 > ema21:
            details.append("📈 Золотое пересечение: EMA 9 пересекла EMA 21 снизу вверх")
            confluences += 1
            direction = "LONG"
        elif prev_ema9 >= prev_ema21 and ema9 < ema21:
            details.append("📉 Смертельное пересечение: EMA 9 пересекла EMA 21 сверху вниз")
            confluences += 1
            direction = "SHORT"
            
        # Условия по RSI (перепроданность/перекупленность как доп фильтр)
        if direction == "LONG" and rsi < 40:
            details.append("💡 RSI благоприятный (не перекуплен)")
            confluences += 1
        elif direction == "SHORT" and rsi > 60:
            details.append("💡 RSI благоприятный (не перепродан)")
            confluences += 1
            
        if confluences >= 2:
            confidence = 3
        else:
            direction = "NEUTRAL"
            
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
            signal = StrategySignal(direction="NEUTRAL", confidence=0, details=["Недостаточно слияний для входа (нужно минимум 2)."])

        return self._make_result(signal, details)
