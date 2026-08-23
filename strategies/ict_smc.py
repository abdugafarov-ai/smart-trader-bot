import pandas as pd
import numpy as np
from typing import Tuple, List

from .base import BaseStrategy, StrategySignal, StrategyResult

class ICTSMCStrategy(BaseStrategy):
    name = 'ICT / Smart Money Concepts'
    short_name = 'ict_smc'
    emoji = '🧠'

    def _format_price(self, price: float, symbol: str) -> str:
        if 'XAU' in symbol or 'BTC' in symbol:
            return f"{price:.2f}"
        return f"{price:.5f}"

    def _find_swing_points(self, df: pd.DataFrame, lookback: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        swing_highs = []
        swing_lows = []
        for i in range(lookback, len(df) - lookback):
            window = df.iloc[i-lookback:i+lookback+1]
            if df['high'].iloc[i] == window['high'].max():
                swing_highs.append((i, df['high'].iloc[i]))
            if df['low'].iloc[i] == window['low'].min():
                swing_lows.append((i, df['low'].iloc[i]))
        return swing_highs, swing_lows

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        if len(df) < 50:
            return self._make_result(StrategySignal("NEUTRAL", 0, details=["Недостаточно данных"]), ["Недостаточно данных для анализа"])

        # Вычисление ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]
        
        current_price = df['close'].iloc[-1]
        swing_highs, swing_lows = self._find_swing_points(df, lookback=5)
        
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return self._make_result(StrategySignal("NEUTRAL", 0, details=["Мало экстремумов"]), ["Недостаточно данных (Swing Points)"])
            
        last_highs = swing_highs[-4:]
        last_lows = swing_lows[-4:]
        
        is_bullish_structure = (len(last_highs) >= 2 and last_highs[-1][1] > last_highs[-2][1]) and (len(last_lows) >= 2 and last_lows[-1][1] > last_lows[-2][1])
        is_bearish_structure = (len(last_highs) >= 2 and last_highs[-1][1] < last_highs[-2][1]) and (len(last_lows) >= 2 and last_lows[-1][1] < last_lows[-2][1])
        
        direction = "NEUTRAL"
        confidence = 0
        details = []
        
        bos_found = False
        choch_found = False
        
        # BOS
        if is_bullish_structure and current_price > last_highs[-1][1]:
            bos_found = True
            direction = "LONG"
            confidence += 1
            details.append(f"Структура рынка: бычий BOS (пробой предыдущего максимума {self._format_price(last_highs[-1][1], symbol)})")
        elif is_bearish_structure and current_price < last_lows[-1][1]:
            bos_found = True
            direction = "SHORT"
            confidence += 1
            details.append(f"Структура рынка: медвежий BOS (пробой предыдущего минимума {self._format_price(last_lows[-1][1], symbol)})")
        
        # CHoCH
        if is_bearish_structure and current_price > last_highs[-1][1]:
            choch_found = True
            direction = "LONG"
            confidence += 1
            details.append(f"Структура рынка: бычий CHoCH (смена тренда вверх, пробой {self._format_price(last_highs[-1][1], symbol)})")
        elif is_bullish_structure and current_price < last_lows[-1][1]:
            choch_found = True
            direction = "SHORT"
            confidence += 1
            details.append(f"Структура рынка: медвежий CHoCH (смена тренда вниз, пробой {self._format_price(last_lows[-1][1], symbol)})")
            
        if not bos_found and not choch_found:
            if is_bullish_structure:
                direction = "LONG"
                details.append("Структура рынка: восходящий тренд (HH, HL)")
            elif is_bearish_structure:
                direction = "SHORT"
                details.append("Структура рынка: нисходящий тренд (LL, LH)")
            
        # Order Blocks (OB)
        ob_zone = None
        for i in range(len(df)-20, len(df)-2):
            is_bullish_candle = df['close'].iloc[i+1] > df['open'].iloc[i+1]
            is_bearish_candle = df['close'].iloc[i+1] < df['open'].iloc[i+1]
            
            impulse = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])
            
            if impulse > 1.5 * atr:
                if df['close'].iloc[i] < df['open'].iloc[i] and is_bullish_candle: # Bearish candle before bullish impulse
                    ob_high, ob_low = df['high'].iloc[i], df['low'].iloc[i]
                    if direction == "LONG" and abs(current_price - ob_high) < atr * 2:
                        ob_zone = (ob_high, ob_low)
                        confidence += 1
                        details.append(f"Бычий Order Block найден на уровне {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                        break
                elif df['close'].iloc[i] > df['open'].iloc[i] and is_bearish_candle: # Bullish candle before bearish impulse
                    ob_high, ob_low = df['high'].iloc[i], df['low'].iloc[i]
                    if direction == "SHORT" and abs(current_price - ob_low) < atr * 2:
                        ob_zone = (ob_high, ob_low)
                        confidence += 1
                        details.append(f"Медвежий Order Block найден на уровне {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                        break
        
        # FVG
        fvg_zone = None
        for i in range(len(df)-10, len(df)-1):
            if df['close'].iloc[i-1] > df['open'].iloc[i-1]: # Bullish impulse
                if df['low'].iloc[i] > df['high'].iloc[i-2]:
                    fvg_low, fvg_high = df['high'].iloc[i-2], df['low'].iloc[i]
                    if direction == "LONG":
                        confidence += 1
                        fvg_zone = (fvg_low, fvg_high)
                        details.append(f"Бычий FVG (имбаланс) между {self._format_price(fvg_low, symbol)} и {self._format_price(fvg_high, symbol)}")
                        break
            elif df['close'].iloc[i-1] < df['open'].iloc[i-1]: # Bearish impulse
                if df['high'].iloc[i] < df['low'].iloc[i-2]:
                    fvg_low, fvg_high = df['high'].iloc[i], df['low'].iloc[i-2]
                    if direction == "SHORT":
                        confidence += 1
                        fvg_zone = (fvg_low, fvg_high)
                        details.append(f"Медвежий FVG (имбаланс) между {self._format_price(fvg_low, symbol)} и {self._format_price(fvg_high, symbol)}")
                        break

        # OTE
        ote_zone = None
        if direction == "LONG" and len(last_lows) > 0 and len(last_highs) > 0:
            swing_l = last_lows[-1][1]
            swing_h = last_highs[-1][1]
            if swing_h > swing_l:
                diff = swing_h - swing_l
                fib_05 = swing_h - 0.5 * diff
                fib_0618 = swing_h - 0.618 * diff
                fib_0786 = swing_h - 0.786 * diff
                if fib_0786 <= current_price <= fib_0618:
                    confidence += 1
                    ote_zone = (fib_0618, fib_0786)
                    details.append(f"Цена находится в OTE зоне Фибоначчи (0.618-0.786)")
                details.append(f"Уровни Фибо: 0.5 = {self._format_price(fib_05, symbol)} | 0.618 = {self._format_price(fib_0618, symbol)} | 0.786 = {self._format_price(fib_0786, symbol)}")
        elif direction == "SHORT" and len(last_lows) > 0 and len(last_highs) > 0:
            swing_l = last_lows[-1][1]
            swing_h = last_highs[-1][1]
            if swing_h > swing_l:
                diff = swing_h - swing_l
                fib_05 = swing_l + 0.5 * diff
                fib_0618 = swing_l + 0.618 * diff
                fib_0786 = swing_l + 0.786 * diff
                if fib_0618 <= current_price <= fib_0786:
                    confidence += 1
                    ote_zone = (fib_0618, fib_0786)
                    details.append(f"Цена находится в OTE зоне Фибоначчи (0.618-0.786)")
                details.append(f"Уровни Фибо: 0.5 = {self._format_price(fib_05, symbol)} | 0.618 = {self._format_price(fib_0618, symbol)} | 0.786 = {self._format_price(fib_0786, symbol)}")
                
        # Liquidity
        target = None
        for h1, h2 in zip(last_highs[:-1], last_highs[1:]):
            if abs(h1[1] - h2[1]) / h1[1] < 0.001:
                details.append(f"Зона ликвидности (equal highs) на уровне {self._format_price(h1[1], symbol)}")
                if direction == "LONG":
                    target = h1[1]
                break
        for l1, l2 in zip(last_lows[:-1], last_lows[1:]):
            if abs(l1[1] - l2[1]) / l1[1] < 0.001:
                details.append(f"Зона ликвидности (equal lows) на уровне {self._format_price(l1[1], symbol)}")
                if direction == "SHORT":
                    target = l1[1]
                break

        if direction == "NEUTRAL":
            return self._make_result(StrategySignal("NEUTRAL", 0, details=details), details)

        confidence = min(confidence, 5)
        
        entry, sl, tp1, tp2 = self._calc_entry_sl_tp(df, direction, atr)
        if direction == "LONG":
            if ob_zone: 
                entry = ob_zone[0]
                sl = ob_zone[1] - 0.5 * atr
            elif ote_zone: 
                entry = ote_zone[0]
                sl = ote_zone[1] - 0.5 * atr
            if target: tp1 = target
        elif direction == "SHORT":
            if ob_zone:
                entry = ob_zone[1]
                sl = ob_zone[0] + 0.5 * atr
            elif ote_zone:
                entry = ote_zone[0]
                sl = ote_zone[1] + 0.5 * atr
            if target: tp1 = target
        
        rr = abs(tp1 - entry) / abs(entry - sl) if sl != entry else 0
        details.append(f"📍 Вход: {self._format_price(entry, symbol)} | 🛑 Стоп: {self._format_price(sl, symbol)} | 🎯 Цель: {self._format_price(tp1, symbol)} | R:R = 1:{rr:.2f}")

        signal = StrategySignal(direction=direction, confidence=confidence, entry=entry, stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2, risk_reward=rr, details=details)
        return self._make_result(signal, details)
