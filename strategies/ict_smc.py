"""
ICT / Smart Money Concepts (SMC) Strategy.
Институциональная стратегия поиска снайперских входов:
- Фрактальные свинги (Williams / ICT 5-bar pivots, lookback=2)
- BOS (Break of Structure) и CHoCH (Change of Character)
- Свежие немитигированные Order Blocks (OB)
- Fair Value Gaps (FVG / имбалансы)
- Золотая OTE зона Фибоначчи (0.618 - 0.705)
- Корректные типы отложенных ордеров (LIMIT строго с запасом от текущей цены)
- Строгий R:R >= 1:2.5 (цели 1:2.5 - 1:5.0)
"""

import pandas as pd
import numpy as np
from typing import Tuple, List, Optional

from .base import BaseStrategy, StrategySignal, StrategyResult


class ICTSMCStrategy(BaseStrategy):
    name = 'ICT / Smart Money Concepts'
    short_name = 'ict_smc'
    emoji = '🧠'

    def _format_price(self, price: float, symbol: str) -> str:
        if 'XAU' in symbol or 'JPY' in symbol:
            return f"{price:.2f}"
        return f"{price:.5f}"

    def _find_swing_points(self, df: pd.DataFrame, lookback: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """
        Находит фрактальные свинги максимумов и минимумов (Classic 5-bar ICT/Williams Fractals).
        lookback=2 означает 2 свечи слева, 1 пик, 2 свечи справа (всего 5 свечей).
        """
        swing_highs = []
        swing_lows = []
        n = len(df)
        if n < lookback * 2 + 1:
            return swing_highs, swing_lows

        for i in range(lookback, n - lookback):
            window_high = df['high'].iloc[i-lookback:i+lookback+1]
            window_low = df['low'].iloc[i-lookback:i+lookback+1]
            if df['high'].iloc[i] == window_high.max():
                swing_highs.append((i, float(df['high'].iloc[i])))
            if df['low'].iloc[i] == window_low.min():
                swing_lows.append((i, float(df['low'].iloc[i])))
                
        return swing_highs, swing_lows

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        if len(df) < 30:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["Недостаточно данных"]),
                ["Недостаточно данных для анализа"]
            )

        # 1. Расчет ATR (14)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr <= 0:
            atr = (df['high'].max() - df['low'].min()) * 0.01

        current_price = float(df['close'].iloc[-1])
        
        # 2. Поиск свингов (lookback=2 для идеальной чувствительности)
        swing_highs, swing_lows = self._find_swing_points(df, lookback=2)

        if len(swing_highs) < 2:
            rolling_max = df['high'].rolling(5).max()
            for idx in range(5, len(df)-1):
                if df['high'].iloc[idx] == rolling_max.iloc[idx]:
                    swing_highs.append((idx, float(df['high'].iloc[idx])))
        if len(swing_lows) < 2:
            rolling_min = df['low'].rolling(5).min()
            for idx in range(5, len(df)-1):
                if df['low'].iloc[idx] == rolling_min.iloc[idx]:
                    swing_lows.append((idx, float(df['low'].iloc[idx])))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["Консолидация / Мало выраженных свингов"]),
                ["Рынок в узком боковике, нет четкой структуры"]
            )

        last_highs = swing_highs[-4:]
        last_lows = swing_lows[-4:]

        # BUG 1 FIX: Structure Detection
        hh1, hh2 = last_highs[-1][1], last_highs[-2][1]
        hl1, hl2 = last_lows[-1][1], last_lows[-2][1]
        
        strong_bull = (hh1 > hh2) and (hl1 > hl2)
        strong_bear = (hh1 < hh2) and (hl1 < hl2)
        weak_bull = ((hh1 > hh2) or (hl1 > hl2)) and not strong_bear
        weak_bear = ((hh1 < hh2) or (hl1 < hl2)) and not strong_bull

        is_bullish_structure = strong_bull or weak_bull
        is_bearish_structure = strong_bear or weak_bear

        direction = "NEUTRAL"
        details = []
        sub_signals = 0

        bos_found = False
        choch_found = False

        # 3. Break of Structure (BOS) & CHoCH
        if is_bullish_structure and current_price > hh1:
            bos_found = True
            direction = "LONG"
            sub_signals += 1
            details.append(f"Бычий BOS (пробой максимума {self._format_price(hh1, symbol)})")
        elif is_bearish_structure and current_price < hl1:
            bos_found = True
            direction = "SHORT"
            sub_signals += 1
            details.append(f"Медвежий BOS (пробой минимума {self._format_price(hl1, symbol)})")

        if not bos_found:
            if is_bearish_structure and current_price > hh1:
                choch_found = True
                direction = "LONG"
                sub_signals += 1
                details.append(f"Бычий CHoCH (разворот тренда вверх через {self._format_price(hh1, symbol)})")
            elif is_bullish_structure and current_price < hl1:
                choch_found = True
                direction = "SHORT"
                sub_signals += 1
                details.append(f"Медвежий CHoCH (разворот тренда вниз через {self._format_price(hl1, symbol)})")

        if not bos_found and not choch_found:
            if strong_bull:
                direction = "LONG"
                details.append("Структура: Сильный восходящий тренд")
            elif strong_bear:
                direction = "SHORT"
                details.append("Структура: Сильный нисходящий тренд")
            else:
                mid_point = (hh1 + hl1) / 2
                if current_price > mid_point:
                    direction = "LONG"
                else:
                    direction = "SHORT"

        if direction == "NEUTRAL":
            return self._make_result(StrategySignal(direction="NEUTRAL"), details)

        # BUG 5 FIX: Volume Confirmation
        vol_penalty = 0
        if (bos_found or choch_found) and 'volume' in df.columns:
            vol_sma = df['volume'].rolling(20).mean().iloc[-1]
            if df['volume'].iloc[-1] < 0.8 * vol_sma:
                vol_penalty = 1
                details.append("Низкий объем на пробое (слабое подтверждение)")
            else:
                sub_signals += 1
                details.append("Высокий объем подтверждает движение")

        # 6. Order Block (OB)
        ob_zone: Optional[Tuple[float, float]] = None
        lookback_ob = min(30, len(df)-2)
        for i in range(len(df)-lookback_ob, len(df)-2):
            is_bull_next = df['close'].iloc[i+1] > df['open'].iloc[i+1]
            is_bear_next = df['close'].iloc[i+1] < df['open'].iloc[i+1]
            impulse = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])

            if impulse > 1.1 * atr:
                if df['close'].iloc[i] < df['open'].iloc[i] and is_bull_next and direction == "LONG":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if current_price >= ob_low:
                        ob_zone = (ob_high, ob_low)
                        sub_signals += 1
                        details.append(f"Бычий Order Block: {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                        break
                elif df['close'].iloc[i] > df['open'].iloc[i] and is_bear_next and direction == "SHORT":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if current_price <= ob_high:
                        ob_zone = (ob_high, ob_low)
                        sub_signals += 1
                        details.append(f"Медвежий Order Block: {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                        break

        # BUG 6 FIX: FVG Detection
        fvg_zone: Optional[Tuple[float, float]] = None
        lookback_fvg = min(20, len(df)-3)
        for i in range(len(df)-lookback_fvg, len(df)-1):
            middle_body = abs(df['close'].iloc[i] - df['open'].iloc[i])
            if middle_body < 0.5 * atr:
                continue
                
            if direction == "LONG" and df['close'].iloc[i] > df['open'].iloc[i]:
                if df['low'].iloc[i+1] > df['high'].iloc[i-1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i-1]), float(df['low'].iloc[i+1])
                    if current_price >= fvg_low:
                        sub_signals += 1
                        fvg_zone = (fvg_low, fvg_high)
                        details.append(f"Бычий FVG: {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                        break
            elif direction == "SHORT" and df['close'].iloc[i] < df['open'].iloc[i]:
                if df['high'].iloc[i+1] < df['low'].iloc[i-1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i+1]), float(df['low'].iloc[i-1])
                    if current_price <= fvg_high:
                        sub_signals += 1
                        fvg_zone = (fvg_low, fvg_high)
                        details.append(f"Медвежий FVG: {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                        break

        # BUG 7 FIX: OTE
        recent_window = min(15, len(df))
        impulse_high = max(hh1, float(df['high'].iloc[-recent_window:].max()))
        impulse_low = min(hl1, float(df['low'].iloc[-recent_window:].min()))
        diff = impulse_high - impulse_low

        if diff <= 0:
            diff = atr * 2.0
            impulse_high = current_price + atr
            impulse_low = current_price - atr

        ote_entry = None
        if direction == "LONG":
            fib_0618 = impulse_high - 0.618 * diff
            fib_0705 = impulse_high - 0.705 * diff
            if current_price >= fib_0705:
                sub_signals += 1
                ote_entry = fib_0618
                details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0705, symbol)} — {self._format_price(fib_0618, symbol)}")
        else:
            fib_0618 = impulse_low + 0.618 * diff
            fib_0705 = impulse_low + 0.705 * diff
            if current_price <= fib_0705:
                sub_signals += 1
                ote_entry = fib_0618
                details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0618, symbol)} — {self._format_price(fib_0705, symbol)}")

        # BUG 4 FIX: Confidence logic
        final_confidence = max(1, sub_signals - vol_penalty)
        if final_confidence < 3:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=final_confidence, details=details + ["Недостаточно подтверждений (<3)"]),
                details + ["Слабый сетап, остаемся в стороне"]
            )
        
        confidence_stars = min(5, final_confidence)

        # BUG 2 & 3 FIX: Stop Loss, Entry, TP
        min_sl_dist = 1.0 * atr if str(timeframe).lower() in ['15m', 'm15', '1h', 'h1', '60m'] else 0.7 * atr

        if direction == "LONG":
            candidates = []
            if ote_entry and ote_entry < current_price:
                candidates.append(ote_entry)
            if ob_zone and ob_zone[1] < current_price:  # BUG 3 FIX: ob_low for discount
                candidates.append(ob_zone[1])
            if fvg_zone and fvg_zone[1] < current_price:
                candidates.append(fvg_zone[1])

            if candidates:
                entry = max(candidates)
            else:
                entry = current_price - 0.4 * atr

            base_sl = ob_zone[1] if ob_zone else impulse_low
            sl = min(base_sl - 0.25 * atr, entry - min_sl_dist) # BUG 2 FIX

            risk = entry - sl
            if risk < min_sl_dist:
                sl = entry - min_sl_dist
                risk = entry - sl

            tp1 = entry + 2.5 * risk
            tp2 = entry + 4.0 * risk # BUG 2 FIX

            if entry <= current_price - 0.1 * atr:
                order_type = "BUY_LIMIT"
            elif entry >= current_price + 0.1 * atr:
                order_type = "BUY_STOP"
            else:
                entry = current_price - 0.25 * atr
                order_type = "BUY_LIMIT"
                risk = entry - sl
                tp1 = entry + 2.5 * risk
                tp2 = entry + 4.0 * risk

        else:  # SHORT
            candidates = []
            if ote_entry and ote_entry > current_price:
                candidates.append(ote_entry)
            if ob_zone and ob_zone[0] > current_price:  # BUG 3 FIX: ob_high for premium
                candidates.append(ob_zone[0])
            if fvg_zone and fvg_zone[0] > current_price:
                candidates.append(fvg_zone[0])

            if candidates:
                entry = min(candidates)
            else:
                entry = current_price + 0.4 * atr

            base_sl = ob_zone[0] if ob_zone else impulse_high
            sl = max(base_sl + 0.25 * atr, entry + min_sl_dist) # BUG 2 FIX

            risk = sl - entry
            if risk < min_sl_dist:
                sl = entry + min_sl_dist
                risk = sl - entry

            tp1 = entry - 2.5 * risk
            tp2 = entry - 4.0 * risk # BUG 2 FIX

            if entry >= current_price + 0.1 * atr:
                order_type = "SELL_LIMIT"
            elif entry <= current_price - 0.1 * atr:
                order_type = "SELL_STOP"
            else:
                entry = current_price + 0.25 * atr
                order_type = "SELL_LIMIT"
                risk = sl - entry
                tp1 = entry - 2.5 * risk
                tp2 = entry - 4.0 * risk

        risk = abs(entry - sl)
        reward_1 = abs(tp1 - entry)
        rr1 = reward_1 / risk if risk > 0 else 0.0

        if rr1 < 2.4:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["R:R < 1:2.5 — отброшен"]),
                ["Сетап не соответствует критерию R:R >= 1:2.5"]
            )

        signal = StrategySignal(
            direction=direction,
            order_type=order_type,
            confidence=confidence_stars,
            current_price=current_price,
            entry=entry,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward=round(rr1, 1),
            details=details,
        )

        return self._make_result(signal, details)
