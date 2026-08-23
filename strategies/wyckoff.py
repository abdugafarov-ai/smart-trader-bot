import pandas as pd
import numpy as np

from .base import BaseStrategy, StrategySignal, StrategyResult

class WyckoffStrategy(BaseStrategy):
    name = 'Wyckoff'
    short_name = 'wyckoff'
    emoji = '📈'

    def _format_price(self, price: float, symbol: str) -> str:
        if 'XAU' in symbol or 'BTC' in symbol:
            return f"{price:.2f}"
        return f"{price:.5f}"

    def _detect_phase(self, df: pd.DataFrame, range_high: float, range_low: float, atr: float, vol_sma: float) -> str:
        current = df.iloc[-1]
        early_avg = df['close'].iloc[:len(df)//5].mean()
        late_avg = df['close'].iloc[-len(df)//5:].mean()
        
        prior_trend = 'up' if late_avg > early_avg else 'down'
        std_of_closes = df['close'].tail(20).std()
        
        is_in_range = (std_of_closes < atr)
        current_vol = current['volume']
        
        if prior_trend == 'down' and is_in_range:
            return 'ACCUMULATION'
        elif prior_trend == 'up' and is_in_range:
            return 'DISTRIBUTION'
        elif late_avg > early_avg and not is_in_range:
            return 'MARKUP'
        else:
            return 'MARKDOWN'

    def _effort_vs_result(self, df: pd.DataFrame) -> str:
        last_5 = df.tail(5)
        avg_body = abs(last_5['close'] - last_5['open']).mean()
        avg_vol = last_5['volume'].mean()
        vol_sma_20 = df['volume'].rolling(20).mean().iloc[-1]
        body_sma_20 = abs(df['close'] - df['open']).rolling(20).mean().iloc[-1]
        
        if pd.isna(vol_sma_20) or pd.isna(body_sma_20) or body_sma_20 == 0:
            return 'NEUTRAL'
            
        high_effort = avg_vol > 1.5 * vol_sma_20
        small_result = avg_body < 0.5 * body_sma_20
        big_result = avg_body > 1.5 * body_sma_20
        
        if high_effort and small_result:
            return 'STOPPING'
        elif high_effort and big_result:
            return 'STRONG_MOVE'
        elif not high_effort and big_result:
            return 'EASY_MOVE'
        else:
            return 'NEUTRAL'

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        if len(df) < 50:
            return self._make_result(StrategySignal("NEUTRAL", 0, details=["Недостаточно данных"]), ["Недостаточно данных для анализа"])

        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]
        vol_sma = df['volume'].rolling(20).mean().iloc[-1]
        
        last_50 = df.tail(50)
        range_high = last_50['high'].max()
        range_low = last_50['low'].min()
        
        phase = self._detect_phase(df, range_high, range_low, atr, vol_sma)
        effort = self._effort_vs_result(df)
        
        direction = "NEUTRAL"
        confidence = 0
        details = []
        
        phase_ru = {
            'ACCUMULATION': 'НАКОПЛЕНИЕ (Accumulation)',
            'DISTRIBUTION': 'РАСПРЕДЕЛЕНИЕ (Distribution)',
            'MARKUP': 'НОВЫЙ ВОСХОДЯЩИЙ ТРЕНД (Markup)',
            'MARKDOWN': 'НОВЫЙ НИСХОДЯЩИЙ ТРЕНД (Markdown)'
        }
        details.append(f"Фаза Вайкоффа: {phase_ru.get(phase, phase)}")
        details.append(f"Торговый диапазон: {self._format_price(range_low, symbol)} — {self._format_price(range_high, symbol)}")
        
        # Spring Detection
        spring_detected = False
        last_3 = df.tail(3)
        if phase == 'ACCUMULATION':
            for i in range(len(last_3)):
                if last_3['low'].iloc[i] < range_low and last_3['close'].iloc[i] > range_low:
                    if last_3['volume'].iloc[i] < vol_sma:
                        spring_detected = True
                        break
        
        if spring_detected:
            direction = "LONG"
            confidence += 3
            details.append(f"Обнаружен Spring! Цена опустилась ниже поддержки {self._format_price(range_low, symbol)} и вернулась выше")

        # UTAD Detection
        utad_detected = False
        if phase == 'DISTRIBUTION':
            for i in range(len(last_3)):
                if last_3['high'].iloc[i] > range_high and last_3['close'].iloc[i] < range_high:
                    if last_3['volume'].iloc[i] < vol_sma:
                        utad_detected = True
                        break

        if utad_detected:
            direction = "SHORT"
            confidence += 3
            details.append(f"Обнаружен UTAD! Цена пробила сопротивление {self._format_price(range_high, symbol)} и вернулась ниже")
            
        # SOS/SOW
        if phase == 'MARKUP' and last_3['close'].iloc[-1] > range_high and last_3['volume'].iloc[-1] > vol_sma * 1.5:
            direction = "LONG"
            confidence += 2
            details.append("Sign of Strength (SOS): Цена пробила диапазон вверх на высоком объёме")
            
        if phase == 'MARKDOWN' and last_3['close'].iloc[-1] < range_low and last_3['volume'].iloc[-1] > vol_sma * 1.5:
            direction = "SHORT"
            confidence += 2
            details.append("Sign of Weakness (SOW): Цена пробила диапазон вниз на высоком объёме")
            
        effort_ru = {
            'STOPPING': 'STOPPING — высокий объём при малом движении (разворот)',
            'STRONG_MOVE': 'STRONG_MOVE — высокий объём при большом движении (продолжение)',
            'EASY_MOVE': 'EASY_MOVE — малое сопротивление (продолжение)',
            'NEUTRAL': 'НЕЙТРАЛЬНО'
        }
        details.append(f"Усилие/Результат: {effort_ru.get(effort, effort)}")
        
        if (direction == "LONG" and effort in ['STOPPING', 'STRONG_MOVE', 'EASY_MOVE']) or \
           (direction == "SHORT" and effort in ['STOPPING', 'STRONG_MOVE', 'EASY_MOVE']):
            confidence += 1
            
        if direction == "NEUTRAL":
            return self._make_result(StrategySignal("NEUTRAL", 0, details=details), details)
            
        confidence = min(confidence, 5)
        
        entry, sl, tp1, tp2 = self._calc_entry_sl_tp(df, direction, atr)
        if direction == "LONG":
            if spring_detected:
                entry = df['close'].iloc[-1]
                sl = range_low - 0.5 * atr
                tp1 = range_high
        elif direction == "SHORT":
            if utad_detected:
                entry = df['close'].iloc[-1]
                sl = range_high + 0.5 * atr
                tp1 = range_low
                
        rr = abs(tp1 - entry) / abs(entry - sl) if sl != entry else 0
        details.append(f"📍 Вход: {self._format_price(entry, symbol)} | 🛑 Стоп: {self._format_price(sl, symbol)} | 🎯 Цель: {self._format_price(tp1, symbol)} | R:R = 1:{rr:.2f}")

        signal = StrategySignal(direction=direction, confidence=confidence, entry=entry, stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2, risk_reward=rr, details=details)
        return self._make_result(signal, details)
