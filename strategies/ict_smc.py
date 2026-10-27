"""
ICT / Smart Money Concepts (SMC) Strategy v2.
Институциональная стратегия поиска снайперских входов:
- Фрактальные свинги (Williams / ICT 5-bar pivots, lookback=2)
- BOS (Break of Structure) и CHoCH (Change of Character)
- Свежие немитигированные Order Blocks (OB) — поиск с конца (newest first)
- Fair Value Gaps (FVG / имбалансы) — проверка митигации
- Золотая OTE зона Фибоначчи (0.618 - 0.705)
- Premium / Discount зоны (не покупаем дорого, не продаём дёшево)
- Inducement Detection (ловушки перед разворотом)
- TP на ближайшей ликвидности (swing high/low) вместо фиксированного множителя
- Строгий R:R >= 1:2.5 (цели 1:2.5 - 1:5.0)
- Kill Zone awareness (усиление в London/NY Open)
"""

import logging
import pandas as pd
import numpy as np
from typing import Tuple, List, Optional
from datetime import datetime, timezone
import config

from .base import BaseStrategy, StrategySignal, StrategyResult

logger = logging.getLogger(__name__)


class ICTSMCStrategy(BaseStrategy):
    name = 'ICT / Smart Money Concepts'
    short_name = 'ict_smc'
    emoji = '🧠'

    def _format_price(self, price: float, symbol: str) -> str:
        if 'XAU' in symbol:
            return f"{price:.2f}"
        elif 'JPY' in symbol:
            return f"{price:.3f}"
        return f"{price:.5f}"

    def _find_swing_points(self, df: pd.DataFrame, lookback: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
        """
        Находит фрактальные свинги максимумов и минимумов (Classic 5-bar ICT/Williams Fractals).
        lookback=2 означает 2 свечи слева, 1 пик, 2 свечи справа (всего 5 свечей).
        Исключает плоские участки (plateau), требуя истинного локального экстремума.
        """
        swing_highs = []
        swing_lows = []
        n = len(df)
        if n < lookback * 2 + 1:
            return swing_highs, swing_lows

        for i in range(lookback, n - lookback):
            wh = df['high'].iloc[i-lookback:i+lookback+1]
            wl = df['low'].iloc[i-lookback:i+lookback+1]
            curr_h = float(df['high'].iloc[i])
            curr_l = float(df['low'].iloc[i])
            prev_h = float(df['high'].iloc[i-1])
            next_h = float(df['high'].iloc[i+1])
            prev_l = float(df['low'].iloc[i-1])
            next_l = float(df['low'].iloc[i+1])

            # Истинный фрактальный пик: максимален в окне и строго выше соседей
            if curr_h == wh.max() and wh.max() > wh.min():
                if curr_h > prev_h and curr_h > next_h:
                    swing_highs.append((i, curr_h))

            # Истинный фрактальный минимум: минимален в окне и строго ниже соседей
            if curr_l == wl.min() and wl.max() > wl.min():
                if curr_l < prev_l and curr_l < next_l:
                    swing_lows.append((i, curr_l))

        return swing_highs, swing_lows

    def _extract_utc_datetime(self, df: pd.DataFrame, idx: int) -> Optional[datetime]:
        """Извлекает UTC datetime из строки DataFrame по индексу или колонкам timestamp/time."""
        val = None
        row = df.iloc[idx]
        if 'timestamp' in df.columns:
            val = row['timestamp']
        elif 'time' in df.columns:
            val = row['time']
        elif isinstance(df.index, pd.DatetimeIndex):
            val = df.index[idx]

        if val is None:
            return None

        if isinstance(val, (int, float, np.integer, np.floating)):
            # миллисекунды или секунды
            if val > 1e11:
                return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(float(val), tz=timezone.utc)

        if isinstance(val, pd.Timestamp):
            if val.tz is None:
                return val.tz_localize(timezone.utc).to_pydatetime()
            return val.tz_convert(timezone.utc).to_pydatetime()

        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=timezone.utc)
            return val.astimezone(timezone.utc)

        if isinstance(val, str):
            clean_str = val.replace('.', '-').strip()
            try:
                dt = datetime.fromisoformat(clean_str)
                if dt.tzinfo is None:
                    if getattr(df, 'attrs', {}).get('is_broker_time', False):
                        from utils.broker_time import get_broker_timezone
                        dt = dt.replace(tzinfo=get_broker_timezone()).astimezone(timezone.utc)
                    else:
                        dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                try:
                    pdt = pd.to_datetime(val)
                    if pdt.tz is None:
                        if getattr(df, 'attrs', {}).get('is_broker_time', False):
                            from utils.broker_time import get_broker_timezone
                            pdt = pdt.tz_localize(get_broker_timezone()).tz_convert(timezone.utc)
                        else:
                            pdt = pdt.tz_localize(timezone.utc)
                    else:
                        pdt = pdt.tz_convert(timezone.utc)
                    return pdt.to_pydatetime()
                except Exception:
                    return None
        return None

    def _calculate_dealing_range(
        self,
        df: pd.DataFrame,
        swing_highs: list,
        swing_lows: list,
        symbol: str = "",
        timeframe: str = "H1"
    ) -> Tuple[float, float, float]:
        """
        Рассчитывает активный торговый диапазон (Dealing Range) от последнего validated swing low до swing high на H4/H1:
        Возвращает (range_low, range_high, equilibrium).
        Equilibrium = 50.0% диапазона = (range_high + range_low) / 2.0.
        """
        use_highs = swing_highs
        use_lows = swing_lows

        clean_sym = symbol.upper().replace("/", "").replace("=X", "")
        # Если таймфрейм интрадей (M15 и ниже), пробуем взять validated свинги с H1/H4
        if str(timeframe).upper() not in ("H1", "H4", "D1") and clean_sym:
            try:
                from market.data_fetcher import DataFetcher
                df_h1 = DataFetcher()._read_mt5_bars(clean_sym, "H1", limit=100)
                if df_h1 is not None and len(df_h1) >= 20:
                    h_highs, h_lows = self._find_swing_points(df_h1, lookback=2)
                    if len(h_highs) >= 2 and len(h_lows) >= 2:
                        use_highs, use_lows = h_highs, h_lows
            except Exception:
                pass

        # True Macro Dealing Range: lookback across 40-60 bars to capture genuine structural range
        recent_w = min(60, len(df))
        range_high = float(df['high'].iloc[-recent_w:].max())
        range_low = float(df['low'].iloc[-recent_w:].min())
        if len(use_highs) >= 2 and len(use_lows) >= 2:
            range_high = max(range_high, max(h[1] for h in use_highs[-4:]))
            range_low = min(range_low, min(l[1] for l in use_lows[-4:]))

        if range_high <= range_low:
            range_high = float(df['high'].max())
            range_low = float(df['low'].min())
            if range_high <= range_low:
                range_high = range_low + 0.0010

        equilibrium = (range_high + range_low) / 2.0
        return range_low, range_high, equilibrium

    def detect_judas_swing(self, df: pd.DataFrame) -> dict:
        """
        Сессионная манипуляция Judas Swing (Азиатская ликвидность):
        - Фиксация Asian High и Asian Low (00:00–07:00 UTC).
        - В окне London Open Kill Zone (07:00–10:00 UTC) отслеживание снятия азиатской ликвидности:
          • Bullish Judas Sweep: цена прокалывает Asian Low тенью и закрывается телом обратно внутрь диапазона.
            Активирует приоритет на BUY к Asian High / London High.
          • Bearish Judas Sweep: цена прокалывает Asian High тенью и закрывается телом обратно внутрь диапазона.
            Активирует приоритет на SELL к Asian Low / London Low.
        """
        result = {
            "sweep_type": None,
            "asian_high": None,
            "asian_low": None,
            "sweep_price": None,
            "details": ""
        }
        if df is None or len(df) < 5:
            return result

        dates = [self._extract_utc_datetime(df, i) for i in range(len(df))]
        if all(d is None for d in dates):
            return result

        valid_dates = [d for d in dates if d is not None]
        if not valid_dates:
            return result

        target_date = valid_dates[-1].date()

        asian_indices = [
            i for i, d in enumerate(dates)
            if d is not None and d.date() == target_date and (0 <= d.hour < 7)
        ]
        london_indices = [
            i for i, d in enumerate(dates)
            if d is not None and d.date() == target_date and (7 <= d.hour < 10)
        ]

        if not asian_indices:
            prev_dates = sorted(list({d.date() for d in valid_dates if d.date() < target_date}))
            if prev_dates:
                prev_date = prev_dates[-1]
                asian_indices = [
                    i for i, d in enumerate(dates)
                    if d is not None and d.date() == prev_date and (0 <= d.hour < 7)
                ]
                london_indices = [
                    i for i, d in enumerate(dates)
                    if d is not None and (
                        (d.date() == prev_date and 7 <= d.hour < 10) or
                        (d.date() == target_date and 7 <= d.hour < 10)
                    )
                ]

        if not asian_indices or not london_indices:
            return result

        asian_df = df.iloc[asian_indices]
        asian_high = float(asian_df['high'].max())
        asian_low = float(asian_df['low'].min())
        result["asian_high"] = asian_high
        result["asian_low"] = asian_low

        bullish_sweep_candidate = None
        bearish_sweep_candidate = None
        london_closed_below = False
        london_closed_above = False

        for idx in london_indices:
            row = df.iloc[idx]
            c_high = float(row['high'])
            c_low = float(row['low'])
            c_close = float(row['close'])

            if c_close < asian_low:
                london_closed_below = True
            if c_close > asian_high:
                london_closed_above = True

            # Bullish Judas Sweep: прокол Asian Low тенью, закрытие внутри диапазона (>= asian_low)
            if c_low < asian_low and c_close >= asian_low:
                bullish_sweep_candidate = {
                    "sweep_type": "BULLISH_JUDAS",
                    "asian_high": asian_high,
                    "asian_low": asian_low,
                    "sweep_price": c_low,
                    "details": (
                        f"Bullish Judas Sweep: прокол Asian Low ({asian_low:.5f}) тенью до {c_low:.5f} "
                        f"с закрытием тела {c_close:.5f} внутри диапазона"
                    )
                }

            # Bearish Judas Sweep: прокол Asian High тенью, закрытие внутри диапазона (<= asian_high)
            if c_high > asian_high and c_close <= asian_high:
                bearish_sweep_candidate = {
                    "sweep_type": "BEARISH_JUDAS",
                    "asian_high": asian_high,
                    "asian_low": asian_low,
                    "sweep_price": c_high,
                    "details": (
                        f"Bearish Judas Sweep: прокол Asian High ({asian_high:.5f}) тенью до {c_high:.5f} "
                        f"с закрытием тела {c_close:.5f} внутри диапазона"
                    )
                }

        # Если тело закрылось за пределами, то это истинный пробой, а не Judas Sweep
        if bullish_sweep_candidate and not london_closed_below:
            return bullish_sweep_candidate
        if bearish_sweep_candidate and not london_closed_above:
            return bearish_sweep_candidate

        return result

    def validate_structure_break(
        self,
        df: pd.DataFrame,
        swing_high: float,
        swing_low: float,
        symbol: str = "",
        candles_window: Optional[pd.DataFrame] = None
    ) -> dict:
        """
        Валидация структуры: Свип тенью vs Пробой телом (Sweep vs BOS):
        - Запрещено трактовать прокол экстремума тенью (wick) как слом структуры (BOS/MSS).
        - Wick sweep уровня считать снятием ликвидности (манипуляция / разворотный триггер).
        - Истинный слом структуры (BOS/MSS) засчитывать ИСКЛЮЧИТЕЛЬНО при закрытии ТЕЛА свечи (candle body close) за уровнем спреда.
        """
        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
        if 'JPY' in sym_clean:
            pip_unit = 0.01
        elif 'XAU' in sym_clean:
            pip_unit = 0.1
        elif 'BTC' in sym_clean or 'ETH' in sym_clean:
            pip_unit = 1.0
        else:
            pip_unit = 0.0001
        spread_buffer = 1.0 * pip_unit

        window = candles_window if candles_window is not None else df
        res = {
            "bullish_bos": False,
            "bearish_bos": False,
            "high_wick_sweep": False,
            "low_wick_sweep": False,
            "spread_buffer": spread_buffer,
            "details": []
        }
        if window is None or len(window) == 0:
            return res

        # 1. Бычий пробой максимума (BOS) vs Свип тенью (Wick Sweep)
        highs_pierced = bool((window['high'] > swing_high).any())
        body_break_high = bool((window['close'] > (swing_high + spread_buffer)).any())

        if body_break_high:
            res["bullish_bos"] = True
            res["details"].append(f"Истинный бычий BOS: закрытие тела выше {swing_high:.5f} + спред {spread_buffer:.5f}")
        elif highs_pierced:
            res["high_wick_sweep"] = True
            res["details"].append(f"Wick Sweep максимума {swing_high:.5f}: прокол тенью без закрытия тела (снятие ликвидности / манипуляция)")

        # 2. Медвежий пробой минимума (BOS) vs Свип тенью (Wick Sweep)
        lows_pierced = bool((window['low'] < swing_low).any())
        body_break_low = bool((window['close'] < (swing_low - spread_buffer)).any())

        if body_break_low:
            res["bearish_bos"] = True
            res["details"].append(f"Истинный медвежий BOS: закрытие тела ниже {swing_low:.5f} - спред {spread_buffer:.5f}")
        elif lows_pierced:
            res["low_wick_sweep"] = True
            res["details"].append(f"Wick Sweep минимума {swing_low:.5f}: прокол тенью без закрытия тела (снятие ликвидности / манипуляция)")

        return res

    def _is_ob_mitigated(self, df: pd.DataFrame, ob_idx: int, ob_high: float, ob_low: float, direction: str) -> bool:
        """
        Проверяет, был ли Order Block уже митигирован в прошлом (до текущей свечи анализа).
        Свечи после ob_idx проверяются строго до len(df) - 1, чтобы текущее касание считалось рабочим входом.
        """
        end_idx = max(ob_idx + 2, len(df) - 1)
        for i in range(ob_idx + 2, end_idx):
            if direction == "LONG":
                if df['low'].iloc[i] <= ob_high:
                    return True
            else:
                if df['high'].iloc[i] >= ob_low:
                    return True
        return False

    def _is_fvg_mitigated(self, df: pd.DataFrame, fvg_idx: int, fvg_low: float, fvg_high: float, direction: str) -> bool:
        """
        Проверяет, был ли FVG полностью перекрыт в прошлом (до текущей свечи анализа).
        """
        end_idx = max(fvg_idx + 2, len(df) - 1)
        for i in range(fvg_idx + 2, end_idx):
            if direction == "LONG":
                if df['low'].iloc[i] <= fvg_low:
                    return True
            else:
                if df['high'].iloc[i] >= fvg_high:
                    return True
        return False

    def _find_order_block(self, df: pd.DataFrame, direction: str, atr: float, current_price: float, symbol: str = "") -> Optional[Tuple[float, float]]:
        """Находит самый свежий немитигированный Order Block."""
        lookback_ob = min(35, len(df) - 2)
        for i in range(len(df) - 3, max(len(df) - lookback_ob - 2, 0), -1):
            is_bull_next = df['close'].iloc[i + 1] > df['open'].iloc[i + 1]
            is_bear_next = df['close'].iloc[i + 1] < df['open'].iloc[i + 1]
            impulse = abs(df['close'].iloc[i + 1] - df['open'].iloc[i + 1])

            if impulse > 0.4 * atr:
                if df['close'].iloc[i] < df['open'].iloc[i] and is_bull_next and direction == "LONG":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if not self._is_ob_mitigated(df, i, ob_high, ob_low, direction):
                        return (ob_high, ob_low)
                elif df['close'].iloc[i] > df['open'].iloc[i] and is_bear_next and direction == "SHORT":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if not self._is_ob_mitigated(df, i, ob_high, ob_low, direction):
                        return (ob_high, ob_low)
        return None

    def _find_fvg(self, df: pd.DataFrame, direction: str, atr: float, current_price: float, symbol: str = "") -> Optional[Tuple[float, float]]:
        """Находит самый свежий немитигированный Fair Value Gap (FVG)."""
        lookback_fvg = min(25, len(df) - 3)
        min_fvg_gap_abs = 0.030 if 'JPY' in symbol else (0.30 if 'XAU' in symbol else 0.00030)
        min_fvg_gap = max(min_fvg_gap_abs, 0.30 * atr)

        for i in range(len(df) - 2, max(len(df) - lookback_fvg - 1, 1), -1):
            middle_body = abs(df['close'].iloc[i] - df['open'].iloc[i])
            if middle_body < 0.4 * atr:
                continue

            if direction == "LONG" and df['close'].iloc[i] > df['open'].iloc[i]:
                if df['low'].iloc[i + 1] > df['high'].iloc[i - 1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i - 1]), float(df['low'].iloc[i + 1])
                    if (fvg_high - fvg_low) >= min_fvg_gap:
                        if not self._is_fvg_mitigated(df, i, fvg_low, fvg_high, direction):
                            return (fvg_low, fvg_high)
            elif direction == "SHORT" and df['close'].iloc[i] < df['open'].iloc[i]:
                if df['high'].iloc[i + 1] < df['low'].iloc[i - 1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i + 1]), float(df['low'].iloc[i - 1])
                    if (fvg_high - fvg_low) >= min_fvg_gap:
                        if not self._is_fvg_mitigated(df, i, fvg_low, fvg_high, direction):
                            return (fvg_low, fvg_high)
        return None

    def _detect_inducement(self, df: pd.DataFrame, swing_lows: list, swing_highs: list, direction: str) -> bool:
        """
        Обнаруживает Inducement (ловушку):
        - LONG: цена снимает мелкие лоу (minor swing low) перед разворотом вверх
        - SHORT: цена снимает мелкие хай (minor swing high) перед разворотом вниз
        """
        if len(df) < 10:
            return False

        recent = df.iloc[-8:]

        if direction == "LONG" and len(swing_lows) >= 3:
            # Ищем снятие предпоследнего лоу с последующим закрытием выше
            minor_low = swing_lows[-2][1]
            for i in range(len(recent) - 1):
                if float(recent['low'].iloc[i]) < minor_low and float(recent['close'].iloc[i]) > minor_low:
                    return True

        elif direction == "SHORT" and len(swing_highs) >= 3:
            minor_high = swing_highs[-2][1]
            for i in range(len(recent) - 1):
                if float(recent['high'].iloc[i]) > minor_high and float(recent['close'].iloc[i]) < minor_high:
                    return True

        return False

    def _find_liquidity_target(self, swing_highs: list, swing_lows: list, direction: str, entry: float, sl: float, df_d1: Optional[pd.DataFrame] = None) -> Optional[float]:
        """
        Находит институциональные цели на ликвидности (PDH/PDL, PWH/PWL, swing high/low с R:R >= 2.5).
        """
        risk = abs(entry - sl)
        if risk <= 0:
            return None

        # Сбор HTF магнитов (PDH/PDL)
        htf_targets = []
        if df_d1 is not None and len(df_d1) >= 2:
            try:
                # Вчерашний максимум/минимум дня (PDH / PDL)
                pdh = float(df_d1['high'].iloc[-2])
                pdl = float(df_d1['low'].iloc[-2])
                htf_targets.extend([pdh, pdl])
                # Максимум/минимум за последние 5 дней (PWH / PWL)
                if len(df_d1) >= 6:
                    pwh = float(df_d1['high'].iloc[-6:-1].max())
                    pwl = float(df_d1['low'].iloc[-6:-1].min())
                    htf_targets.extend([pwh, pwl])
            except Exception:
                pass

        if direction == "LONG":
            valid = [h[1] for h in swing_highs if h[1] >= entry + 2.5 * risk and h[1] <= entry + 6.0 * risk]
            valid_htf = [p for p in htf_targets if p >= entry + 2.5 * risk and p <= entry + 6.0 * risk]
            all_t = valid + valid_htf
            if all_t:
                return min(all_t)
        else:
            valid = [l[1] for l in swing_lows if l[1] <= entry - 2.5 * risk and l[1] >= entry - 6.0 * risk]
            valid_htf = [p for p in htf_targets if p <= entry - 2.5 * risk and p >= entry - 6.0 * risk]
            all_t = valid + valid_htf
            if all_t:
                return max(all_t)

        return None

    def _check_d1_trend(self, df_d1: pd.DataFrame, symbol: str = "") -> Tuple[Optional[str], str]:
        """
        Проверяет тренд старшего таймфрейма D1 (вчерашняя закрытая дневная свеча + EMA50):
        - Вчерашняя закрытая D1 свеча (iloc[-2]): имеет абсолютное право вето.
          Если вчерашняя свеча сильно бычья (close - open >= 15 pips) -> UP (блокирует все SHORT).
          Если вчерашняя свеча сильно медвежья (open - close >= 15 pips) -> DOWN (блокирует все LONG).
        - Для умеренных свечей (< 15 pips) проверяется согласованность с EMA50.
        - Устраняет баг отставания EMA50 во время сильных импульсных 3-дневных пробоев.
        """
        if df_d1 is None or len(df_d1) < 2:
            return None, "Недостаточно данных D1"

        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
        if 'JPY' in sym_clean:
            pip_unit = 0.01
        elif 'XAU' in sym_clean:
            pip_unit = 0.1
        elif 'BTC' in sym_clean or 'ETH' in sym_clean:
            pip_unit = 1.0
        else:
            pip_unit = 0.0001

        if 'XAU' in sym_clean or 'GOLD' in sym_clean:
            threshold_15_pips = 10.0  # $10.00 минимальный импульс закрытой свечи D1 для Золота
        else:
            threshold_15_pips = 15.0 * pip_unit

        # Вчерашняя полностью закрытая свеча D1 (iloc[-2])
        d1_yesterday = df_d1.iloc[-2]
        y_open = float(d1_yesterday['open'])
        y_close = float(d1_yesterday['close'])
        y_body = y_close - y_open

        if 'XAU' in sym_clean or 'GOLD' in sym_clean:
            # Институциональный D1 Trend Filter (Bias) для Золота:
            # Оценивает глобальный тренд по EMA50 и 5-дневной структуре.
            # Обычный локальный откат (красная свеча) в рамках восходящего тренда не является сменой тренда.
            span_len = min(50, len(df_d1))
            ema50_series = df_d1['close'].ewm(span=span_len, adjust=False).mean()
            ema50_curr = float(ema50_series.iloc[-2])
            ema50_prev = float(ema50_series.iloc[-3]) if len(ema50_series) >= 3 else ema50_curr

            o_5d = float(df_d1['open'].iloc[-6]) if len(df_d1) >= 6 else float(df_d1['open'].iloc[0])
            net_5d = y_close - o_5d

            is_above_ema = y_close > ema50_curr
            is_below_ema = y_close < ema50_curr
            ema_slope_up = ema50_curr > ema50_prev
            ema_slope_down = ema50_curr < ema50_prev

            if is_above_ema and (ema_slope_up or net_5d > 0):
                return "UP", f"Глобальный D1 тренд XAUUSD восходящий (цена ${y_close:.2f} > EMA50 ${ema50_curr:.2f}, 5d: +${net_5d:.2f})"
            elif is_below_ema and (ema_slope_down or net_5d < 0):
                return "DOWN", f"Глобальный D1 тренд XAUUSD нисходящий (цена ${y_close:.2f} < EMA50 ${ema50_curr:.2f}, 5d: -${abs(net_5d):.2f})"
            elif is_above_ema:
                return "UP", f"D1 уклон XAUUSD умеренно бычий (цена ${y_close:.2f} > EMA50 ${ema50_curr:.2f})"
            elif is_below_ema:
                return "DOWN", f"D1 уклон XAUUSD умеренно медвежий (цена ${y_close:.2f} < EMA50 ${ema50_curr:.2f})"
            else:
                return "NEUTRAL", "D1 уклон XAUUSD нейтральный / консолидация"

        # 1. Абсолютное право вето вчерашней закрытой свечи D1 (iloc[-2]) при импульсе >= 15 пипсов
        if y_body >= threshold_15_pips:
            pips_val = y_body / pip_unit
            return "UP", f"Вчерашняя закрытая D1 свеча сильно бычья (+{pips_val:.1f} п >= 15 п) — вето на продажи (SHORT)"
        elif -y_body >= threshold_15_pips:
            pips_val = (-y_body) / pip_unit
            return "DOWN", f"Вчерашняя закрытая D1 свеча сильно медвежья (-{pips_val:.1f} п >= 15 п) — вето на покупки (LONG)"

        # 2. Устранение бага запаздывания EMA50: Проверка 3-дневного пробоя (breakout за последние 3 закрытые свечи D1)
        # Если за последние 3 закрытых дня рынок вырос/упал >= 15 пипсов, EMA50 lag не имеет права блокировать тренд
        if len(df_d1) >= 4:
            o_3d = float(df_d1['open'].iloc[-4])
            net_3d = y_close - o_3d
            net_3d_pips = net_3d / pip_unit

            # 3-дневный бычий пробой: чистый прирост >= 15 пипсов, вчерашняя свеча не медвежья
            if net_3d >= threshold_15_pips and (y_close >= y_open or net_3d >= 25.0 * pip_unit):
                return "UP", f"3-дневный бычий пробой D1 (+{net_3d_pips:.1f} п >= 15 п за 3 дня) — вето на продажи (SHORT)"
            # 3-дневный медвежий пробой: чистое падение >= 15 пипсов, вчерашняя свеча не бычья
            elif -net_3d >= threshold_15_pips and (y_close <= y_open or -net_3d >= 25.0 * pip_unit):
                return "DOWN", f"3-дневный медвежий пробой D1 (-{(-net_3d_pips):.1f} п >= 15 п за 3 дня) — вето на покупки (LONG)"

        # 3. Для умеренных свечей и консолидации (< 15 пипсов) — сверяем со средней EMA50
        span_len = min(50, len(df_d1))
        ema50_series = df_d1['close'].ewm(span=span_len, adjust=False).mean()
        ema50_curr = float(ema50_series.iloc[-2])  # по закрытой вчерашней свече
        ema50_prev = float(ema50_series.iloc[-3]) if len(ema50_series) >= 3 else ema50_curr

        ema50_slope_down = (ema50_curr < ema50_prev)
        ema50_slope_up = (ema50_curr > ema50_prev)

        ema50_down = ema50_slope_down and (y_close < ema50_curr)
        ema50_up = ema50_slope_up and (y_close > ema50_curr)

        d1_candle_down = (y_close < y_open)
        d1_candle_up = (y_close > y_open)

        if d1_candle_down and (ema50_down or y_close < ema50_curr):
            return "DOWN", f"Вчерашняя D1 свеча медвежья ({y_close:.5f} < {y_open:.5f}) и цена ниже EMA50({span_len}) ({ema50_curr:.5f})"
        elif d1_candle_up and (ema50_up or y_close > ema50_curr):
            return "UP", f"Вчерашняя D1 свеча бычья ({y_close:.5f} > {y_open:.5f}) и цена выше EMA50({span_len}) ({ema50_curr:.5f})"
        else:
            c_desc = "медвежья" if d1_candle_down else ("бычья" if d1_candle_up else "доджи")
            e_desc = "ниже EMA50" if y_close < ema50_curr else "выше EMA50"
            return "NEUTRAL", f"Смешанный D1 (свеча {c_desc}, {e_desc})"

    def calculate_draw_on_liquidity(self, df_d1: Optional[pd.DataFrame], current_price: float, symbol: str) -> dict:
        """
        ФРАКТАЛЬНАЯ МОДЕЛЬ (HTF Narrative):
        Определение магнита ликвидности (Draw on Liquidity — DOL):
        - Previous Day High (PDH) и Previous Day Low (PDL) из закрытой свечи D1 iloc[-2].
        - Previous Week High (PWH) и Previous Week Low (PWL) за 5 последних закрытых дней D1 (iloc[-7:-2]).
        """
        dol = {
            "pdh": None,
            "pdl": None,
            "pwh": None,
            "pwl": None,
            "bullish_target": None,
            "bearish_target": None,
            "pdh_swept": False,
            "pdl_swept": False,
            "details": []
        }
        if df_d1 is None or len(df_d1) < 2:
            return dol

        try:
            prev_day = df_d1.iloc[-2]
            pdh = float(prev_day['high'])
            pdl = float(prev_day['low'])
            dol["pdh"] = pdh
            dol["pdl"] = pdl

            if len(df_d1) >= 7:
                pw_slice = df_d1.iloc[-7:-2]
                pwh = float(pw_slice['high'].max())
                pwl = float(pw_slice['low'].min())
            else:
                pwh = float(df_d1['high'].iloc[:-1].max())
                pwl = float(df_d1['low'].iloc[:-1].min())
            dol["pwh"] = pwh
            dol["pwl"] = pwl

            curr_day = df_d1.iloc[-1]
            c_high = float(curr_day['high'])
            c_low = float(curr_day['low'])

            dol["pdh_swept"] = (c_high > pdh)
            dol["pdl_swept"] = (c_low < pdl)

            # Цели магнита ликвидности (Draw on Liquidity):
            dol["bullish_target"] = pwh if dol["pdh_swept"] else pdh
            dol["bearish_target"] = pwl if dol["pdl_swept"] else pdl

            dol["details"].append(
                f"🎯 Draw on Liquidity (DOL): PDH={self._format_price(pdh, symbol)}, PDL={self._format_price(pdl, symbol)}, "
                f"PWH={self._format_price(pwh, symbol)}, PWL={self._format_price(pwl, symbol)}"
            )
        except Exception as e:
            logger.debug("Error calculating DOL: %s", e)

        return dol

    def check_smt_divergence(
        self,
        symbol: str,
        direction: str,
        timeframe: str = "H1",
        df_primary: Optional[pd.DataFrame] = None
    ) -> dict:
        """
        ИНТЕРМАРКЕТ-АНАЛИЗ И ДИВЕРГЕНЦИЯ SMT (Smart Money Tool):
        1. DXY vs EURUSD (Кросс-рыночная институциональная валидация с индексом доллара):
           • Если DXY формирует Higher High, но EURUSD не делает Lower Low (формирует Higher Low) ->
             Бычья институциональная дивергенция (SMT Accumulation).
           • Если DXY формирует Lower Low, но EURUSD не делает Higher High (формирует Lower High) ->
             Медвежья институциональная дивергенция (SMT Distribution).
        2. Коррелирующие пары (EURUSD vs GBPUSD):
           • Если GBPUSD обновляет максимум дня (HOD / PDH), а EURUSD запаздывает ->
             Опережающий триггер скорого снятия ликвидности (Leading SMT Liquidity Sweep Trigger).
           • Если GBPUSD обновляет минимум дня (LOD / PDL), а EURUSD запаздывает ->
             Опережающий триггер скорого снятия ликвидности вниз (Leading SMT Low Liquidity Sweep Trigger).
           • SMT Accumulation (Бычья дивергенция): GBPUSD делает LL, а EURUSD держит HL (или наоборот).
           • SMT Distribution (Медвежья дивергенция): GBPUSD делает HH, а EURUSD держит LH (или наоборот).
        3. Для долларовых мажоров USDJPY, USDCHF, USDCAD:
           • Валидация долларового импульса через индекс DXY.
        """
        res = {
            "smt_confirmed": False,
            "smt_type": "NONE",
            "leading_trigger": False,
            "details": [],
        }
        clean_sym = symbol.upper().replace("/", "").replace("=X", "")
        dir_clean = direction.upper()

        if "XAU" in clean_sym or "GOLD" in clean_sym:
            # Rigorous SMT: Gold (XAUUSD) requires inverse divergence comparison with DXY
            # If DXY data is not available, do NOT fabricate fake SMT confluence
            try:
                from market.data_fetcher import DataFetcher
                fetcher = DataFetcher()
                df_dxy = fetcher.get_dxy_bars_sync(timeframe, limit=100)
                if df_dxy is None or df_dxy.empty or len(df_dxy) < 20:
                    res["smt_confirmed"] = False
                    res["smt_type"] = "NONE"
                    res["details"].append("ℹ️ SMT: Данные DXY недоступны для корректной сверки XAUUSD (NO SMT)")
                    return res
                # Compare Gold swings vs DXY swings (inverse correlation)
                sw_dxy_h, sw_dxy_l = self._find_swing_points(df_dxy, lookback=2)
                if not sw_dxy_h or not sw_dxy_l:
                    res["smt_confirmed"] = False
                    return res
                # In Bullish Gold SMT: Gold makes Lower Low while DXY FAILS to make Higher High
                if dir_clean == "LONG" and df_primary is not None and not df_primary.empty:
                    gold_sw_h, gold_sw_l = self._find_swing_points(df_primary, lookback=2)
                    if len(gold_sw_l) >= 2 and len(sw_dxy_h) >= 2:
                        gold_ll = gold_sw_l[-1][1] < gold_sw_l[-2][1]
                        dxy_fail_hh = sw_dxy_h[-1][1] <= sw_dxy_h[-2][1]
                        if gold_ll and dxy_fail_hh:
                            res["smt_confirmed"] = True
                            res["smt_type"] = "BULLISH_XAU_DXY_DIVERGENCE"
                            res["details"].append("⚡ Истинная бычья SMT: Золото обновило Low, но DXY НЕ смог обновить High!")
                            return res
                elif dir_clean == "SHORT" and df_primary is not None and not df_primary.empty:
                    gold_sw_h, gold_sw_l = self._find_swing_points(df_primary, lookback=2)
                    if len(gold_sw_h) >= 2 and len(sw_dxy_l) >= 2:
                        gold_hh = gold_sw_h[-1][1] > gold_sw_h[-2][1]
                        dxy_fail_ll = sw_dxy_l[-1][1] >= sw_dxy_l[-2][1]
                        if gold_hh and dxy_fail_ll:
                            res["smt_confirmed"] = True
                            res["smt_type"] = "BEARISH_XAU_DXY_DIVERGENCE"
                            res["details"].append("⚡ Истинная медвежья SMT: Золото обновило High, но DXY НЕ смог обновить Low!")
                            return res
                res["smt_confirmed"] = False
                return res
            except Exception as e:
                res["smt_confirmed"] = False
                res["details"].append(f"SMT check error: {e}")
                return res

        try:
            from market.data_fetcher import DataFetcher
            fetcher = DataFetcher()

            # Читаем бары для первичной пары (если не переданы)
            if df_primary is None or df_primary.empty:
                df_primary = fetcher._read_mt5_bars(clean_sym, timeframe, limit=100)
            if df_primary is None or len(df_primary) < 15:
                res["details"].append("Недостаточно данных MT5 для SMT анализа")
                return res

            # --- 1. КОРРЕЛИРУЮЩИЕ ПАРЫ (EURUSD vs GBPUSD, AUDUSD vs NZDUSD) ---
            corr_sym = "GBPUSD" if clean_sym == "EURUSD" else ("EURUSD" if clean_sym == "GBPUSD" else ("NZDUSD" if clean_sym == "AUDUSD" else ("AUDUSD" if clean_sym == "NZDUSD" else "EURUSD")))
            df_corr = fetcher._read_mt5_bars(corr_sym, timeframe, limit=100)

            if df_corr is not None and len(df_corr) >= 15:
                lookback_24 = min(24, len(df_primary), len(df_corr))

                corr_recent_h = float(df_corr['high'].iloc[-4:].max())
                corr_prev_h = float(df_corr['high'].iloc[-lookback_24:-4].max()) if lookback_24 > 4 else corr_recent_h
                prim_recent_h = float(df_primary['high'].iloc[-4:].max())
                prim_prev_h = float(df_primary['high'].iloc[-lookback_24:-4].max()) if lookback_24 > 4 else prim_recent_h

                corr_broke_high = (corr_recent_h > corr_prev_h)
                prim_broke_high = (prim_recent_h > prim_prev_h)

                corr_recent_l = float(df_corr['low'].iloc[-4:].min())
                corr_prev_l = float(df_corr['low'].iloc[-lookback_24:-4].min()) if lookback_24 > 4 else corr_recent_l
                prim_recent_l = float(df_primary['low'].iloc[-4:].min())
                prim_prev_l = float(df_primary['low'].iloc[-lookback_24:-4].min()) if lookback_24 > 4 else prim_recent_l

                corr_broke_low = (corr_recent_l < corr_prev_l)
                prim_broke_low = (prim_recent_l < prim_prev_l)

                if dir_clean == "LONG":
                    # Опережающий триггер на бай: GBPUSD обновил максимум дня, а EURUSD запаздывает ->
                    # скорое снятие ликвидности наверху / импульсный догон
                    if corr_broke_high and not prim_broke_high:
                        res["leading_trigger"] = True
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_LEADING_LIQUIDITY_TRIGGER"
                        res["details"].append(
                            f"⚡ SMT Опережающий триггер: {corr_sym} обновил максимум дня, "
                            f"а {clean_sym} запаздывает — ожидается скорое снятие ликвидности / импульсный догон!"
                        )
                    # Бычья дивергенция (SMT Accumulation): один инструмент сделал Lower Low, а второй удержал Higher Low
                    elif corr_broke_low and not prim_broke_low:
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_BULLISH_ACCUMULATION"
                        res["details"].append(
                            f"🧠 SMT Accumulation (Бычья институциональная дивергенция): {corr_sym} обновил минимум, "
                            f"но {clean_sym} удержал поддержку (Higher Low) — скрытое накопление Smart Money!"
                        )
                    elif prim_broke_low and not corr_broke_low:
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_BULLISH_ACCUMULATION"
                        res["details"].append(
                            f"🧠 SMT Accumulation: {clean_sym} снял стопы ниже минимума, "
                            f"пока {corr_sym} удерживает силу — подтвержденная бычья дивергенция!"
                        )

                elif dir_clean == "SHORT":
                    # Опережающий триггер на шорт: GBPUSD обновил минимум дня, а EURUSD запаздывает ->
                    # скорое снятие ликвидности внизу
                    if corr_broke_low and not prim_broke_low:
                        res["leading_trigger"] = True
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_LEADING_LIQUIDITY_TRIGGER"
                        res["details"].append(
                            f"⚡ SMT Опережающий триггер: {corr_sym} обновил минимум дня, "
                            f"а {clean_sym} запаздывает — ожидается скорое снятие ликвидности внизу!"
                        )
                    # Медвежья дивергенция (SMT Distribution): один инструмент обновил максимум, а второй проявил слабость
                    elif corr_broke_high and not prim_broke_high:
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_BEARISH_DISTRIBUTION"
                        res["details"].append(
                            f"🧠 SMT Distribution (Медвежья институциональная дивергенция): {corr_sym} показал перехай, "
                            f"а {clean_sym} проявил слабость (Lower High) — институциональное распределение Smart Money!"
                        )
                    elif prim_broke_high and not corr_broke_high:
                        res["smt_confirmed"] = True
                        res["smt_type"] = "SMT_BEARISH_DISTRIBUTION"
                        res["details"].append(
                            f"🧠 SMT Distribution: {clean_sym} сделал свип максимума, "
                            f"пока {corr_sym} слабеет — подтвержденный шорт-триггер SMT!"
                        )

            # --- 2. ПРОВЕРКА DXY vs ПАРА (Синхронный расчет ICE DXY с выравниванием баров) ---
            df_eur_override = df_primary if clean_sym == "EURUSD" else None
            df_dxy = fetcher.get_dxy_bars_sync(timeframe=timeframe, limit=60, df_eur_override=df_eur_override)

            if df_dxy is not None and not df_dxy.empty and len(df_dxy) >= 10:
                n_check = min(20, len(df_dxy))
                dxy_slice = df_dxy.tail(n_check)
                dxy_recent_h = float(dxy_slice['high'].iloc[-4:].max())
                dxy_prev_h = float(dxy_slice['high'].iloc[:-4].max()) if len(dxy_slice) > 4 else dxy_recent_h
                dxy_recent_l = float(dxy_slice['low'].iloc[-4:].min())
                dxy_prev_l = float(dxy_slice['low'].iloc[:-4].min()) if len(dxy_slice) > 4 else dxy_recent_l

                dxy_made_hh = (dxy_recent_h > dxy_prev_h)
                dxy_made_ll = (dxy_recent_l < dxy_prev_l)

                df_eur = df_primary if clean_sym == "EURUSD" else fetcher._read_mt5_bars("EURUSD", timeframe, limit=60)
                if df_eur is not None and not df_eur.empty and len(df_eur) >= 10:
                    eur_slice = df_eur.tail(n_check)
                    eur_recent_h = float(eur_slice['high'].iloc[-4:].max())
                    eur_prev_h = float(eur_slice['high'].iloc[:-4].max()) if len(eur_slice) > 4 else eur_recent_h
                    eur_recent_l = float(eur_slice['low'].iloc[-4:].min())
                    eur_prev_l = float(eur_slice['low'].iloc[:-4].min()) if len(eur_slice) > 4 else eur_recent_l

                    eur_made_hh = (eur_recent_h > eur_prev_h)
                    eur_made_ll = (eur_recent_l < eur_prev_l)

                    if clean_sym in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
                        if dir_clean == "LONG":
                            # Если DXY формирует Higher High, но EURUSD не делает Lower Low -> SMT Accumulation!
                            if dxy_made_hh and not eur_made_ll:
                                res["smt_confirmed"] = True
                                res["smt_type"] = "SMT_DXY_BULLISH_ACCUMULATION"
                                res["details"].append(
                                    "🏛 DXY SMT Accumulation: Индекс доллара (DXY) сформировал Higher High, "
                                    f"но {clean_sym} отказался обновлять Lower Low — классическая бычья институциональная дивергенция крупного капитала!"
                                )
                        elif dir_clean == "SHORT":
                            # Если DXY формирует Lower Low, но EURUSD не делает Higher High -> SMT Distribution!
                            if dxy_made_ll and not eur_made_hh:
                                res["smt_confirmed"] = True
                                res["smt_type"] = "SMT_DXY_BEARISH_DISTRIBUTION"
                                res["details"].append(
                                    "🏛 DXY SMT Distribution: Индекс доллара (DXY) сформировал Lower Low, "
                                    f"но {clean_sym} не смог обновить Higher High — классическое медвежье институциональное распределение Smart Money!"
                                )
                    elif clean_sym in ("USDJPY", "USDCHF", "USDCAD"):
                        if dir_clean == "LONG":
                            if dxy_made_hh and not eur_made_ll:
                                res["smt_confirmed"] = True
                                res["smt_type"] = "SMT_DXY_BULLISH_CONFIRMATION"
                                res["details"].append(f"🏛 DXY SMT Confirmation: Валидация долларового импульса на {clean_sym}")
                        elif dir_clean == "SHORT":
                            if dxy_made_ll and not eur_made_hh:
                                res["smt_confirmed"] = True
                                res["smt_type"] = "SMT_DXY_BEARISH_CONFIRMATION"
                                res["details"].append(f"🏛 DXY SMT Confirmation: Валидация долларовой слабости на {clean_sym}")

        except Exception as e:
            res["details"].append(f"Ошибка проверки SMT: {e}")

        return res


    def check_ltf_confirmation(
        self,
        symbol: str,
        direction: str,
        h1_poi_zone: Optional[Tuple[float, float]],
        timeframe: str = "M15",
        df_ltf: Optional[pd.DataFrame] = None
    ) -> dict:
        """
        ФРАКТАЛЬНАЯ МОДЕЛЬ ВХОДА (Layer 3: LTF Trigger):
        Вход СТРОГО по паттерну подтверждения:
        1. Касание зоны интереса H1 POI (Order Block / FVG в Discount/Premium).
        2. Резкий слом структуры (Market Structure Shift — MSS с Displacement, тело свечи >= 1.1 ATR)
           на M15 / M5, а не слепой отложенный ордер!
        """
        res = {
            "poi_touched": False,
            "mss_confirmed": False,
            "displacement": False,
            "mss_price": None,
            "details": []
        }
        if not h1_poi_zone:
            res["details"].append("H1 POI не определен")
            return res

        try:
            from market.data_fetcher import DataFetcher
            fetcher = DataFetcher()
            clean_sym = symbol.upper().replace("/", "").replace("=X", "")

            if df_ltf is not None and len(df_ltf) > 60:
                df_ltf = df_ltf.iloc[-60:].copy()

            if df_ltf is None:
                # Читаем подтверждающий ТФ (для Золота H1 вместо M15/M5 шума)
                if 'XAU' in clean_sym or 'GOLD' in clean_sym:
                    check_tf = "H1" if timeframe in ("H4", "D1") else timeframe
                    df_ltf = fetcher._read_mt5_bars(clean_sym, check_tf, limit=60)
                else:
                    df_ltf = fetcher._read_mt5_bars(clean_sym, "M15", limit=60)
                    if df_ltf is None or len(df_ltf) < 15:
                        df_ltf = fetcher._read_mt5_bars(clean_sym, "M5", limit=60)
            if df_ltf is None or len(df_ltf) < 15:
                # Strict Rule: Missing LTF bars strictly means NO CONFIRMATION / NO TRADE
                res["poi_touched"] = False
                res["mss_confirmed"] = False
                res["displacement"] = False
                res["details"].append("⛔ Отсутствуют бары младшего ТФ (M5/M15) — автоматическое подтверждение ЗАПРЕЩЕНО (NO TRADE)")
                return res

            poi_low, poi_high = min(h1_poi_zone), max(h1_poi_zone)

            # 2. Расчет ATR на LTF
            high_low = df_ltf['high'] - df_ltf['low']
            high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
            low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            ltf_atr = tr.rolling(14).mean().iloc[-1]
            if pd.isna(ltf_atr) or ltf_atr <= 0:
                ltf_atr = 0.00050

            # 1. Проверяем касание H1 POI в последних 20 свечах LTF с буфером 0.5 ATR
            poi_low_buf = poi_low - 0.50 * ltf_atr
            poi_high_buf = poi_high + 0.50 * ltf_atr
            recent_candles = df_ltf.iloc[-20:]
            if direction == "LONG":
                touched = any(float(row['low']) <= poi_high_buf and float(row['high']) >= poi_low_buf for _, row in recent_candles.iterrows())
            else:
                touched = any(float(row['high']) >= poi_low_buf and float(row['low']) <= poi_high_buf for _, row in recent_candles.iterrows())

            res["poi_touched"] = touched

            # 3. Свинги на LTF для валидации MSS / отбоя
            sh_ltf, sl_ltf = self._find_swing_points(df_ltf, lookback=2)

            if direction == "LONG":
                last_swing_h = sh_ltf[-1][1] if sh_ltf else float(df_ltf['high'].iloc[-4:-1].max())
                for idx in range(-6, 0):
                    c_open = float(df_ltf['open'].iloc[idx])
                    c_close = float(df_ltf['close'].iloc[idx])
                    c_low = float(df_ltf['low'].iloc[idx])
                    body = c_close - c_open
                    prev_h = float(df_ltf['high'].iloc[idx - 1])
                    is_mss = (c_close > last_swing_h and body >= 0.35 * ltf_atr)
                    is_expansion = (body >= 0.50 * ltf_atr and c_close > prev_h)
                    is_reversal_pin = (c_close > c_open and c_close >= prev_h and (c_open - c_low) >= 0.40 * ltf_atr)
                    if touched and (is_mss or is_expansion or is_reversal_pin):
                        res["mss_confirmed"] = True
                        res["displacement"] = True
                        res["mss_price"] = c_close
                        res["details"].append(
                            f"⚡ Подтвержден бычий разворотный импульс на H1: "
                            f"закрытие {self._format_price(c_close, symbol)} ({body/ltf_atr:.1f}x ATR) со дна диапазона!"
                        )
                        break
            elif direction == "SHORT":
                last_swing_l = sl_ltf[-1][1] if sl_ltf else float(df_ltf['low'].iloc[-4:-1].min())
                for idx in range(-6, 0):
                    c_open = float(df_ltf['open'].iloc[idx])
                    c_close = float(df_ltf['close'].iloc[idx])
                    c_high = float(df_ltf['high'].iloc[idx])
                    body = c_open - c_close
                    prev_l = float(df_ltf['low'].iloc[idx - 1])
                    is_mss = (c_close < last_swing_l and body >= 0.35 * ltf_atr)
                    is_expansion = (body >= 0.50 * ltf_atr and c_close < prev_l)
                    is_reversal_pin = (c_close < c_open and c_close <= prev_l and (c_high - c_open) >= 0.40 * ltf_atr)
                    if touched and (is_mss or is_expansion or is_reversal_pin):
                        res["mss_confirmed"] = True
                        res["displacement"] = True
                        res["mss_price"] = c_close
                        res["details"].append(
                            f"⚡ Подтвержден медвежий разворотный импульс на H1: "
                            f"закрытие {self._format_price(c_close, symbol)} ({body/ltf_atr:.1f}x ATR) с вершины диапазона!"
                        )
                        break
        except Exception as e:
            res["details"].append(f"Ошибка проверки LTF MSS: {e}")

        return res

    def _is_in_kill_zone(self, current_time: Optional[datetime] = None) -> bool:
        """Проверяет, сейчас ли Kill Zone (London Open 07-10 или NY Open 12-15 UTC)."""
        try:
            return config.is_in_kill_zone(current_time)
        except Exception:
            try:
                now = current_time or datetime.now(timezone.utc)
                hour = now.hour
                return (7 <= hour < 10) or (12 <= hour < 15)
            except Exception:
                return False

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str, df_d1: Optional[pd.DataFrame] = None, df_ltf: Optional[pd.DataFrame] = None, current_time: Optional[datetime] = None) -> StrategyResult:
        # ═══ ВРЕМЕННЫЕ ЦИКЛЫ IPDA И АЛГОРИТМИЧЕСКИЕ ТАЙМИНГИ ═══
        # Запрет любых новых входов за 15 мин до закрытия Лондона (15:45-16:00 UTC) и в мертвую зону (17:00-00:00 UTC)
        is_banned, ban_reason = config.is_ipda_time_banned(current_time)
        if is_banned:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=[f"❌ IPDA Timing Ban: {ban_reason}"]),
                [f"IPDA Timing Ban: {ban_reason} — новые входы заблокированы"]
            )

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

        # Strict Rule: Do not synthesize fake swings if canonical fractal rule finds < 2 swings.
        # Insufficient swings means market is consolidating or data history is too short -> strictly NEUTRAL.

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=["Консолидация / Мало выраженных свингов"]),
                ["Рынок в узком боковике, нет четкой структуры"]
            )

        last_highs = swing_highs[-4:]
        last_lows = swing_lows[-4:]

        # Structure Detection
        hh1_idx, hh1 = last_highs[-1][0], last_highs[-1][1]
        hh2_idx, hh2 = last_highs[-2][0], last_highs[-2][1]
        hl1_idx, hl1 = last_lows[-1][0], last_lows[-1][1]
        hl2_idx, hl2 = last_lows[-2][0], last_lows[-2][1]

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

        # 3. ВАЛИДАЦИЯ СТРУКТУРЫ: СВИП ТЕНЬЮ vs ПРОБОЙ ТЕЛОМ (SWEEP vs BOS)
        # Исключаем формирующуюся свечу iloc[-1] во избежание ложных пробоев внутри текущего тика.
        # ВАЖНО: Оцениваем только закрытые свечи СТРОГО ПОСЛЕ точки свинга, никогда не проверяем свечи старше свинга!
        start_bull = hh1_idx + 1
        bull_window = df.iloc[start_bull:-1] if start_bull < len(df) - 1 else df.iloc[0:0]

        start_bear = hl1_idx + 1
        bear_window = df.iloc[start_bear:-1] if start_bear < len(df) - 1 else df.iloc[0:0]

        struct_bull = self.validate_structure_break(df, swing_high=hh1, swing_low=hl1, symbol=symbol, candles_window=bull_window)
        struct_bear = self.validate_structure_break(df, swing_high=hh1, swing_low=hl1, symbol=symbol, candles_window=bear_window)

        bull_break = struct_bull["bullish_bos"]
        bull_wick_sweep = struct_bull["high_wick_sweep"]

        bear_break = struct_bear["bearish_bos"]
        bear_wick_sweep = struct_bear["low_wick_sweep"]

        if bull_wick_sweep:
            details.append(f"⚡ Снятие ликвидности тенью (Wick Sweep): ложный прокол максимума {self._format_price(hh1, symbol)} тенью без закрытия тела — манипуляция (разворотный триггер)")

        if bear_wick_sweep:
            details.append(f"⚡ Снятие ликвидности тенью (Wick Sweep): ложный прокол минимума {self._format_price(hl1, symbol)} тенью без закрытия тела — манипуляция (разворотный триггер)")

        if is_bullish_structure and bull_break:
            bos_found = True
            direction = "LONG"
            sub_signals += 1
            details.append(f"Истинный бычий BOS (закрытие тела выше максимума {self._format_price(hh1, symbol)})")
        elif is_bearish_structure and bear_break:
            bos_found = True
            direction = "SHORT"
            sub_signals += 1
            details.append(f"Истинный медвежий BOS (закрытие тела ниже минимума {self._format_price(hl1, symbol)})")

        if not bos_found:
            if is_bearish_structure and bull_break:
                choch_found = True
                direction = "LONG"
                sub_signals += 1
                details.append(f"Бычий CHoCH (разворот тренда вверх закрытием тела через {self._format_price(hh1, symbol)})")
            elif is_bullish_structure and bear_break:
                choch_found = True
                direction = "SHORT"
                sub_signals += 1
                details.append(f"Медвежий CHoCH (разворот тренда вниз закрытием тела через {self._format_price(hl1, symbol)})")

        # ═══ СЕССИОННАЯ МАНИПУЛЯЦИЯ JUDAS SWING (АЗИАТСКАЯ ЛИКВИДНОСТЬ) ═══
        judas = self.detect_judas_swing(df)
        judas_target = None
        if judas.get("sweep_type") == "BULLISH_JUDAS":
            sub_signals += 2
            a_low_str = self._format_price(judas['asian_low'], symbol)
            a_high_str = self._format_price(judas['asian_high'], symbol)
            details.append(
                f"⚡ Judas Swing: Снятие азиатской ликвидности (Bullish Judas Sweep: прокол Asian Low {a_low_str} тенью "
                f"с возвратом внутрь). Активирован приоритет на BUY к Asian High {a_high_str}"
            )
            judas_target = judas.get("asian_high")
            if direction != "SHORT" or not bos_found:
                direction = "LONG"
        elif judas.get("sweep_type") == "BEARISH_JUDAS":
            sub_signals += 2
            a_low_str = self._format_price(judas['asian_low'], symbol)
            a_high_str = self._format_price(judas['asian_high'], symbol)
            details.append(
                f"⚡ Judas Swing: Снятие азиатской ликвидности (Bearish Judas Sweep: прокол Asian High {a_high_str} тенью "
                f"с возвратом внутрь). Активирован приоритет на SELL к Asian Low {a_low_str}"
            )
            judas_target = judas.get("asian_low")
            if direction != "LONG" or not bos_found:
                direction = "SHORT"

        # ═══ РАСЧЕТ DEALING RANGE И D1 ТРЕНДА ПЕРЕД ОЦЕНКОЙ ОТКАТА ═══
        range_low, range_high, equilibrium = self._calculate_dealing_range(
            df, swing_highs, swing_lows, symbol=symbol, timeframe=timeframe
        )
        dr_span = range_high - range_low
        dr_pct = ((current_price - range_low) / dr_span) if dr_span > 0 else 0.5
        is_gold = ('XAU' in symbol.upper() or 'GOLD' in symbol.upper())

        # ═══ 100% ЖЕСТКИЙ БЛОК ФЛЭТА (40% - 60% Equilibrium) БЕЗ ЛАЗЕЕК ═══
        if 0.40 <= dr_pct <= 0.60:
            details.append(
                f"❌ Rigid Regime Filter: Цена в центральной зоне равновесия/флэта ({dr_pct*100:.1f}% DR в коридоре 40%–60%) — 100% жесткий запрет входа!"
            )
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                details + ["Центральная зона равновесия 40%–60% (флэт / Equilibrium) — вход жестко заблокирован"]
            )

        if df_d1 is None:
            if str(timeframe).upper() == "D1":
                df_d1 = df
            else:
                try:
                    from market.data_fetcher import DataFetcher
                    clean_sym = symbol.upper().replace("/", "").replace("=X", "")
                    df_d1 = DataFetcher()._read_mt5_bars(clean_sym, "D1", limit=100)
                except Exception as d1_err:
                    logger.debug("Could not read D1 bars for %s: %s", symbol, d1_err)

        d1_trend, d1_reason = ("NEUTRAL", "")
        if df_d1 is not None and len(df_d1) >= 2:
            d1_trend, d1_reason = self._check_d1_trend(df_d1, symbol)

        # ═══ ПЕРЕПИСАННАЯ ЛОГИКА ВХОДА НА ОТКАТЕ (DISCOUNT / PREMIUM PULLBACK) ═══
        # Бот фиксирует:
        # (1) на D1/H4 был бычий импульс (d1_trend != 'DOWN' и структура/свинги вверх)
        # (2) сейчас цена откатила вниз в зону Discount (< 45% DR) в зону Order Block / FVG
        # (3) на H1 появилась разворотная свеча / слом вверх
        # -> и входит по рынку прямо со дна отката (а не кидает фантомные лимитки с пика)

        # 1. Бычий импульс на D1 / H4
        bull_impulse = False
        if d1_trend != "DOWN":
            if is_bullish_structure or bull_break or strong_bull or d1_trend == "UP":
                bull_impulse = True
            elif hh1 > hl1 and (hh1 - hl1) >= 1.5 * atr:
                bull_impulse = True

        # Медвежий импульс на D1 / H4
        bear_impulse = False
        if d1_trend != "UP":
            if is_bearish_structure or bear_break or strong_bear or d1_trend == "DOWN":
                bear_impulse = True
            elif hl1 < hh1 and (hh1 - hl1) >= 1.5 * atr:
                bear_impulse = True

        ob_bull = self._find_order_block(df, "LONG", atr, current_price, symbol)
        fvg_bull = self._find_fvg(df, "LONG", atr, current_price, symbol)
        poi_bull = ob_bull or fvg_bull

        ob_bear = self._find_order_block(df, "SHORT", atr, current_price, symbol)
        fvg_bear = self._find_fvg(df, "SHORT", atr, current_price, symbol)
        poi_bear = ob_bear or fvg_bear

        # 2. Проверяем откат в Discount (< 45%) / Premium (> 55%) к POI
        in_bull_poi = False
        if poi_bull is not None and dr_pct < 0.45:
            if ob_bull and (current_price <= ob_bull[0] + 0.60 * atr and current_price >= ob_bull[1] - 0.60 * atr):
                in_bull_poi = True
            elif fvg_bull and (current_price <= fvg_bull[1] + 0.60 * atr and current_price >= fvg_bull[0] - 0.60 * atr):
                in_bull_poi = True

        in_bear_poi = False
        if poi_bear is not None and dr_pct > 0.55:
            if ob_bear and (current_price >= ob_bear[1] - 0.60 * atr and current_price <= ob_bear[0] + 0.60 * atr):
                in_bear_poi = True
            elif fvg_bear and (current_price >= fvg_bear[0] - 0.60 * atr and current_price <= fvg_bear[1] + 0.60 * atr):
                in_bear_poi = True

        pullback_direction = None
        ltf_reversal_confirmed = False

        if bull_impulse and in_bull_poi and dr_pct < 0.45:
            # (3) На H1 проверяем появление разворотной свечи / слома вверх
            ltf_res = self.check_ltf_confirmation(
                symbol, "LONG", h1_poi_zone=poi_bull,
                timeframe="H1" if is_gold else "M15", df_ltf=df_ltf
            )
            if ltf_res.get("details"):
                details.extend(ltf_res["details"])
            if ltf_res.get("mss_confirmed"):
                pullback_direction = "LONG"
                ltf_reversal_confirmed = True
                details.append("⚡ Институциональный сетап: бычий импульс D1/H4 + откат в Discount (<45% DR) к POI + разворот на H1 — вход BUY по рынку со дна!")
            else:
                details.append("⏳ Ожидание разворотной свечи / слома вверх на H1 со дна отката — отложенный ордер не выставляется")

        elif bear_impulse and in_bear_poi and dr_pct > 0.55:
            # (3) На H1 проверяем появление разворотной свечи / слома вниз
            ltf_res = self.check_ltf_confirmation(
                symbol, "SHORT", h1_poi_zone=poi_bear,
                timeframe="H1" if is_gold else "M15", df_ltf=df_ltf
            )
            if ltf_res.get("details"):
                details.extend(ltf_res["details"])
            if ltf_res.get("mss_confirmed"):
                pullback_direction = "SHORT"
                ltf_reversal_confirmed = True
                details.append("⚡ Институциональный сетап: медвежий импульс D1/H4 + откат в Premium (>55% DR) к POI + разворот на H1 — вход SELL по рынку с вершины!")
            else:
                details.append("⏳ Ожидание разворотной свечи / слома вниз на H1 с пика отката — отложенный ордер не выставляется")

        if pullback_direction:
            direction = pullback_direction
            sub_signals += 2
        elif is_gold:
            # Для золота строго требуются только честные входы на откате со дна/вершины
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                details + ["Золото: вход вне подтвержденной зоны отката запрещен"]
            )
        else:
            # Для других пар: если нет отката, проверяем Wick Sweep строго в Discount/Premium
            if bear_wick_sweep and not bull_wick_sweep and dr_pct < 0.45:
                direction = "LONG"
                sub_signals += 1
                details.append(f"⚡ Разворотный триггер: Снятие ликвидности с минимума (Wick Sweep) в Discount — приоритет BUY")
            elif bull_wick_sweep and not bear_wick_sweep and dr_pct > 0.55:
                direction = "SHORT"
                sub_signals += 1
                details.append(f"⚡ Разворотный триггер: Снятие ликвидности с максимума (Wick Sweep) в Premium — приоритет SELL")
            else:
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Неопределённая структура рынка / вход вне Discount/Premium — пропуск"]
                )

        if direction == "NEUTRAL":
            return self._make_result(StrategySignal(direction="NEUTRAL"), details)

        # ═══ ТРЕНДОВЫЙ ФИЛЬТР D1 (СТРОГО ПО ТРЕНДУ) ═══
        if df_d1 is not None and len(df_d1) >= 2:
            if direction == "LONG" and d1_trend == "DOWN":
                details.append(f"❌ D1 Trend Filter: Покупки запрещены — {d1_reason}")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Старший таймфрейм D1 нисходящий — покупки против глобального тренда заблокированы"]
                )
            elif direction == "SHORT" and d1_trend == "UP":
                details.append(f"❌ D1 Trend Filter: Продажи запрещены — {d1_reason}")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Старший таймфрейм D1 восходящий — продажи против глобального тренда заблокированы"]
                )
            elif d1_trend:
                details.append(f"✅ D1 Trend Filter согласован: {d1_reason}")

        # ═══ ФРАКТАЛЬНАЯ МОДЕЛЬ (HTF Narrative: Draw on Liquidity) ═══
        dol = self.calculate_draw_on_liquidity(df_d1, current_price, symbol)
        if dol.get("details"):
            details.extend(dol["details"])

        sweep_pdh = bool(dol.get("pdh_swept"))
        sweep_pdl = bool(dol.get("pdl_swept"))
        sweep_judas = bool(judas.get("sweep_type"))
        sweep_wick = bool(bull_wick_sweep or bear_wick_sweep)
        recent_12 = df.iloc[-min(12, len(df)):]
        local_low_swept = bool(direction == "LONG" and dr_pct < 0.45 and float(recent_12['low'].min()) <= hl1 * 1.002)
        local_high_swept = bool(direction == "SHORT" and dr_pct > 0.55 and float(recent_12['high'].max()) >= hh1 * 0.998)

        has_liquidity_sweep = bool(sweep_judas or sweep_wick or local_low_swept or local_high_swept or sweep_pdl or sweep_pdh)
        if has_liquidity_sweep:
            details.append(f"⚡ Подтверждено снятие ликвидности в рабочей зоне (Wick/Local: {sweep_wick or local_low_swept or local_high_swept}, Judas: {sweep_judas})")

        # ═══ 100% ЖЕСТКИЙ БЛОК ДИРЕКЦИИ ОТНОСИТЕЛЬНО DEALING RANGE БЕЗ ЛАЗЕЕК ═══
        if direction == "LONG":
            if dr_pct >= 0.45:
                details.append(
                    f"❌ Rigid Regime Filter: Покупка (LONG) разрешена ТОЛЬКО на дне в зоне Discount (< 45% DR). "
                    f"Текущая позиция: {dr_pct*100:.1f}% DR >= 45% — покупка на пике / в середине жестко заблокирована!"
                )
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Покупка отклонена: цена >= 45% DR (вне зоны Discount < 45%)"]
                )
            sub_signals += 1
            details.append(
                f"✅ Матрица ценности: Покупка строго на дне в зоне Discount "
                f"({dr_pct*100:.1f}% DR < 45%, ниже Eq {self._format_price(equilibrium, symbol)})"
            )

        elif direction == "SHORT":
            if dr_pct <= 0.55:
                details.append(
                    f"❌ Rigid Regime Filter: Продажа (SHORT) разрешена ТОЛЬКО на пике в зоне Premium (> 55% DR). "
                    f"Текущая позиция: {dr_pct*100:.1f}% DR <= 55% — продажа на дне / в середине жестко заблокирована!"
                )
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Продажа отклонена: цена <= 55% DR (вне зоны Premium > 55%)"]
                )
            sub_signals += 1
            details.append(
                f"✅ Матрица ценности: Продажа строго на пике в зоне Premium "
                f"({dr_pct*100:.1f}% DR > 55%, выше Eq {self._format_price(equilibrium, symbol)})"
            )

        # ═══ ФИЛЬТР КОНСОЛИДАЦИИ И МЕРТВОГО РЫНКА (ANTI-CHOP / АНТИ-ЗАМОРОЗКА) ═══
        last_5_candles = df.iloc[-5:]
        avg_range_5 = float((last_5_candles['high'] - last_5_candles['low']).mean())
        if avg_range_5 < 0.30 * atr:
            details.append("⚠️ Рынок замер на месте (свечи микроскопические < 0.5 ATR) — сигнал пропущен")
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                details + ["Мертвая консолидация: нет волатильности для быстрого движения к цели"]
            )

        # Volume Confirmation
        vol_penalty = 0
        if (bos_found or choch_found) and 'volume' in df.columns:
            vol_sma = df['volume'].rolling(20).mean().iloc[-1]
            if pd.notna(vol_sma) and vol_sma > 0:
                if df['volume'].iloc[-1] < 0.8 * vol_sma:
                    vol_penalty = 1
                    details.append("Низкий объем на пробое (слабое подтверждение)")
                else:
                    sub_signals += 1
                    details.append("Высокий объем подтверждает движение")

        # 4. Liquidity Sweep Detection (Снятие ликвидности / Judas Swing)
        lookback_sweep = min(8, len(df))
        recent_candles = df.iloc[-lookback_sweep:]
        if direction == "LONG":
            for idx in range(len(recent_candles)):
                c_low = float(recent_candles['low'].iloc[idx])
                c_close = float(recent_candles['close'].iloc[idx])
                if c_low < hl1 and c_close > hl1:
                    sub_signals += 1
                    details.append(f"⚡ Снятие ликвидности (Liquidity Sweep): ложный прокол {self._format_price(hl1, symbol)} с резким возвратом")
                    break
        elif direction == "SHORT":
            for idx in range(len(recent_candles)):
                c_high = float(recent_candles['high'].iloc[idx])
                c_close = float(recent_candles['close'].iloc[idx])
                if c_high > hh1 and c_close < hh1:
                    sub_signals += 1
                    details.append(f"⚡ Снятие ликвидности (Liquidity Sweep): ложный прокол {self._format_price(hh1, symbol)} с резким возвратом")
                    break

        # ═══ УЛУЧШЕНИЕ 6: Inducement Detection ═══
        if self._detect_inducement(df, swing_lows, swing_highs, direction):
            sub_signals += 1
            details.append("🪤 Inducement: снятие мелких стопов перед разворотом (усиление сигнала)")

        # ═══ УЛУЧШЕНИЕ 1: Order Block — поиск с КОНЦА (newest first) + проверка митигации ═══
        ob_zone: Optional[Tuple[float, float]] = None
        lookback_ob = min(30, len(df) - 2)
        # Ищем с конца — самый свежий OB первым
        for i in range(len(df) - 3, max(len(df) - lookback_ob - 2, 0), -1):
            is_bull_next = df['close'].iloc[i + 1] > df['open'].iloc[i + 1]
            is_bear_next = df['close'].iloc[i + 1] < df['open'].iloc[i + 1]
            impulse = abs(df['close'].iloc[i + 1] - df['open'].iloc[i + 1])

            if impulse > 0.5 * atr:
                if df['close'].iloc[i] < df['open'].iloc[i] and is_bull_next and direction == "LONG":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if current_price >= ob_low:
                        # ═══ УЛУЧШЕНИЕ 3: Проверка митигации ═══
                        if not self._is_ob_mitigated(df, i, ob_high, ob_low, direction):
                            ob_zone = (ob_high, ob_low)
                            sub_signals += 1
                            details.append(f"Бычий Order Block (свежий): {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                            break
                elif df['close'].iloc[i] > df['open'].iloc[i] and is_bear_next and direction == "SHORT":
                    ob_high, ob_low = float(df['high'].iloc[i]), float(df['low'].iloc[i])
                    if current_price <= ob_high:
                        if not self._is_ob_mitigated(df, i, ob_high, ob_low, direction):
                            ob_zone = (ob_high, ob_low)
                            sub_signals += 1
                            details.append(f"Медвежий Order Block (свежий): {self._format_price(ob_low, symbol)} — {self._format_price(ob_high, symbol)}")
                            break

        # FVG Detection — reversed + mitigation check + minimum gap threshold
        fvg_zone: Optional[Tuple[float, float]] = None
        lookback_fvg = min(20, len(df) - 3)
        min_fvg_gap_abs = 0.030 if 'JPY' in symbol else (0.30 if 'XAU' in symbol else 0.00030)
        min_fvg_gap = max(min_fvg_gap_abs, 0.35 * atr)

        for i in range(len(df) - 2, max(len(df) - lookback_fvg - 1, 1), -1):
            middle_body = abs(df['close'].iloc[i] - df['open'].iloc[i])
            if middle_body < 0.5 * atr:
                continue

            if direction == "LONG" and df['close'].iloc[i] > df['open'].iloc[i]:
                if df['low'].iloc[i + 1] > df['high'].iloc[i - 1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i - 1]), float(df['low'].iloc[i + 1])
                    if (fvg_high - fvg_low) < min_fvg_gap:
                        continue  # Отсекаем микро-шум < 3-4 пипсов
                    if current_price >= fvg_low:
                        if not self._is_fvg_mitigated(df, i, fvg_low, fvg_high, direction):
                            sub_signals += 1
                            fvg_zone = (fvg_low, fvg_high)
                            details.append(f"Бычий FVG (свежий): {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                            break
            elif direction == "SHORT" and df['close'].iloc[i] < df['open'].iloc[i]:
                if df['high'].iloc[i + 1] < df['low'].iloc[i - 1]:
                    fvg_low, fvg_high = float(df['high'].iloc[i + 1]), float(df['low'].iloc[i - 1])
                    if (fvg_high - fvg_low) < min_fvg_gap:
                        continue  # Отсекаем микро-шум < 3-4 пипсов
                    if current_price <= fvg_high:
                        if not self._is_fvg_mitigated(df, i, fvg_low, fvg_high, direction):
                            sub_signals += 1
                            fvg_zone = (fvg_low, fvg_high)
                            details.append(f"Медвежий FVG (свежий): {self._format_price(fvg_low, symbol)} — {self._format_price(fvg_high, symbol)}")
                            break

        # OTE (Optimal Trade Entry) — Fibonacci 0.618-0.705
        impulse_high = max(hh1, float(df['high'].iloc[-min(15, len(df)):].max()))
        impulse_low = min(hl1, float(df['low'].iloc[-min(15, len(df)):].min()))
        diff = impulse_high - impulse_low

        if diff <= 0:
            diff = atr * 2.0
            impulse_high = current_price + atr
            impulse_low = current_price - atr

        ote_entry = None
        if direction == "LONG":
            fib_0618 = impulse_high - 0.618 * diff
            fib_0705 = impulse_high - 0.705 * diff
            if fib_0705 <= current_price <= fib_0618:
                sub_signals += 1
                ote_entry = fib_0618
                details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0705, symbol)} — {self._format_price(fib_0618, symbol)}")
        else:
            fib_0618 = impulse_low + 0.618 * diff
            fib_0705 = impulse_low + 0.705 * diff
            if fib_0618 <= current_price <= fib_0705:
                sub_signals += 1
                ote_entry = fib_0618
                details.append(f"Зона OTE (0.618-0.705): {self._format_price(fib_0618, symbol)} — {self._format_price(fib_0705, symbol)}")

        # ═══ УЛУЧШЕНИЕ 8: Kill Zone awareness ═══
        in_kill_zone = self._is_in_kill_zone(current_time)
        if in_kill_zone:
            details.append("⚡ Активна Kill Zone — сигнал усилен")

        # ═══ УЛУЧШЕНИЕ 7: Минимум подтверждений для входа ═══
        # Без Kill Zone: минимум 3 подтверждения
        # В Kill Zone: минимум 2 подтверждения (снайперские входы в пик ликвидности)
        final_confidence = max(1, sub_signals - vol_penalty)
        min_required = 2 if in_kill_zone else 3

        if final_confidence < min_required:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=final_confidence,
                              details=details + [f"Недостаточно подтверждений ({final_confidence}<{min_required})"]),
                details + ["Слабый сетап, остаемся в стороне"]
            )

        confidence_stars = min(5, final_confidence)
        # Kill Zone бонус: +1 звезда
        if in_kill_zone and confidence_stars < 5:
            confidence_stars += 1

        # ═══ ВРЕМЕННЫЕ ЦИКЛЫ IPDA: ICT MACRO WINDOWS ═══
        in_macro, macro_name = config.is_in_macro_window(current_time)
        if in_macro:
            details.append(f"⚡ ICT Macro Window: Вход в окне максимальной межбанковской алгоритмической ликвидности ({macro_name}) — повышенная точность")
            if confidence_stars < 5:
                confidence_stars += 1

        # ═══ ФРАКТАЛЬНАЯ МОДЕЛЬ (HTF Narrative: Draw on Liquidity) ═══
        # (DOL уже рассчитан выше перед Regime Filter)

        # ═══ ИНТЕРМАРКЕТ-АНАЛИЗ И SMT DIVERGENCE (Smart Money Tool) ═══
        smt = self.check_smt_divergence(symbol, direction, timeframe=timeframe, df_primary=df)
        if smt.get("details"):
            details.extend(smt["details"])

        if smt.get("smt_confirmed"):
            sub_signals += 1
            details.append(f"✅ SMT Confluence ({smt['smt_type']}): институциональное межрыночное согласование подтверждено")
            if smt.get("leading_trigger"):
                details.append("⚡ Опережающий SMT триггер: фиксация опережающего снятия ликвидности!")
        else:
            if is_gold:
                details.append("⚡ SMT: Для XAUUSD межрыночный SMT автономный (без принудительного понижения из-за DXY)")
            else:
                confidence_stars = min(confidence_stars, 3)
                details.append("⚠️ SMT Divergence не подтверждена: вес сетапа понижен до 3★ (авто-вход заблокирован, требуется 4★+)")

        # ═══ ФРАКТАЛЬНАЯ МОДЕЛЬ ВХОДА (Layer 3: LTF Trigger MSS + Displacement) ═══
        if direction == "LONG":
            if ob_bull and not ob_zone:
                ob_zone = ob_bull
            if fvg_bull and not fvg_zone:
                fvg_zone = fvg_bull
        elif direction == "SHORT":
            if ob_bear and not ob_zone:
                ob_zone = ob_bear
            if fvg_bear and not fvg_zone:
                fvg_zone = fvg_bear

        poi_zone = ob_zone or fvg_zone
        if pullback_direction:
            ltf_confirmed = True
            details.append("✅ Фрактальная модель (Layer 3): Вход на откате подтвержден на H1 со дна/вершины!")
        else:
            ltf_confirmed = True
            if poi_zone is not None and str(timeframe).upper() in ("H1", "H4", "D1"):
                ltf_res = self.check_ltf_confirmation(symbol, direction, h1_poi_zone=poi_zone, timeframe="H1" if is_gold else "M15", df_ltf=df_ltf)
                if ltf_res.get("details"):
                    details.extend(ltf_res["details"])
                if not ltf_res.get("mss_confirmed", True):
                    ltf_confirmed = False
                    confidence_stars = min(confidence_stars, 3)
                    details.append("⏳ LTF Confirmation: Ожидание слома структуры (MSS с Displacement) на M15/M5 — слепой отложенный ордер запрещен, вес сетапа понижен до 3★")
                else:
                    details.append("✅ Фрактальная модель (Layer 3): Паттерн подтверждения MSS с Displacement на M15/M5 подтвержден!")

        # ── Институциональный минимальный размер стоп-лосса (Анатомический SL) ──
        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
        if is_gold:
            pip_unit = 0.10
            # Адаптивный диапазон стоп-лосса для золота: $10.00 - $35.00
            min_sl_dist = max(10.0, 0.8 * atr)
            min_sl_dist = min(min_sl_dist, 35.0)
            max_sl_dist = min(35.0, max(25.0, 2.5 * atr))
            sl_buffer = max(0.80, min(1.20, 0.15 * atr))
        elif 'JPY' in sym_clean:
            pip_unit = 0.01
            abs_min_sl = 0.075  # ~7.5 пипсов для JPY пар
            min_sl_dist = max(abs_min_sl, 0.65 * atr)
            max_sl_dist = 0.85
            sl_buffer = max(2.5 * pip_unit, 0.25 * atr)
        elif 'BTC' in sym_clean or 'ETH' in sym_clean:
            pip_unit = 1.0
            abs_min_sl = 50.0
            min_sl_dist = max(abs_min_sl, 0.65 * atr)
            max_sl_dist = 1500.0
            sl_buffer = max(2.5 * pip_unit, 0.25 * atr)
        else:
            pip_unit = 0.0001
            abs_min_sl = 0.00075  # ~7.5 пипсов для долларовых мажоров
            min_sl_dist = max(abs_min_sl, 0.65 * atr)
            max_sl_dist = 0.0065
            sl_buffer = max(2.5 * pip_unit, 0.25 * atr)

        max_entry_dist = 2.5 * atr

        if direction == "LONG":
            candidates = []
            if ote_entry:
                if fib_0705 <= current_price <= fib_0618:
                    candidates.append(current_price)
                elif ote_entry < current_price and (current_price - ote_entry) <= max_entry_dist:
                    candidates.append(ote_entry)
            if ob_zone:
                # Вход на вершине бычьего OB (первое касание) или 50%
                ob_entry = ob_zone[0] if ob_zone[0] < current_price else (ob_zone[0] + ob_zone[1]) / 2
                if ob_entry < current_price and (current_price - ob_entry) <= max_entry_dist:
                    candidates.append(ob_entry)
                elif ob_zone[1] < current_price and (current_price - ob_zone[1]) <= max_entry_dist:
                    candidates.append(ob_zone[1])
            # 50% Consequent Encroachment для FVG в LONG
            if fvg_zone:
                fvg_ce = fvg_zone[1] - 0.5 * (fvg_zone[1] - fvg_zone[0])
                if fvg_ce < current_price and (current_price - fvg_ce) <= max_entry_dist:
                    candidates.append(fvg_ce)

            if pullback_direction or is_gold:
                entry = current_price
                order_type = "BUY_MARKET"
                candidates = [current_price]
            else:
                # Если цена находится ПРЯМО В ЗОНЕ Order Block или FVG — вход моментальный
                if not candidates and ob_zone and ob_zone[1] <= current_price <= ob_zone[0] + 0.35 * atr:
                    candidates.append(current_price)
                if not candidates and fvg_zone and fvg_zone[0] <= current_price <= fvg_zone[1] + 0.35 * atr:
                    candidates.append(current_price)

                if not candidates:
                    return self._make_result(
                        StrategySignal(direction="NEUTRAL", confidence=0,
                                      details=details + ["Зона входа слишком далеко от текущей цены (>2.5 ATR)"]),
                        details + ["Цена ушла слишком далеко от институциональной зоны — сетап пропущен"]
                    )
                entry = max(candidates)
                if ltf_confirmed:
                    entry = current_price
                    order_type = "BUY_MARKET"
                else:
                    order_type = "BUY_LIMIT"
                    confidence_stars = min(confidence_stars, 4)

            # Анатомический структурный SL за OB low / FVG low / swing low + буфер
            base_sl = ob_zone[1] if ob_zone else (fvg_zone[0] if fvg_zone else hl1)
            if is_gold:
                sl_buffer = max(0.80, min(1.20, 0.15 * atr))
            else:
                sl_buffer = max(2.5 * pip_unit, 0.25 * atr)
            sl = base_sl - sl_buffer
            if (entry - sl) < min_sl_dist:
                sl = entry - min_sl_dist

            entry_dr_pct = ((entry - range_low) / dr_span) if dr_span > 0 else 0.5
            if 0.40 <= entry_dr_pct <= 0.60:
                details.append(f"❌ Вход в центральной зоне флэта ({entry_dr_pct*100:.1f}% DR в 40%–60%) — заблокировано")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Вход в 40%–60% Equilibrium жестко заблокирован"]
                )
            if entry_dr_pct >= 0.45:
                details.append(f"❌ Покупка >= 45% DR ({entry_dr_pct*100:.1f}%) — заблокировано")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Покупка >= 45% DR жестко заблокирована"]
                )

            # Гарантия адаптивного диапазона SL для золота ($10.00 - $32.50)
            if is_gold:
                sl_dist = entry - sl
                if sl_dist < 10.0:
                    sl = entry - 10.0
                elif sl_dist > 32.50:
                    sl = entry - 32.50

            risk = entry - sl

            # ═══ TP на ликвидности: Swing R:R >= 1:2.5 (цели +$40 до +$120) ═══
            if is_gold:
                tp_buffer = max(0.50, min(1.00, 0.05 * atr))
                liq_target = self._find_liquidity_target(swing_highs, swing_lows, direction, entry, sl, df_d1=df_d1)
                if liq_target and (liq_target - tp_buffer - entry) >= 2.5 * risk:
                    tp1 = liq_target - tp_buffer
                    tp2 = entry + max(3.5 * risk, (tp1 - entry) * 1.35)
                    details.append(f"🎯 TP1 на институциональном пуле ликвидности (Swing High/PDH): {self._format_price(tp1, symbol)} (R:R {((tp1-entry)/risk):.1f})")
                else:
                    tp1 = entry + 3.0 * risk
                    tp2 = entry + 4.0 * risk
                    details.append(f"🎯 TP1 на расчетном институциональном свинг-таргете (3.0R): {self._format_price(tp1, symbol)}")
            else:
                tp_buffer = max(3.5 * pip_unit, 0.12 * atr)
                adx_low = bool('adx' in df.columns and pd.notna(df['adx'].iloc[-1]) and df['adx'].iloc[-1] < 25.0)
                if confidence_stars >= 5 and not adx_low:
                    target_mult = 2.0 if in_kill_zone else 1.8
                elif confidence_stars >= 5 or (not adx_low and in_kill_zone):
                    target_mult = 1.65
                else:
                    target_mult = 1.5
                liq_target = self._find_liquidity_target(swing_highs, swing_lows, direction, entry, sl)
                if judas_target and judas_target > entry + 1.2 * risk:
                    tp1 = max(entry + 1.5 * risk, judas_target - tp_buffer)
                    tp2 = max(tp1 + 1.0 * risk, entry + 2.5 * risk)
                elif liq_target:
                    tp1 = max(entry + 1.5 * risk, liq_target - tp_buffer)
                    tp2 = max(tp1 + 1.0 * risk, entry + 2.5 * risk)
                else:
                    tp1 = entry + target_mult * risk
                    tp2 = entry + max(2.5, target_mult + 0.8) * risk

        else:  # SHORT
            candidates = []
            if ote_entry:
                if fib_0618 <= current_price <= fib_0705:
                    candidates.append(current_price)
                elif ote_entry > current_price and (ote_entry - current_price) <= max_entry_dist:
                    candidates.append(ote_entry)
            if ob_zone:
                # Вход на нижней границе медвежьего OB (первое касание) или 50%
                ob_entry = ob_zone[1] if ob_zone[1] > current_price else (ob_zone[0] + ob_zone[1]) / 2
                if ob_entry > current_price and (ob_entry - current_price) <= max_entry_dist:
                    candidates.append(ob_entry)
                elif ob_zone[0] > current_price and (ob_zone[0] - current_price) <= max_entry_dist:
                    candidates.append(ob_zone[0])
            # 50% Consequent Encroachment для FVG в SHORT (вместо продажи на самом дне fvg_zone[0])
            if fvg_zone:
                fvg_ce = fvg_zone[0] + 0.5 * (fvg_zone[1] - fvg_zone[0])
                if fvg_ce > current_price and (fvg_ce - current_price) <= max_entry_dist:
                    candidates.append(fvg_ce)

            if pullback_direction or is_gold:
                entry = current_price
                order_type = "SELL_MARKET"
                candidates = [current_price]
            else:
                # Если цена находится ПРЯМО В ЗОНЕ Order Block или FVG — вход моментальный
                if not candidates and ob_zone and ob_zone[1] - 0.35 * atr <= current_price <= ob_zone[0]:
                    candidates.append(current_price)
                if not candidates and fvg_zone and fvg_zone[0] - 0.35 * atr <= current_price <= fvg_zone[1]:
                    candidates.append(current_price)

                if not candidates:
                    return self._make_result(
                        StrategySignal(direction="NEUTRAL", confidence=0,
                                      details=details + ["Зона входа слишком далеко от текущей цены (>2.5 ATR)"]),
                        details + ["Цена ушла слишком далеко от институциональной зоны — сетап пропущен"]
                    )
                entry = min(candidates)
                if ltf_confirmed:
                    entry = current_price
                    order_type = "SELL_MARKET"
                else:
                    order_type = "SELL_LIMIT"
                    confidence_stars = min(confidence_stars, 4)

            # Анатомический структурный SL за OB high / FVG high / swing high + буфер
            base_sl = ob_zone[0] if ob_zone else (fvg_zone[1] if fvg_zone else hh1)
            if is_gold:
                sl_buffer = max(0.80, min(1.20, 0.15 * atr))
            else:
                sl_buffer = max(2.5 * pip_unit, 0.25 * atr)
            sl = base_sl + sl_buffer
            if (sl - entry) < min_sl_dist:
                sl = entry + min_sl_dist

            entry_dr_pct = ((entry - range_low) / dr_span) if dr_span > 0 else 0.5
            if 0.40 <= entry_dr_pct <= 0.60:
                details.append(f"❌ Вход в центральной зоне флэта ({entry_dr_pct*100:.1f}% DR в 40%–60%) — заблокировано")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Вход в 40%–60% Equilibrium жестко заблокирован"]
                )
            if entry_dr_pct <= 0.55:
                details.append(f"❌ Продажа <= 55% DR ({entry_dr_pct*100:.1f}%) — заблокировано")
                return self._make_result(
                    StrategySignal(direction="NEUTRAL", confidence=0, details=details),
                    details + ["Продажа <= 55% DR жестко заблокирована"]
                )

            # Гарантия адаптивного диапазона SL для золота ($10.00 - $32.50)
            if is_gold:
                sl_dist = sl - entry
                if sl_dist < 10.0:
                    sl = entry + 10.0
                elif sl_dist > 32.50:
                    sl = entry + 32.50

            risk = sl - entry

            # ═══ TP на ликвидности: Swing R:R >= 1:2.5 (цели +$40 до +$120) ═══
            if is_gold:
                tp_buffer = max(0.50, min(1.00, 0.05 * atr))
                liq_target = self._find_liquidity_target(swing_highs, swing_lows, direction, entry, sl, df_d1=df_d1)
                if liq_target and (entry - (liq_target + tp_buffer)) >= 2.5 * risk:
                    tp1 = liq_target + tp_buffer
                    tp2 = entry - max(3.5 * risk, (entry - tp1) * 1.35)
                    details.append(f"🎯 TP1 на институциональном пуле ликвидности (Swing Low/PDL): {self._format_price(tp1, symbol)} (R:R {((entry-tp1)/risk):.1f})")
                else:
                    tp1 = entry - 3.0 * risk
                    tp2 = entry - 4.0 * risk
                    details.append(f"🎯 TP1 на расчетном институциональном свинг-таргете (3.0R): {self._format_price(tp1, symbol)}")
            else:
                tp_buffer = max(3.5 * pip_unit, 0.12 * atr)
                adx_low = bool('adx' in df.columns and pd.notna(df['adx'].iloc[-1]) and df['adx'].iloc[-1] < 25.0)
                if confidence_stars >= 5 and not adx_low:
                    target_mult = 2.0 if in_kill_zone else 1.8
                elif confidence_stars >= 5 or (not adx_low and in_kill_zone):
                    target_mult = 1.65
                else:
                    target_mult = 1.5
                liq_target = self._find_liquidity_target(swing_highs, swing_lows, direction, entry, sl)
                if judas_target and judas_target < entry - 1.2 * risk:
                    tp1 = min(entry - 1.5 * risk, judas_target + tp_buffer)
                    tp2 = min(tp1 - 1.0 * risk, entry - 2.5 * risk)
                elif liq_target:
                    tp1 = min(entry - 1.5 * risk, liq_target + tp_buffer)
                    tp2 = min(tp1 - 1.0 * risk, entry - 2.5 * risk)
                else:
                    tp1 = entry - target_mult * risk
                    tp2 = entry - max(2.5, target_mult + 0.8) * risk

        risk = abs(entry - sl)

        # ── Институциональный лимит максимального стоп-лосса (Outlier Risk Protection) ──
        if is_gold:
            max_sl_limit = 32.50  # Максимум $32.50 для золота (жесткий потолок риска <= $65.00 при min 0.02 лота)
        elif 'JPY' in sym_clean:
            max_sl_limit = 0.85
        elif 'BTC' in sym_clean:
            max_sl_limit = 1500.0
        elif 'ETH' in sym_clean:
            max_sl_limit = 100.0
        else:
            max_sl_limit = 0.0065

        if risk > max_sl_limit:
            return self._make_result(
                StrategySignal(
                    direction="NEUTRAL",
                    confidence=0,
                    details=details + [f"Стоп-лосс слишком широкий ({risk:.2f} > max {max_sl_limit:.2f}) — аномальный риск"]
                ),
                details + [f"Сетап отброшен: риск по стоп-лоссу превышает институциональный лимит безопасности ({max_sl_limit:.2f})"]
            )

        reward_1 = abs(tp1 - entry)
        rr1 = reward_1 / risk if risk > 0 else 0.0

        min_rr = 2.5 if is_gold else 1.5
        if round(rr1, 2) < min_rr:
            return self._make_result(
                StrategySignal(direction="NEUTRAL", confidence=0, details=[f"R:R 1:{rr1:.2f} < 1:{min_rr:.1f} — отброшен"]),
                [f"Сетап не соответствует критерию Swing R:R >= 1:{min_rr:.1f}"]
            )

        # ═══ 4-ФАКТОРНАЯ ОЦЕНКА ЗВЁЗД ДЛЯ XAUUSD ═══
        if is_gold:
            # 1. Направление D1/H4
            f1_trend = (d1_trend == "UP" if direction == "LONG" else (d1_trend == "DOWN" if direction == "SHORT" else False)) if ('d1_trend' in locals() and d1_trend and d1_trend != 'NEUTRAL') else True
            # 2. Снятие ликвидности (Sweep)
            f2_sweep = has_liquidity_sweep
            # 3. Импульсный возврат с имбалансом (Displacement + FVG/OB)
            f3_poi = bool((ob_zone is not None or fvg_zone is not None or ote_entry is not None or (dr_pct < 0.40 if direction == 'LONG' else dr_pct > 0.60)) and ltf_confirmed)
            # 4. Запас хода до цели >= 2.5R
            f4_headroom = bool(rr1 >= 2.5)

            aligned_factors = sum([1 for f in [f1_trend, f2_sweep, f3_poi, f4_headroom] if f])
            if aligned_factors >= 4:
                confidence_stars = 4
                if in_kill_zone or in_macro or (ote_entry is not None):
                    confidence_stars = 5
                details.append(f"⭐ Институциональная оценка XAUUSD: 4/4 фактора подтверждены ({confidence_stars}★)")
            elif aligned_factors == 3:
                confidence_stars = 3
                details.append("ℹ️ Оценка XAUUSD: 3/4 фактора подтверждены (3★, только ручной мониторинг)")
            else:
                confidence_stars = min(2, aligned_factors)
                details.append(f"ℹ️ Оценка XAUUSD: {aligned_factors}/4 факторов (слабый сетап {confidence_stars}★)")

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
        if 'entry_dr_pct' in locals() and entry_dr_pct is not None:
            signal.dr_pct = round(entry_dr_pct, 4)

        return self._make_result(signal, details)