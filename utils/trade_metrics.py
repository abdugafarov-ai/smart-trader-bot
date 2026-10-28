"""
Smart Trader Bot — Trade Metrics & Accounting Calculator.
Модуль для точного институционального расчёта торговых метрик:
- Плановый R:R и фактический R
- Чистый PnL и торговые издержки (комиссия, своп, проскальзывание)
- MAE (Maximum Adverse Excursion) и MFE (Maximum Favorable Excursion)
- Длительность удержания позиции
- Определение торговой сессии (DST-aware)
"""

import math
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
import pandas as pd

logger = logging.getLogger(__name__)


def calculate_planned_rr(
    entry: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    direction: Optional[str] = None,
) -> Optional[float]:
    """
    Вычисляет плановое соотношение Risk-to-Reward (R:R).
    Плановый R:R = |TP - Entry| / |Entry - SL|
    """
    if entry is None or stop_loss is None or take_profit is None:
        return None
    try:
        e = float(entry)
        sl = float(stop_loss)
        tp = float(take_profit)
        risk = abs(e - sl)
        reward = abs(tp - e)
        if risk <= 1e-9:
            return None
        return round(reward / risk, 2)
    except (ValueError, TypeError):
        return None


def calculate_actual_r(
    entry: Optional[float] = None,
    close: Optional[float] = None,
    initial_sl: Optional[float] = None,
    direction: str = "BUY",
    net_pnl: Optional[float] = None,
    risk_usd: Optional[float] = None,
    entry_price: Optional[float] = None,
    close_price: Optional[float] = None,
) -> Optional[float]:
    """
    Вычисляет фактический коэффициент R (реализованный R-multiple):
    - Если известен риск в USD и чистый PnL: actual_r = net_pnl / risk_usd
    - Иначе по ценовой дистанции: move / risk_dist (с учётом направления)
    """
    try:
        if entry is None and entry_price is not None:
            entry = entry_price
        if close is None and close_price is not None:
            close = close_price

        if net_pnl is not None and risk_usd is not None and float(risk_usd) > 0.01:
            return round(float(net_pnl) / float(risk_usd), 2)

        if entry is not None and close is not None and initial_sl is not None:
            e = float(entry)
            c = float(close)
            sl = float(initial_sl)
            risk_dist = abs(e - sl)
            if risk_dist <= 1e-9:
                return None
            dir_u = str(direction).upper()
            if "BUY" in dir_u or "LONG" in dir_u:
                move = c - e
            else:
                move = e - c
            return round(move / risk_dist, 2)
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return None


def calculate_trade_costs(
    gross_profit: Optional[float],
    commission: Optional[float],
    swap: Optional[float],
    slippage: Optional[float] = None,
    reported_profit: Optional[float] = None,
    slippage_pips: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Вычисляет финансовый результат и торговые издержки:
    - Чистый PnL вычисляется ТОЛЬКО на основе действительно полученных данных.
    - Если отдельного компонента нет в телеметрии, возвращается None ("N/A"),
      а не подставляется выдуманное значение.
    """
    if slippage is None and slippage_pips is not None:
        slippage = slippage_pips

    has_gross = gross_profit is not None
    has_comm = commission is not None
    has_swap = swap is not None

    c_val = float(commission) if has_comm else None
    s_val = float(swap) if has_swap else None
    g_val = float(gross_profit) if has_gross else None
    slip_val = float(slippage) if slippage is not None else None

    if has_gross and has_comm and has_swap:
        # В MT5 комиссия обычно отрицательная (расход), своп может быть +/-
        net = g_val + c_val + s_val
        return {
            "gross_profit": round(g_val, 2),
            "commission": round(c_val, 2),
            "swap": round(s_val, 2),
            "slippage": round(slip_val, 2) if slip_val is not None else None,
            "net_pnl": round(net, 2),
            "is_complete": True,
        }
    elif reported_profit is not None:
        rep_val = float(reported_profit)
        return {
            "gross_profit": round(g_val, 2) if has_gross else None,
            "commission": round(c_val, 2) if has_comm else None,
            "swap": round(s_val, 2) if has_swap else None,
            "slippage": round(slip_val, 2) if slip_val is not None else None,
            "net_pnl": round(rep_val, 2),
            "is_complete": False,
        }
    elif has_gross:
        return {
            "gross_profit": round(g_val, 2),
            "commission": None,
            "swap": None,
            "slippage": round(slip_val, 2) if slip_val is not None else None,
            "net_pnl": round(g_val, 2),
            "is_complete": False,
        }
    else:
        return {
            "gross_profit": None,
            "commission": None,
            "swap": None,
            "slippage": round(slip_val, 2) if slip_val is not None else None,
            "net_pnl": None,
            "is_complete": False,
        }


def calculate_duration(
    open_time: Optional[datetime | str],
    close_time: Optional[datetime | str],
) -> Tuple[Optional[int], str]:
    """
    Вычисляет длительность сделки в секундах и возвращает форматированную строку (например, "1h 24m 10s").
    Если время не указано или некорректно, возвращает (None, "N/A").
    """
    if not open_time or not close_time:
        return None, "N/A"

    try:
        if isinstance(open_time, str):
            ot = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
        else:
            ot = open_time

        if isinstance(close_time, str):
            ct = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        else:
            ct = close_time

        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=timezone.utc)
        if ct.tzinfo is None:
            ct = ct.replace(tzinfo=timezone.utc)

        sec = int((ct - ot).total_seconds())
        if sec < 0:
            return None, "N/A"

        hours = sec // 3600
        minutes = (sec % 3600) // 60
        seconds = sec % 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or hours > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return sec, " ".join(parts) if parts else "0s"
    except Exception:
        return None, "N/A"


def calculate_mae_mfe(
    symbol: str,
    direction: str,
    entry_price: float,
    open_time: Optional[datetime | str] = None,
    close_time: Optional[datetime | str] = None,
    lot: float = 0.01,
    df_bars: Optional[pd.DataFrame] = None,
    bars: Optional[pd.DataFrame] = None,
    close_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Вычисляет MAE (Maximum Adverse Excursion) и MFE (Maximum Favorable Excursion).
    Если для расчёта необходимы исторические тики или бары, которых нет в системе,
    явно помечает результат как недоступный и указывает необходимые данные.
    """
    if df_bars is None and bars is not None:
        df_bars = bars
    sym_u = symbol.upper()
    pip_mult = 100.0 if "JPY" in sym_u else (10.0 if "XAU" in sym_u else 10000.0)
    contract_size = 100.0 if "XAU" in sym_u else (1000.0 if "JPY" in sym_u else 100000.0)
    dir_u = direction.upper()
    is_long = ("BUY" in dir_u or "LONG" in dir_u)

    if isinstance(open_time, (int, float)):
        ot_str = datetime.fromtimestamp(open_time, tz=timezone.utc).isoformat()
    elif isinstance(open_time, str):
        ot_str = open_time
    elif isinstance(open_time, datetime):
        ot_str = open_time.isoformat()
    else:
        ot_str = "N/A"

    if isinstance(close_time, (int, float)):
        ct_str = datetime.fromtimestamp(close_time, tz=timezone.utc).isoformat()
    elif isinstance(close_time, str):
        ct_str = close_time
    elif isinstance(close_time, datetime):
        ct_str = close_time.isoformat()
    else:
        ct_str = "N/A"

    if df_bars is None or df_bars.empty:
        return {
            "mae": None,
            "mfe": None,
            "mae_pips": None,
            "mfe_pips": None,
            "mae_usd": None,
            "mfe_usd": None,
            "status": f"UNAVAILABLE: requires historical tick/bar data for {symbol} between {ot_str} and {ct_str}",
            "is_available": False,
        }

    try:
        df = df_bars.copy()
        ts_col = "timestamp" if "timestamp" in df.columns else ("time" if "time" in df.columns else None)
        if ts_col and open_time and close_time:
            sample = df[ts_col].iloc[0] if not df.empty else None
            is_numeric = isinstance(sample, (int, float)) or (isinstance(sample, str) and sample.replace(".", "", 1).isdigit())
            unit = "s" if (is_numeric and float(sample) < 1e11) else None
            ts_series = pd.to_datetime(df[ts_col], unit=unit)
            ot_dt = pd.to_datetime(open_time, unit="s" if isinstance(open_time, (int, float)) and open_time < 1e11 else None)
            ct_dt = pd.to_datetime(close_time, unit="s" if isinstance(close_time, (int, float)) and close_time < 1e11 else None)
            mask = (ts_series >= ot_dt) & (ts_series <= ct_dt)
            period_df = df[mask]
            if period_df.empty:
                period_df = df
        else:
            period_df = df

        if period_df.empty or "high" not in period_df.columns or "low" not in period_df.columns:
            return {
                "mae": None,
                "mfe": None,
                "mae_pips": None,
                "mfe_pips": None,
                "mae_usd": None,
                "mfe_usd": None,
                "status": f"UNAVAILABLE: empty OHLC data for {symbol} in timeframe",
                "is_available": False,
            }

        max_high = float(period_df["high"].max())
        min_low = float(period_df["low"].min())
        e = float(entry_price)

        if is_long:
            # Для BUY худшая точка — минимальный Low ниже входа
            mae_dist = max(0.0, e - min_low)
            # Лучшая точка — максимальный High выше входа
            mfe_dist = max(0.0, max_high - e)
        else:
            # Для SELL худшая точка — максимальный High выше входа
            mae_dist = max(0.0, max_high - e)
            # Лучшая точка — минимальный Low ниже входа
            mfe_dist = max(0.0, e - min_low)

        mae_pips = round(mae_dist * pip_mult, 1)
        mfe_pips = round(mfe_dist * pip_mult, 1)
        mae_usd = round(mae_dist * contract_size * float(lot), 2)
        mfe_usd = round(mfe_dist * contract_size * float(lot), 2)

        return {
            "mae": round(mae_dist, 5),
            "mfe": round(mfe_dist, 5),
            "mae_pips": mae_pips,
            "mfe_pips": mfe_pips,
            "mae_usd": mae_usd,
            "mfe_usd": mfe_usd,
            "status": "AVAILABLE",
            "is_available": True,
        }
    except Exception as err:
        return {
            "mae": None,
            "mfe": None,
            "mae_pips": None,
            "mfe_pips": None,
            "mae_usd": None,
            "mfe_usd": None,
            "status": f"UNAVAILABLE: error evaluating bars ({err})",
            "is_available": False,
        }


def detect_trading_session(dt: Optional[datetime | str]) -> str:
    """
    Определяет институциональную торговую сессию (DST-aware):
    - London-NY Overlap (пиковая ликвидность)
    - London Session
    - New York Session
    - Asian Session
    - Off-hours
    """
    if dt is None:
        return "Unknown"

    try:
        if isinstance(dt, str):
            parsed_dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        else:
            parsed_dt = dt

        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        else:
            parsed_dt = parsed_dt.astimezone(timezone.utc)

        from sessions.trading_sessions import TradingSessions
        ts = TradingSessions()
        cur_time = parsed_dt.time()
        sessions_data = ts._get_dynamic_sessions_data()

        # Проверяем в приоритетном рыночном порядке:
        # 1. London-NY Overlap (пиковая ликвидность)
        for s in sessions_data:
            if s["name"] == "London-NY Overlap" and ts._is_time_between(s["start"], s["end"], cur_time):
                return "London-NY Overlap"

        # 2. London Session
        for s in sessions_data:
            if "London" in s["name"] and "Overlap" not in s["name"] and ts._is_time_between(s["start"], s["end"], cur_time):
                return "London"

        # 3. New York Session
        for s in sessions_data:
            if "New York" in s["name"] and ts._is_time_between(s["start"], s["end"], cur_time):
                return "New York"

        # 4. Asian Session
        for s in sessions_data:
            if ("Asia" in s["name"] or "Tokyo" in s["name"]) and ts._is_time_between(s["start"], s["end"], cur_time):
                return "Asian"

        h = parsed_dt.hour
        if 13 <= h < 17:
            return "London-NY Overlap"
        elif 7 <= h < 16:
            return "London"
        elif 12 <= h < 21:
            return "New York"
        elif h >= 22 or h < 7:
            return "Asian"
        return "Off-hours"
    except Exception:
        try:
            if isinstance(dt, str):
                parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            else:
                parsed = dt
            h = parsed.hour
            if 13 <= h < 17:
                return "London-NY Overlap"
            elif 7 <= h < 16:
                return "London"
            elif 12 <= h < 21:
                return "New York"
            elif h >= 22 or h < 7:
                return "Asian"
            return "Off-hours"
        except Exception:
            return "Unknown"
