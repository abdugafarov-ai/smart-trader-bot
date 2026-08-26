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

    def _find_swing_points(self, df: pd.DataFrame, lookback: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """Находит свинги максимумов и минимумов (Fractals / Pivots)."""
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
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["Недостаточно данных"]),
                ["Недостаточно данных для анализа"]
            )

        # Вычисление ATR (14)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr <= 0:
            atr = (df['high'].max() - df['low'].min()) * 0.01

        current_price = float(df['close'].iloc[-1])
        swing_highs, swing_lows = self._find_swing_points(df, lookback=5)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["Мало экстремумов"]),
                ["Недостаточно данных (Swing Points)"]
            )

        last_highs = swing_highs[-4:]
        last_lows = swing_lows[-4:]

        is_bullish_structure = (len(last_highs) >= 2 and last_highs[-1][1] > last_highs[-2][1]) and \
                               (len(last_lows) >= 2 and last_lows[-1][1] > last_lows[-2][1])
        is_bearish_structure = (len(last_highs) >= 2 and last_highs[-1][1] < last_highs[-2][1]) and \
                               (len(last_lows) >= 2 and last_lows[-1][1] < last_lows[-2][1])

        direction = "NEUTRAL"
        confidence = 0
        details = []

        bos_found = False
        choch_found = False

        # 1. Break of Structure (BOS)
        if is_bullish_structure and current_price > last_highs[-1][1]:
            bos_found = True
            direction = "LONG"
            confidence += 2
            details.append(f"Структура: бычий BOS (пробой максимума {self._format_price(last_highs[-1][1], symbol)})")
        elif is_bearish_structure and current_price < last_lows[-1][1]:
            bos_found = True
            direction = "SHORT"
            confidence += 2
            details.append(f"Структура: медвежий BOS (пробой минимума {self._format_price(last_lows[-1][1], symbol)})")

        # 2. Change of Character (CHoCH)
        if is_bearish_structure and current_price > last_highs[-1][1]:
            choch_found = True
            direction = "LONG"
            confidence += 3
            details.append(f"Слом структуры: бычий CHoCH (разворот вверх через {self._format_price(last_highs[-1][1], symbol)})")
        elif is_bullish_structure and current_price < last_lows[-1][1]:
            choch_found = True
            direction = "SHORT"
            confidence += 3
            details.append(f"Слом структуры: медвежий CHoCH (разворот вниз через {self._format_price(last_lows[-1][1], symbol)})")

        # 3. Трендовый контекст
        if not bos_found and not choch_found:
            if is_bullish_structure:
                direction = "LONG"
                confidence += 1
                details.append("Структура: восходящий тренд (Higher Highs / Higher Lows)")
            elif is_bearish_structure:
                direction = "SHORT"
                confidence += 1
                details.append("Структура: нисходящий тренд (Lower Highs / Lower Lows)")

        if direction == "NEUTRAL":
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                details
            )

        # 4. Поиск Order Block (OB)
        ob_zone: Optional[Tuple[float, float]] = None
        for i in range(len(df)-25, len(df)-2):
            is_bull = df['close'].iloc[i+1] > df['open'].iloc[i+1]
            is_bear = df['close'].iloc[i+1] < df['open'].iloc[i+1]
            impulse = abs(df['close'].iloc[i+1] - df['open'].iloc[i+1])

            if impulse > 1.3 * atr:
                if df['close'].iloc[i] < df['open'].iloc[i] and is_bull and direction == "LONG":
                    ob_high, ob_low = df['high'].iloc[i], df['low'].iloc[i]
                    ob_zone = (ob_high, ob_low)
                    confidence += 1
                    details.append(f"Бычий Order Block: {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                    break
                elif df['close'].iloc[i] > df['open'].iloc[i] and is_bear and direction == "SHORT":
                    ob_high, ob_low = df['high'].iloc[i], df['low'].iloc[i]
                    ob_zone = (ob_high, ob_low)
                    confidence += 1
                    details.append(f"Медвежий Order Block: {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                    break

        # 5. Fair Value Gap (FVG)
        fvg_zone: Optional[Tuple[float, float]] = None
        for i in range(len(df)-12, len(df)-1):
            if df['close'].iloc[i-1] > df['open'].iloc[i-1] and direction == "LONG":
                if df['low'].iloc[i] > df['high'].iloc[i-2]:
                    fvg_low, fvg_high = df['high'].iloc[i-2], df['low'].iloc[i]
                    confidence += 1
                    fvg_zone = (fvg_low, fvg_high)
                    details.append(f"Бычий FVG (имбаланс): {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                    break
            elif df['close'].iloc[i-1] < df['open'].iloc[i-1] and direction == "SHORT":
                if df['high'].iloc[i] < df['low'].iloc[i-2]:
                    fvg_low, fvg_high = df['high'].iloc[i], df['low'].iloc[i-2]
                    confidence += 1
                    fvg_zone = (fvg_low, fvg_high)
                    details.append(f"Медвежий FVG (имбаланс): {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                    break

        # 6. OTE (Optimal Trade Entry) — Fibonacci 0.618 - 0.705 - 0.786
        ote_entry = None
        swing_l = last_lows[-1][1] if last_lows else df['low'].min()
        swing_h = last_highs[-1][1] if last_highs else df['high'].max()

        if direction == "LONG" and swing_h > swing_l:
            diff = swing_h - swing_l
            fib_0618 = swing_h - 0.618 * diff
            fib_0705 = swing_h - 0.705 * diff
            ote_entry = fib_0618
            details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0705, symbol)} — {self._format_price(fib_0618, symbol)}")
        elif direction == "SHORT" and swing_h > swing_l:
            diff = swing_h - swing_l
            fib_0618 = swing_l + 0.618 * diff
            fib_0705 = swing_l + 0.705 * diff
            ote_entry = fib_0618
            details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0618, symbol)} — {self._format_price(fib_0705, symbol)}")

        # 7. Расчет точной институциональной точки входа (Entry, SL, TP1, TP2)
        if direction == "LONG":
            # Точка входа: OTE или верх Order Block / FVG
            if ob_zone:
                entry = ob_zone[0]
                sl = ob_zone[1] - 0.3 * atr
            elif ote_entry:
                entry = ote_entry
                sl = swing_l - 0.3 * atr
            elif fvg_zone:
                entry = fvg_zone[1]
                sl = fvg_zone[0] - 0.3 * atr
            else:
                entry = current_price - 0.5 * atr
                sl = swing_l - 0.3 * atr

            # Проверка разумности SL
            if sl >= entry or (entry - sl) < 0.3 * atr:
                sl = entry - 1.0 * atr

            risk = entry - sl
            # Жесткий R:R минимум 1:2.5, TP2 на 1:4.5
            tp1 = entry + 2.5 * risk
            tp2 = entry + 4.5 * risk

            # Определение типа ордера
            if entry < current_price - 0.1 * atr:
                order_type = "BUY_LIMIT"
            elif entry > current_price + 0.1 * atr:
                order_type = "BUY_STOP"
            else:
                order_type = "BUY_LIMIT"

        else:  # SHORT
            if ob_zone:
                entry = ob_zone[1]
                sl = ob_zone[0] + 0.3 * atr
            elif ote_entry:
                entry = ote_entry
                sl = swing_h + 0.3 * atr
            elif fvg_zone:
                entry = fvg_zone[0]
                sl = fvg_zone[1] + 0.3 * atr
            else:
                entry = current_price + 0.5 * atr
                sl = swing_h + 0.3 * atr

            if sl <= entry or (sl - entry) < 0.3 * atr:
                sl = entry + 1.0 * atr

            risk = sl - entry
            tp1 = entry - 2.5 * risk
            tp2 = entry - 4.5 * risk

            if entry > current_price + 0.1 * atr:
                order_type = "SELL_LIMIT"
            elif entry < current_price - 0.1 * atr:
                order_type = "SELL_STOP"
            else:
                order_type = "SELL_LIMIT"

        risk = abs(entry - sl)
        reward_1 = abs(tp1 - entry)
        rr1 = reward_1 / risk if risk > 0 else 0

        # Жесткий фильтр: если R:R < 2.4 — сигнал ВЫБРАСЫВАЕТСЯ!
        if rr1 < 2.4:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["R:R сетапа меньше 1:2.5 — отброшен"]),
                ["Сетап не соответствует строгому критерию R:R >= 1:2.5"]
            )

        confidence = max(4, min(confidence + 2, 5))

        signal = StrategySignal(
            direction=direction,
            order_type=order_type,
            confidence=confidence,
            current_price=current_price,
            entry=entry,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward=rr1,
            details=details,
        )

        return self._make_result(signal, details)
