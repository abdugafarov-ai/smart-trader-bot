#!/usr/bin/env python3
"""
High-Performance Quantitative Intraday Gold (XAUUSD) Research & Backtesting Engine
Strictly Intraday, Zero-Lookahead, M5/M15/H1 Multi-Timeframe Architecture
Optimized with np.searchsorted for instant O(log N) temporal alignment.

Models Tested:
- Control (No Trade): 0 trades benchmark ($5,000.00 preserved)
- Baseline Legacy: Pre-audit behavior (forced 0.02 min lot, $10.00 min SL inflation, no min-lot rejection, no Auto-BE)
- Model A: Liquidity Sweep -> Displacement -> MSS/CHOCH -> FVG Entry
- Model B: Trend Pullback -> Unmitigated Order Block in Discount/Premium -> M5 Reversal Confirmation (1 trade per OB)
- Model C: Breakout + Momentum -> Retest of Structural Flip

Complete Quantitative Protocol (Sections 19, 20, 21):
- Separate Outcomes: Full Wins (r_multiple > 0.15), Breakeven Scratches (-0.05 <= r <= 0.15), Full Losses (r < -0.05)
- Intratrade MAE & MFE (in USD and R-multiples, mean & median)
- Trade Duration (in minutes, mean & median)
- Rejected & Unfilled Order Tracking (min_lot_exceeds_risk, outside_killzone, eod_cutoff, unfilled_limit_expired)
- 3-Way Temporal Split (Train / Validation / Out-of-Sample) + Rolling Walk-Forward Analysis
- Filter Ablation Study (Full Model vs No KillZone vs No H1 Bias vs No Discount/Premium vs No Auto-BE)
- Transaction Cost & Slippage Stress Tests (Base $0.35/$0.00 vs Moderate $0.50/$0.15 vs Severe $0.75/$0.30)
- Monthly & Session Breakdowns (London KZ vs NY KZ) and LONG vs SHORT decomposition
"""

import os
import sys
import json
import math
from datetime import datetime, timezone, timedelta
from bisect import bisect_left
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

BASE_SPREAD_USD = 0.35       # $0.35 / oz ($35.00 per standard lot)
COMMISSION_PER_LOT = 3.00    # $3.00 / lot ($0.03 per 0.01 lot)
TICK_SIZE = 0.01
TICK_VALUE = 1.00            # $1.00 per 1.0 lot per 0.01 move = $100 per $1.00 move
CONTRACT_SIZE = 100.0        # 1 lot = 100 oz

INITIAL_BALANCE = 5000.0
DAILY_LOSS_LIMIT = 200.0
STATIC_FLOOR = 4400.0

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data", "gold_candles")


def load_candles():
    m5_file = os.path.join(DATA_DIR, "candles_XAUUSD_M5.csv")
    m15_file = os.path.join(DATA_DIR, "candles_XAUUSD_M15.csv")
    h1_file = os.path.join(DATA_DIR, "candles_XAUUSD_H1.csv")

    if not os.path.exists(m5_file):
        raise FileNotFoundError(f"Missing M5 candle file: {m5_file}")

    broker_tz = ZoneInfo("Europe/Helsinki")
    utc_tz = timezone.utc

    print("Loading M5 candles...", flush=True)
    df_m5 = pd.read_csv(m5_file)
    df_m5["time"] = pd.to_datetime(df_m5["timestamp"]).dt.tz_localize(broker_tz, ambiguous='NaT', nonexistent='shift_forward').dt.tz_convert(utc_tz)
    df_m5 = df_m5.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    print("Loading M15 candles...", flush=True)
    df_m15 = pd.read_csv(m15_file)
    df_m15["time"] = pd.to_datetime(df_m15["timestamp"]).dt.tz_localize(broker_tz, ambiguous='NaT', nonexistent='shift_forward').dt.tz_convert(utc_tz)
    df_m15 = df_m15.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    print("Loading H1 candles...", flush=True)
    df_h1 = pd.read_csv(h1_file)
    df_h1["time"] = pd.to_datetime(df_h1["timestamp"]).dt.tz_localize(broker_tz, ambiguous='NaT', nonexistent='shift_forward').dt.tz_convert(utc_tz)
    df_h1 = df_h1.dropna(subset=["time"]).sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    return df_m5, df_m15, df_h1


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


LONDON_TZ = ZoneInfo("Europe/London")
NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc


def classify_session(dt: datetime) -> str:
    """
    Returns 'LONDON_KZ', 'NY_KZ', or 'OUTSIDE_KZ' using dynamic DST local timezone boundaries:
    - London Open KZ: 08:00 - 11:00 Europe/London (DST-aware GMT/BST)
    - New York Open KZ: 08:00 - 11:30 America/New_York (DST-aware EST/EDT)
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    dt_utc = dt.astimezone(UTC_TZ)

    # Weekend / Friday market close check
    if dt_utc.weekday() > 4:
        return "OUTSIDE_KZ"
    if dt_utc.weekday() == 4 and dt_utc.hour >= 19:
        return "OUTSIDE_KZ"

    dt_london = dt_utc.astimezone(LONDON_TZ)
    dt_ny = dt_utc.astimezone(NY_TZ)

    l_mins = dt_london.hour * 60 + dt_london.minute
    ny_mins = dt_ny.hour * 60 + dt_ny.minute

    # London Open Kill Zone: 08:00 - 11:00 Europe/London
    if (8 * 60) <= l_mins < (11 * 60):
        return "LONDON_KZ"

    # New York Open Kill Zone: 08:00 - 11:30 America/New_York
    if (8 * 60) <= ny_mins < (11 * 60 + 30):
        return "NY_KZ"

    return "OUTSIDE_KZ"


def is_killzone(dt: datetime) -> bool:
    return classify_session(dt) in ("LONDON_KZ", "NY_KZ")


def is_eod_cutoff(dt: datetime) -> bool:
    """Intraday cutoff: 20:30 UTC Mon-Thu, 19:00 UTC Fri."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    dt_utc = dt.astimezone(UTC_TZ)
    if dt_utc.weekday() == 4 and dt_utc.hour >= 19:
        return True
    return (dt_utc.hour == 20 and dt_utc.minute >= 30) or (dt_utc.hour > 20)


def get_high_impact_usd_events() -> list[datetime]:
    """
    Returns sorted list of UTC datetime timestamps for high-impact USD economic events
    across 2026 (NFP, CPI, PPI, Retail Sales, FOMC Rate Decisions/Press Conferences,
    GDP, Core PCE, ISM Manufacturing & Services PMI, Unemployment Claims).
    All events defined in America/New_York and converted to UTC with dynamic DST awareness.
    """
    ny_tz = ZoneInfo("America/New_York")
    utc_tz = timezone.utc
    events = []

    # 1. NFP & Unemployment Rate (First Friday of every month at 08:30 NY)
    for month in range(1, 13):
        d = datetime(2026, month, 1, tzinfo=ny_tz)
        while d.weekday() != 4:
            d += timedelta(days=1)
        if month == 1 and d.day == 2:
            d += timedelta(days=7)
        events.append(d.replace(hour=8, minute=30, second=0, microsecond=0).astimezone(utc_tz))

    # 2. CPI (Mid-month Wednesday/Thursday at 08:30 NY)
    cpi_days = [
        (1, 14), (2, 11), (3, 11), (4, 10), (5, 13), (6, 10),
        (7, 14), (8, 12), (9, 11), (10, 13), (11, 12), (12, 10)
    ]
    for m, day in cpi_days:
        events.append(datetime(2026, m, day, 8, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 3. PPI (Day adjacent to CPI at 08:30 NY)
    ppi_days = [
        (1, 15), (2, 12), (3, 12), (4, 14), (5, 14), (6, 11),
        (7, 15), (8, 13), (9, 15), (10, 14), (11, 13), (12, 11)
    ]
    for m, day in ppi_days:
        events.append(datetime(2026, m, day, 8, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 4. Retail Sales (Mid-month at 08:30 NY)
    ret_days = [
        (1, 16), (2, 13), (3, 17), (4, 15), (5, 15), (6, 16),
        (7, 16), (8, 14), (9, 16), (10, 16), (11, 17), (12, 15)
    ]
    for m, day in ret_days:
        events.append(datetime(2026, m, day, 8, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 5. FOMC Interest Rate Decision & Press Conference (14:00 & 14:30 NY)
    fomc_dates = [
        (1, 28), (3, 18), (5, 6), (6, 17), (7, 29), (9, 16), (11, 4), (12, 16)
    ]
    for m, day in fomc_dates:
        events.append(datetime(2026, m, day, 14, 0, tzinfo=ny_tz).astimezone(utc_tz))
        events.append(datetime(2026, m, day, 14, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 6. GDP Advance/Prelim/Final (Late month at 08:30 NY)
    gdp_days = [
        (1, 29), (2, 26), (3, 26), (4, 30), (5, 28), (6, 25),
        (7, 30), (8, 27), (9, 24), (10, 29), (11, 25), (12, 23)
    ]
    for m, day in gdp_days:
        events.append(datetime(2026, m, day, 8, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 7. Core PCE Price Index (Last Friday of month at 08:30 NY)
    pce_days = [
        (1, 30), (2, 27), (3, 27), (4, 29), (5, 29), (6, 26),
        (7, 31), (8, 28), (9, 25), (10, 30), (11, 27), (12, 24)
    ]
    for m, day in pce_days:
        events.append(datetime(2026, m, day, 8, 30, tzinfo=ny_tz).astimezone(utc_tz))

    # 8. ISM Manufacturing PMI (1st business day at 10:00 NY)
    ism_mfg = [
        (1, 2), (2, 2), (3, 2), (4, 1), (5, 1), (6, 1),
        (7, 1), (8, 3), (9, 1), (10, 1), (11, 2), (12, 1)
    ]
    for m, day in ism_mfg:
        events.append(datetime(2026, m, day, 10, 0, tzinfo=ny_tz).astimezone(utc_tz))

    # 9. ISM Services PMI (3rd business day at 10:00 NY)
    ism_srv = [
        (1, 6), (2, 4), (3, 4), (4, 3), (5, 5), (6, 3),
        (7, 6), (8, 5), (9, 3), (10, 5), (11, 4), (12, 3)
    ]
    for m, day in ism_srv:
        events.append(datetime(2026, m, day, 10, 0, tzinfo=ny_tz).astimezone(utc_tz))

    # 10. Initial Jobless Claims (Every Thursday at 08:30 NY)
    cur = datetime(2026, 1, 1, tzinfo=ny_tz)
    end = datetime(2026, 12, 31, tzinfo=ny_tz)
    while cur <= end:
        if cur.weekday() == 3:
            events.append(cur.replace(hour=8, minute=30, second=0, microsecond=0).astimezone(utc_tz))
        cur += timedelta(days=1)

    return sorted(list(set(events)))


def is_news_blocked(dt: datetime, events_utc: list[datetime], window_min: int = 30) -> bool:
    """
    Checks if datetime dt falls within +/- window_min minutes of any high-impact event.
    Fast O(log K) search using bisect_left.
    """
    if not events_utc:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)

    delta = timedelta(minutes=window_min)
    left_bound = dt_utc - delta
    right_bound = dt_utc + delta

    idx = bisect_left(events_utc, left_bound)
    if idx < len(events_utc) and events_utc[idx] <= right_bound:
        return True
    return False


def calculate_lot(
    balance: float,
    risk_pct: float,
    sl_distance_usd: float,
    legacy_mode: bool = False,
    spread_usd: float = 0.0,
    slippage_usd: float = 0.0,
    commission_per_lot: float = 0.0,
) -> float:
    if sl_distance_usd <= 0.01:
        return 0.0
    if legacy_mode:
        # Legacy pre-audit behavior: forced minimum 0.02 lot even when SL exceeded budget
        risk_budget = balance * risk_pct
        raw_lot = risk_budget / (sl_distance_usd * 100.0)
        lot = math.floor(raw_lot * 100.0) / 100.0
        return max(0.02, min(0.50, lot))

    risk_budget = balance * risk_pct
    effective_sl = sl_distance_usd + spread_usd + slippage_usd
    loss_per_std_lot = (effective_sl * 100.0) + commission_per_lot
    if loss_per_std_lot <= 0.0:
        return 0.0

    min_lot_risk = 0.01 * loss_per_std_lot
    if min_lot_risk > risk_budget + 1e-9:
        return 0.0  # Min lot risk exceeds budget -> REJECT (NO TRADE)

    raw_lot = risk_budget / loss_per_std_lot
    lot = math.floor(raw_lot * 100.0) / 100.0
    return max(0.01, min(0.50, lot))


class IntradaySimulator:
    def __init__(
        self,
        df_m5: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_h1: pd.DataFrame,
        model: str = "model_b",
        risk_pct: float = 0.0025,
        rr_target: float = 2.0,
        entry_mode: str = "market",
        sl_buffer_mode: str = "structural",
        use_auto_be: bool = True,
        use_partial_close: bool = False,
        use_killzone_filter: bool = True,
        use_h1_bias_filter: bool = True,
        use_discount_premium_filter: bool = True,
        spread_usd: float = BASE_SPREAD_USD,
        slippage_usd: float = 0.0,
        legacy_mode: bool = False,
        sample_split_date: str = "2026-06-01",
        train_end_date: str = "2026-05-01",
        val_end_date: str = "2026-08-01",
        lot_mode: str = "dynamic",
        fixed_lot: float = 0.02,
        max_daily_trades: int = 5,
        use_news_filter: bool = True,
        news_window_min: int = 30,
    ):
        self.df_m5 = df_m5
        self.df_m15 = df_m15
        self.df_h1 = df_h1
        self.model = model
        self.risk_pct = risk_pct
        self.rr_target = rr_target
        self.entry_mode = entry_mode
        self.sl_buffer_mode = sl_buffer_mode
        self.use_auto_be = use_auto_be
        self.use_partial_close = use_partial_close
        self.use_killzone_filter = use_killzone_filter
        self.use_h1_bias_filter = use_h1_bias_filter
        self.use_discount_premium_filter = use_discount_premium_filter
        self.spread_usd = spread_usd
        self.slippage_usd = slippage_usd
        self.legacy_mode = legacy_mode
        self.sample_split_date = sample_split_date
        self.split_date = pd.to_datetime(sample_split_date, utc=True)
        self.train_end_date = pd.to_datetime(train_end_date, utc=True)
        self.val_end_date = pd.to_datetime(val_end_date, utc=True)
        self.lot_mode = lot_mode
        self.fixed_lot = fixed_lot
        self.max_daily_trades = max_daily_trades
        self.use_news_filter = use_news_filter
        self.news_window_min = news_window_min
        self.news_events_utc = get_high_impact_usd_events()

    def run(self):
        if self.model == "control_no_trade":
            return self._compile_metrics(
                trades=[],
                max_daily_dd=0.0,
                max_total_dd=0.0,
                daily_breaches=0,
                floor_breaches=0,
                diagnostics={
                    "rejected_min_lot_risk": 0,
                    "rejected_outside_killzone": 0,
                    "rejected_eod_cutoff": 0,
                    "rejected_daily_limit": 0,
                    "rejected_news_filter": 0,
                    "orders_unfilled_expired": 0,
                    "orders_filled": 0,
                },
            )

        df_m5 = self.df_m5
        df_m15 = self.df_m15
        df_h1 = self.df_h1

        m5_times = df_m5["time"].values
        m15_times = df_m15["time"].values
        h1_times = df_h1["time"].values

        m5_opens = df_m5["open"].values
        m5_highs = df_m5["high"].values
        m5_lows = df_m5["low"].values
        m5_closes = df_m5["close"].values

        m15_opens = df_m15["open"].values
        m15_highs = df_m15["high"].values
        m15_lows = df_m15["low"].values
        m15_closes = df_m15["close"].values
        m15_atrs = df_m15["atr"].values

        h1_highs = df_h1["high"].values
        h1_lows = df_h1["low"].values
        h1_closes = df_h1["close"].values

        trades = []
        open_pos = None
        pending_order = None

        balance = INITIAL_BALANCE
        equity = balance
        peak_equity = balance
        current_day = None
        day_start_equity = balance
        today_trades_count = 0

        max_daily_dd = 0.0
        max_total_dd = 0.0
        daily_breaches = 0
        floor_breaches = 0

        # Track consumed POIs so each M15 Order Block / FVG / Breakout level triggers at most ONE trade
        used_poi_keys = set()

        # Diagnostic counters
        rejected_min_lot_risk = 0
        rejected_outside_killzone = 0
        rejected_eod_cutoff = 0
        rejected_daily_limit = 0
        rejected_news_filter = 0
        orders_unfilled_expired = 0
        orders_filled = 0

        td_15m = np.timedelta64(15, "m")
        td_1h = np.timedelta64(1, "h")
        effective_exec_cost = self.spread_usd + self.slippage_usd

        n_m5 = len(df_m5)

        for i in range(100, n_m5):
            t_np = m5_times[i]
            ts = pd.Timestamp(t_np)
            if ts.tzinfo is None:
                ts = ts.tz_localize(UTC_TZ)
            else:
                ts = ts.tz_convert(UTC_TZ)
            dt = ts.to_pydatetime()

            bar_date = dt.date()
            if bar_date != current_day:
                current_day = bar_date
                day_start_equity = max(balance, equity)
                today_trades_count = 0

            b_open = m5_opens[i]
            b_high = m5_highs[i]
            b_low = m5_lows[i]
            b_close = m5_closes[i]

            # 1. Manage open position (Conservative intrabar rule: SL checked BEFORE TP)
            if open_pos is not None:
                pos_type = open_pos["type"]
                pos_entry = open_pos["entry"]
                pos_sl = open_pos["sl"]
                pos_tp = open_pos["tp"]
                pos_lot = open_pos["lot"]
                r_dist = open_pos["r_dist"]

                # Update intratrade MAE / MFE before checking exit
                if pos_type == "BUY":
                    fav_move = max(0.0, b_high - pos_entry)
                    adv_move = max(0.0, pos_entry - b_low)
                else:
                    fav_move = max(0.0, pos_entry - b_low)
                    adv_move = max(0.0, b_high - pos_entry)

                if fav_move > open_pos["mfe_dist"]:
                    open_pos["mfe_dist"] = fav_move
                if adv_move > open_pos["mae_dist"]:
                    open_pos["mae_dist"] = adv_move

                closed = False
                close_price = 0.0
                close_reason = ""

                # EOD Flat
                if is_eod_cutoff(dt):
                    closed = True
                    close_price = b_close - (self.slippage_usd if pos_type == "BUY" else -self.slippage_usd)
                    close_reason = "EOD_FLAT"
                elif pos_type == "BUY" and b_low <= pos_sl:
                    closed = True
                    close_price = pos_sl - self.slippage_usd
                    close_reason = "SL_BE" if open_pos["be_moved"] else "SL"
                elif pos_type == "SELL" and b_high >= pos_sl:
                    closed = True
                    close_price = pos_sl + self.slippage_usd
                    close_reason = "SL_BE" if open_pos["be_moved"] else "SL"
                elif pos_type == "BUY" and b_high >= pos_tp:
                    closed = True
                    close_price = pos_tp
                    close_reason = "TP"
                elif pos_type == "SELL" and b_low <= pos_tp:
                    closed = True
                    close_price = pos_tp
                    close_reason = "TP"

                if not closed:
                    cur_fav = (b_high - pos_entry) if pos_type == "BUY" else (pos_entry - b_low)
                    if cur_fav >= r_dist:
                        if self.use_auto_be and not open_pos["be_moved"]:
                            be_level = pos_entry + (self.spread_usd if pos_type == "BUY" else -self.spread_usd)
                            open_pos["sl"] = be_level
                            open_pos["be_moved"] = True

                        if self.use_partial_close and not open_pos["partial_taken"] and pos_lot >= 0.02:
                            partial_lot = round(pos_lot / 2.0, 2)
                            p_gain = r_dist * partial_lot * 100.0
                            comm = partial_lot * COMMISSION_PER_LOT
                            net_p = p_gain - comm
                            balance += net_p
                            open_pos["lot"] -= partial_lot
                            open_pos["partial_taken"] = True
                            open_pos["realized_partial"] = net_p

                if closed:
                    diff = (close_price - pos_entry) if pos_type == "BUY" else (pos_entry - close_price)
                    gross_pnl = diff * open_pos["lot"] * 100.0
                    comm = open_pos["lot"] * COMMISSION_PER_LOT
                    net_pnl = gross_pnl - comm

                    total_trade_pnl = net_pnl + open_pos.get("realized_partial", 0.0)
                    balance += net_pnl
                    equity = balance

                    r_mult = total_trade_pnl / open_pos["risk_usd"] if open_pos["risk_usd"] > 0 else 0.0
                    dur_min = max(5.0, (dt - open_pos["entry_time"]).total_seconds() / 60.0)
                    mae_r = open_pos["mae_dist"] / r_dist if r_dist > 0 else 0.0
                    mfe_r = open_pos["mfe_dist"] / r_dist if r_dist > 0 else 0.0
                    mae_usd = open_pos["mae_dist"] * open_pos["orig_lot"] * 100.0
                    mfe_usd = open_pos["mfe_dist"] * open_pos["orig_lot"] * 100.0

                    ent_dt = open_pos["entry_time"]
                    if getattr(ent_dt, "tzinfo", None) is None:
                        ent_dt = ent_dt.replace(tzinfo=UTC_TZ)
                    if ent_dt < self.train_end_date:
                        phase = "TRAIN"
                    elif ent_dt < self.val_end_date:
                        phase = "VALIDATION"
                    else:
                        phase = "OOS"

                    trades.append(
                        {
                            "entry_time": ent_dt,
                            "exit_time": dt,
                            "month": ent_dt.strftime("%Y-%m"),
                            "session": open_pos["session"],
                            "phase": phase,
                            "type": pos_type,
                            "lot": open_pos["orig_lot"],
                            "entry": round(pos_entry, 2),
                            "exit": round(close_price, 2),
                            "sl": round(open_pos["orig_sl"], 2),
                            "tp": round(pos_tp, 2),
                            "reason": close_reason,
                            "pnl_usd": round(total_trade_pnl, 2),
                            "r_multiple": round(r_mult, 3),
                            "duration_min": round(dur_min, 1),
                            "mae_r": round(mae_r, 3),
                            "mfe_r": round(mfe_r, 3),
                            "mae_usd": round(mae_usd, 2),
                            "mfe_usd": round(mfe_usd, 2),
                            "balance_after": round(balance, 2),
                            "in_sample": bool(ent_dt < self.split_date),
                        }
                    )
                    open_pos = None

            # Track drawdowns (strictly accounting for floating unrealized losses)
            if open_pos is not None:
                pos_floating = (
                    (b_close - open_pos["entry"]) if open_pos["type"] == "BUY" else (open_pos["entry"] - b_close)
                ) * open_pos["lot"] * CONTRACT_SIZE - (open_pos["lot"] * COMMISSION_PER_LOT)
                cur_eval_equity = balance + pos_floating
            else:
                cur_eval_equity = balance

            cur_daily_dd = day_start_equity - min(balance, cur_eval_equity)
            if cur_daily_dd > max_daily_dd:
                max_daily_dd = cur_daily_dd
            if cur_daily_dd >= DAILY_LOSS_LIMIT:
                daily_breaches += 1

            if cur_eval_equity > peak_equity:
                peak_equity = cur_eval_equity
            cur_tot_dd = peak_equity - cur_eval_equity
            if cur_tot_dd > max_total_dd:
                max_total_dd = cur_tot_dd
            if cur_eval_equity <= STATIC_FLOOR:
                floor_breaches += 1

            # Manage pending limit orders
            if pending_order is not None:
                p_age = i - pending_order["bar_index"]
                kz_ok = is_killzone(dt) if self.use_killzone_filter else (not is_eod_cutoff(dt))
                if p_age > 6 or not kz_ok:
                    orders_unfilled_expired += 1
                    pending_order = None
                else:
                    p_type = pending_order["type"]
                    p_limit = pending_order["limit_price"]
                    filled = False
                    if p_type == "BUY" and b_low <= p_limit:
                        filled = True
                        actual_entry = p_limit + effective_exec_cost
                    elif p_type == "SELL" and b_high >= p_limit:
                        filled = True
                        actual_entry = p_limit - effective_exec_cost

                    if filled:
                        orders_filled += 1
                        open_pos = {
                            "type": p_type,
                            "entry": actual_entry,
                            "sl": pending_order["sl"],
                            "tp": pending_order["tp"],
                            "lot": pending_order["lot"],
                            "orig_lot": pending_order["lot"],
                            "orig_sl": pending_order["sl"],
                            "r_dist": pending_order["r_dist"],
                            "risk_usd": pending_order["risk_usd"],
                            "entry_time": dt,
                            "session": pending_order["session"],
                            "be_moved": False,
                            "partial_taken": False,
                            "mae_dist": 0.0,
                            "mfe_dist": 0.0,
                        }
                        pending_order = None

            # Signal scanning
            if open_pos is not None or pending_order is not None:
                continue

            if is_eod_cutoff(dt):
                rejected_eod_cutoff += 1
                continue

            if self.use_killzone_filter and not is_killzone(dt):
                rejected_outside_killzone += 1
                continue

            # Binary search for closed M15 and H1 bars (strictly closed prior to current M5 timestamp)
            m15_end = np.searchsorted(m15_times, t_np - td_15m, side="right")
            h1_end = np.searchsorted(h1_times, t_np - td_1h, side="right")

            if m15_end < 25 or h1_end < 20:
                continue

            # H1 Bias
            h1_h_slice = h1_highs[h1_end - 20 : h1_end]
            h1_l_slice = h1_lows[h1_end - 20 : h1_end]
            h1_eq = (np.max(h1_h_slice) + np.min(h1_l_slice)) / 2.0
            h1_last_c = h1_closes[h1_end - 1]

            if self.use_h1_bias_filter:
                h1_bull = h1_last_c > h1_eq
                h1_bear = h1_last_c < h1_eq
            else:
                h1_bull = True
                h1_bear = True

            # M15 Setup
            m15_h_slice = m15_highs[m15_end - 15 : m15_end]
            m15_l_slice = m15_lows[m15_end - 15 : m15_end]

            m15_prev_h = np.max(m15_highs[m15_end - 12 : m15_end - 2])
            m15_prev_l = np.min(m15_lows[m15_end - 12 : m15_end - 2])

            last_m15_c = m15_closes[m15_end - 1]
            cur_atr = m15_atrs[m15_end - 1]
            if np.isnan(cur_atr) or cur_atr <= 0:
                cur_atr = 3.5

            sig = None
            active_model = "model_a" if self.model == "baseline_legacy" else self.model

            if active_model in ("model_a", "model_a_plus_b"):
                # Model A: Sweep -> Displacement -> MSS -> FVG (1 trade per M15 FVG)
                poi_key = ("MODEL_A_FVG", m15_end - 2)
                if poi_key not in used_poi_keys:
                    bull_swp = (np.min(m15_l_slice) < m15_prev_l) and (last_m15_c > m15_prev_l)
                    bear_swp = (np.max(m15_h_slice) > m15_prev_h) and (last_m15_c < m15_prev_h)

                    c1_h = m15_highs[m15_end - 3]
                    c1_l = m15_lows[m15_end - 3]
                    c2_o = m15_opens[m15_end - 2]
                    c2_c = m15_closes[m15_end - 2]
                    c3_h = m15_highs[m15_end - 1]
                    c3_l = m15_lows[m15_end - 1]

                    bull_fvg = (c3_l > c1_h) and (c2_c > c2_o) and (abs(c2_c - c2_o) > 0.4 * cur_atr)
                    bear_fvg = (c3_h < c1_l) and (c2_c < c2_o) and (abs(c2_c - c2_o) > 0.4 * cur_atr)

                    if bull_swp and bull_fvg and h1_bull:
                        sl = np.min(m15_l_slice) - (0.50 if self.sl_buffer_mode == "structural" else 1.2 * cur_atr)
                        if self.legacy_mode and (b_close - sl) < 10.0:
                            sl = b_close - 10.0
                        entry = b_close
                        limit_p = (c1_h + c3_l) / 2.0
                        sig = {"type": "BUY", "entry": entry, "sl": sl, "limit_price": limit_p, "poi_key": poi_key}

                    elif bear_swp and bear_fvg and h1_bear:
                        sl = np.max(m15_h_slice) + (0.50 if self.sl_buffer_mode == "structural" else 1.2 * cur_atr)
                        if self.legacy_mode and (sl - b_close) < 10.0:
                            sl = b_close + 10.0
                        entry = b_close
                        limit_p = (c1_l + c3_h) / 2.0
                        sig = {"type": "SELL", "entry": entry, "sl": sl, "limit_price": limit_p, "poi_key": poi_key}

            if sig is None and active_model in ("model_b", "model_a_plus_b"):
                # Model B (Institutional ICT): H1 Trend + Discount(<0.45)/Premium(>0.55) +
                # Unmitigated M15 Order Block (followed by >0.5*ATR displacement) + M5 MSS confirmation (1 trade per OB)
                m15_hi = np.max(m15_h_slice)
                m15_lo = np.min(m15_l_slice)
                m15_range = max(0.10, m15_hi - m15_lo)
                dr_pct = (last_m15_c - m15_lo) / m15_range
                if self.use_discount_premium_filter:
                    in_discount = dr_pct < 0.45
                    in_premium = dr_pct > 0.55
                else:
                    in_discount = True
                    in_premium = True

                # Find institutional OB (opposing candle k followed by displacement candle k+1 > 0.5*ATR, unmitigated)
                down_idx = [
                    k
                    for k in range(m15_end - 12, m15_end - 2)
                    if m15_closes[k] < m15_opens[k]
                    and m15_closes[k + 1] > m15_opens[k + 1]
                    and (m15_closes[k + 1] - m15_opens[k + 1]) > 0.5 * cur_atr
                    and ("OB_BUY", k) not in used_poi_keys
                    and not (k + 2 < m15_end and np.min(m15_closes[k + 2 : m15_end]) < m15_lows[k])
                ]
                up_idx = [
                    k
                    for k in range(m15_end - 12, m15_end - 2)
                    if m15_closes[k] > m15_opens[k]
                    and m15_closes[k + 1] < m15_opens[k + 1]
                    and (m15_opens[k + 1] - m15_closes[k + 1]) > 0.5 * cur_atr
                    and ("OB_SELL", k) not in used_poi_keys
                    and not (k + 2 < m15_end and np.max(m15_closes[k + 2 : m15_end]) > m15_highs[k])
                ]

                m5_prev3_high = np.max(m5_highs[i - 3 : i])
                m5_prev3_low = np.min(m5_lows[i - 3 : i])

                if h1_bull and in_discount and down_idx:
                    ob_k = down_idx[-1]
                    ob_h = m15_highs[ob_k]
                    ob_l = m15_lows[ob_k]
                    # M5 pullback into OB + M5 MSS break above prior 3 M5 highs
                    if b_close > b_open and b_low <= ob_h and b_close >= ob_l and b_close > m5_prev3_high:
                        sl = min(ob_l, b_low) - 0.50
                        sig = {
                            "type": "BUY",
                            "entry": b_close,
                            "sl": sl,
                            "limit_price": ob_h,
                            "poi_key": ("OB_BUY", ob_k),
                        }

                elif h1_bear and in_premium and up_idx:
                    ob_k = up_idx[-1]
                    ob_h = m15_highs[ob_k]
                    ob_l = m15_lows[ob_k]
                    # M5 pullback into OB + M5 MSS break below prior 3 M5 lows
                    if b_close < b_open and b_high >= ob_l and b_close <= ob_h and b_close < m5_prev3_low:
                        sl = max(ob_h, b_high) + 0.50
                        sig = {
                            "type": "SELL",
                            "entry": b_close,
                            "sl": sl,
                            "limit_price": ob_l,
                            "poi_key": ("OB_SELL", ob_k),
                        }

            elif active_model == "model_b_multi_touch":
                # Unfiltered OB Touch (any opposing M15 candle without displacement or M5 MSS confirmation, 1 trade per OB)
                m15_eq = (np.max(m15_h_slice) + np.min(m15_l_slice)) / 2.0
                if self.use_discount_premium_filter:
                    in_discount = last_m15_c < m15_eq
                    in_premium = last_m15_c > m15_eq
                else:
                    in_discount = True
                    in_premium = True

                down_idx = [
                    k
                    for k in range(m15_end - 6, m15_end - 1)
                    if m15_closes[k] < m15_opens[k]
                    and ("OB_NAIVE_BUY", k) not in used_poi_keys
                    and not (k + 2 < m15_end and np.min(m15_closes[k + 2 : m15_end]) < m15_lows[k])
                ]
                up_idx = [
                    k
                    for k in range(m15_end - 6, m15_end - 1)
                    if m15_closes[k] > m15_opens[k]
                    and ("OB_NAIVE_SELL", k) not in used_poi_keys
                    and not (k + 2 < m15_end and np.max(m15_closes[k + 2 : m15_end]) > m15_highs[k])
                ]

                if h1_bull and in_discount and down_idx:
                    ob_k = down_idx[-1]
                    ob_h = m15_highs[ob_k]
                    ob_l = m15_lows[ob_k]
                    if b_close > b_open and b_low <= ob_h and b_close >= ob_l:
                        sl = ob_l - 0.50
                        sig = {
                            "type": "BUY",
                            "entry": b_close,
                            "sl": sl,
                            "limit_price": ob_h,
                            "poi_key": ("OB_NAIVE_BUY", ob_k),
                        }

                elif h1_bear and in_premium and up_idx:
                    ob_k = up_idx[-1]
                    ob_h = m15_highs[ob_k]
                    ob_l = m15_lows[ob_k]
                    if b_close < b_open and b_high >= ob_l and b_close <= ob_h:
                        sl = ob_h + 0.50
                        sig = {
                            "type": "SELL",
                            "entry": b_close,
                            "sl": sl,
                            "limit_price": ob_l,
                            "poi_key": ("OB_NAIVE_SELL", ob_k),
                        }

            elif active_model == "model_c":
                # Model C: Breakout + Retest (1 trade per M15 breakout bar)
                poi_key = ("MODEL_C_BRK", m15_end - 1)
                if poi_key not in used_poi_keys:
                    broke_h = (last_m15_c > m15_prev_h) and (
                        (m15_highs[m15_end - 1] - m15_lows[m15_end - 1]) > 1.0 * cur_atr
                    )
                    broke_l = (last_m15_c < m15_prev_l) and (
                        (m15_highs[m15_end - 1] - m15_lows[m15_end - 1]) > 1.0 * cur_atr
                    )

                    if broke_h and h1_bull:
                        if b_low <= m15_prev_h <= b_high and b_close > m15_prev_h:
                            sl = b_low - 0.50
                            sig = {
                                "type": "BUY",
                                "entry": b_close,
                                "sl": sl,
                                "limit_price": m15_prev_h,
                                "poi_key": poi_key,
                            }

                    elif broke_l and h1_bear:
                        if b_low <= m15_prev_l <= b_high and b_close < m15_prev_l:
                            sl = b_high + 0.50
                            sig = {
                                "type": "SELL",
                                "entry": b_close,
                                "sl": sl,
                                "limit_price": m15_prev_l,
                                "poi_key": poi_key,
                            }

            if sig:
                sig_type = sig["type"]
                sig_entry = sig["entry"]
                sig_sl = sig["sl"]
                # Strict directional stop-loss validation (never allow inverted SL)
                if sig_type == "BUY" and sig_sl >= sig_entry - 0.10:
                    continue
                if sig_type == "SELL" and sig_sl <= sig_entry + 0.10:
                    continue

                # News filter check
                if self.use_news_filter and is_news_blocked(dt, self.news_events_utc, self.news_window_min):
                    rejected_news_filter += 1
                    continue

                # Daily trades limit check
                if self.max_daily_trades > 0 and today_trades_count >= self.max_daily_trades:
                    rejected_daily_limit += 1
                    continue

                sl_dist = abs(sig_entry - sig_sl)
                # Enforce XAUUSD minimum anatomical stop-loss ($2.00, matching config.MIN_SL_PIPS["XAUUSD"])
                min_gold_sl = 10.0 if self.legacy_mode else 2.0
                if sl_dist < min_gold_sl:
                    sl_dist = min_gold_sl
                    sig_sl = (sig_entry - min_gold_sl) if sig_type == "BUY" else (sig_entry + min_gold_sl)

                if self.lot_mode == "fixed":
                    lot = self.fixed_lot
                    effective_sl_dist = sl_dist + self.spread_usd + self.slippage_usd
                    sl_dollar_risk = (lot * effective_sl_dist * CONTRACT_SIZE) + (lot * COMMISSION_PER_LOT)
                    max_risk_cap = balance * self.risk_pct if self.risk_pct > 0 else 25.0
                    if sl_dollar_risk > max_risk_cap + 1e-9:
                        lot = 0.0
                else:
                    lot = calculate_lot(
                        balance,
                        self.risk_pct,
                        sl_dist,
                        legacy_mode=self.legacy_mode,
                        spread_usd=self.spread_usd,
                        slippage_usd=self.slippage_usd,
                        commission_per_lot=COMMISSION_PER_LOT,
                    )

                if lot <= 0.0:
                    rejected_min_lot_risk += 1
                else:
                    today_trades_count += 1
                    # Mark POI as consumed ONLY when a trade/order is actually accepted by risk manager
                    if "poi_key" in sig:
                        used_poi_keys.add(sig["poi_key"])
                    actual_risk_usd = lot * sl_dist * CONTRACT_SIZE
                    r_dist = sl_dist
                    tp = sig_entry + (r_dist * self.rr_target if sig_type == "BUY" else -r_dist * self.rr_target)
                    sess_name = classify_session(dt)

                    if self.entry_mode == "market":
                        orders_filled += 1
                        exec_price = sig_entry + (effective_exec_cost if sig_type == "BUY" else -effective_exec_cost)
                        open_pos = {
                            "type": sig_type,
                            "entry": exec_price,
                            "sl": sig_sl,
                            "tp": tp,
                            "lot": lot,
                            "orig_lot": lot,
                            "orig_sl": sig_sl,
                            "r_dist": r_dist,
                            "risk_usd": actual_risk_usd,
                            "entry_time": dt,
                            "session": sess_name,
                            "be_moved": False,
                            "partial_taken": False,
                            "mae_dist": 0.0,
                            "mfe_dist": 0.0,
                        }
                    elif self.entry_mode == "limit_fvg":
                        pending_order = {
                            "type": sig_type,
                            "limit_price": sig.get("limit_price", sig_entry),
                            "sl": sig_sl,
                            "tp": tp,
                            "lot": lot,
                            "r_dist": r_dist,
                            "risk_usd": actual_risk_usd,
                            "bar_index": i,
                            "session": sess_name,
                        }

        diagnostics = {
            "rejected_min_lot_risk": rejected_min_lot_risk,
            "rejected_outside_killzone": rejected_outside_killzone,
            "rejected_eod_cutoff": rejected_eod_cutoff,
            "rejected_daily_limit": rejected_daily_limit,
            "rejected_news_filter": rejected_news_filter,
            "orders_unfilled_expired": orders_unfilled_expired,
            "orders_filled": orders_filled,
        }
        self._last_trades = trades
        return self._compile_metrics(trades, max_daily_dd, max_total_dd, daily_breaches, floor_breaches, diagnostics)

    def _compile_metrics(self, trades, max_daily_dd, max_total_dd, daily_breaches, floor_breaches, diagnostics):
        if not trades:
            return {
                "model": self.model.upper(),
                "risk_pct": f"{self.risk_pct * 100:.2f}%",
                "total_trades": 0,
                "wins_tp": 0,
                "breakevens": 0,
                "losses": 0,
                "tp_win_rate_pct": 0.0,
                "non_loss_rate_pct": 0.0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "net_profit_usd": 0.0,
                "total_r": 0.0,
                "expectancy_r": 0.0,
                "expectancy_usd": 0.0,
                "max_daily_dd_usd": 0.0,
                "max_total_dd_usd": 0.0,
                "fundingpips_rules_passed": True,
                "diagnostics": diagnostics,
                "status": "NO_TRADES",
            }

        df_t = pd.DataFrame(trades)
        total_trades = len(df_t)

        # Strict outcome separation:
        # 1. Full Wins (wins_tp): TP hit or EOD close with r_multiple > 0.15
        # 2. Breakeven Scratches (breakevens): SL_BE or EOD close with -0.05 <= r_multiple <= 0.15
        # 3. Full Losses (losses): SL hit or EOD close with r_multiple < -0.05
        tp_mask = (df_t["reason"] == "TP") | ((df_t["reason"] == "EOD_FLAT") & (df_t["r_multiple"] > 0.15))
        be_mask = (~tp_mask) & ((df_t["reason"] == "SL_BE") | ((df_t["reason"] == "EOD_FLAT") & (df_t["r_multiple"] >= -0.05) & (df_t["r_multiple"] <= 0.15)))
        loss_mask = (~tp_mask) & (~be_mask)

        wins_tp_df = df_t[tp_mask]
        be_df = df_t[be_mask]
        losses_df = df_t[loss_mask]

        pos_pnl_df = df_t[df_t["pnl_usd"] > 0]
        neg_pnl_df = df_t[df_t["pnl_usd"] <= 0]

        tp_win_rate = (len(wins_tp_df) / total_trades) * 100.0 if total_trades > 0 else 0.0
        non_loss_rate = ((len(wins_tp_df) + len(be_df)) / total_trades) * 100.0 if total_trades > 0 else 0.0
        raw_win_rate = (len(pos_pnl_df) / total_trades) * 100.0 if total_trades > 0 else 0.0

        gross_profit = pos_pnl_df["pnl_usd"].sum() if len(pos_pnl_df) > 0 else 0.0
        gross_loss = abs(neg_pnl_df["pnl_usd"].sum()) if len(neg_pnl_df) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0

        avg_win_usd = float(wins_tp_df["pnl_usd"].mean()) if len(wins_tp_df) > 0 else 0.0
        avg_win_r = float(wins_tp_df["r_multiple"].mean()) if len(wins_tp_df) > 0 else 0.0
        avg_loss_usd = float(losses_df["pnl_usd"].mean()) if len(losses_df) > 0 else 0.0
        avg_loss_r = float(losses_df["r_multiple"].mean()) if len(losses_df) > 0 else 0.0
        payoff_ratio = (avg_win_usd / abs(avg_loss_usd)) if avg_loss_usd < 0 else 0.0

        net_profit = float(df_t["pnl_usd"].sum())
        total_r = float(df_t["r_multiple"].sum())
        exp_r = total_r / total_trades if total_trades > 0 else 0.0
        exp_usd = net_profit / total_trades if total_trades > 0 else 0.0

        def sub_metrics(sub_df):
            if sub_df.empty:
                return {
                    "trades": 0,
                    "wins_tp": 0,
                    "breakevens": 0,
                    "losses": 0,
                    "tp_win_rate": 0.0,
                    "non_loss_rate": 0.0,
                    "win_rate": 0.0,
                    "pf": 0.0,
                    "net_usd": 0.0,
                    "exp_r": 0.0,
                    "avg_win_usd": 0.0,
                    "avg_loss_usd": 0.0,
                    "payoff_ratio": 0.0,
                    "mean_mae_r": 0.0,
                    "mean_mfe_r": 0.0,
                    "avg_duration_min": 0.0,
                }
            s_tp = (sub_df["reason"] == "TP") | ((sub_df["reason"] == "EOD_FLAT") & (sub_df["r_multiple"] > 0.15))
            s_be = (~s_tp) & ((sub_df["reason"] == "SL_BE") | ((sub_df["reason"] == "EOD_FLAT") & (sub_df["r_multiple"] >= -0.05) & (sub_df["r_multiple"] <= 0.15)))
            s_ls = (~s_tp) & (~s_be)
            w_tp = sub_df[s_tp]
            b_sc = sub_df[s_be]
            l_fl = sub_df[s_ls]
            w_raw = sub_df[sub_df["pnl_usd"] > 0]
            l_raw = sub_df[sub_df["pnl_usd"] <= 0]
            gp = float(w_raw["pnl_usd"].sum()) if len(w_raw) > 0 else 0.0
            gl = abs(float(l_raw["pnl_usd"].sum()) ) if len(l_raw) > 0 else 0.0
            pf = (gp / gl) if gl > 0 else 99.0
            aw = float(w_tp["pnl_usd"].mean()) if len(w_tp) > 0 else 0.0
            al = float(l_fl["pnl_usd"].mean()) if len(l_fl) > 0 else 0.0
            pr = (aw / abs(al)) if al < 0 else 0.0
            return {
                "trades": len(sub_df),
                "wins_tp": len(w_tp),
                "breakevens": len(b_sc),
                "losses": len(l_fl),
                "tp_win_rate": round((len(w_tp) / len(sub_df)) * 100.0, 1),
                "non_loss_rate": round(((len(w_tp) + len(b_sc)) / len(sub_df)) * 100.0, 1),
                "win_rate": round((len(w_raw) / len(sub_df)) * 100.0, 1),
                "pf": round(pf, 2),
                "net_usd": round(float(sub_df["pnl_usd"].sum()), 2),
                "exp_r": round(float(sub_df["r_multiple"].mean()), 3),
                "avg_win_usd": round(aw, 2),
                "avg_loss_usd": round(al, 2),
                "payoff_ratio": round(pr, 2),
                "mean_mae_r": round(float(sub_df["mae_r"].mean()), 2),
                "mean_mfe_r": round(float(sub_df["mfe_r"].mean()), 2),
                "avg_duration_min": round(float(sub_df["duration_min"].mean()), 1),
            }

        # Loss streak (strict losses r_multiple < -0.05)
        streak = 0
        max_streak = 0
        for r_val in df_t["r_multiple"]:
            if r_val < -0.05:
                streak += 1
                max_streak = max(max_streak, streak)
            elif r_val > 0.15:
                streak = 0

        longs = df_t[df_t["type"] == "BUY"]
        shorts = df_t[df_t["type"] == "SELL"]

        is_trades = df_t[df_t["in_sample"] == True]
        oos_trades = df_t[df_t["in_sample"] == False]

        train_df = df_t[df_t["phase"] == "TRAIN"]
        val_df = df_t[df_t["phase"] == "VALIDATION"]
        oos3_df = df_t[df_t["phase"] == "OOS"]

        # Monthly breakdown
        monthly_breakdown = {}
        for m_key, m_grp in df_t.groupby("month"):
            monthly_breakdown[str(m_key)] = sub_metrics(m_grp)

        # Session breakdown
        session_breakdown = {}
        for s_key, s_grp in df_t.groupby("session"):
            session_breakdown[str(s_key)] = sub_metrics(s_grp)

        # Walk-forward rolling windows (bi-monthly windows across 2026)
        wf_windows = [
            ("WF1_Feb_Mar", "2026-02-01", "2026-04-01"),
            ("WF2_Apr_May", "2026-04-01", "2026-06-01"),
            ("WF3_Jun_Jul", "2026-06-01", "2026-08-01"),
            ("WF4_Aug_Oct", "2026-08-01", "2026-11-01"),
        ]
        walk_forward = {}
        for wf_name, wf_start, wf_end in wf_windows:
            s_dt = pd.to_datetime(wf_start, utc=True)
            e_dt = pd.to_datetime(wf_end, utc=True)
            wf_sub = df_t[(df_t["entry_time"] >= s_dt) & (df_t["entry_time"] < e_dt)]
            walk_forward[wf_name] = sub_metrics(wf_sub)

        res = {
            "model": self.model.upper(),
            "lot_mode": self.lot_mode,
            "fixed_lot": self.fixed_lot if self.lot_mode == "fixed" else None,
            "risk_pct": f"{self.risk_pct * 100:.2f}%",
            "max_daily_trades": self.max_daily_trades,
            "news_filter": self.use_news_filter,
            "news_window_min": self.news_window_min,
            "rr_target": f"1:{self.rr_target}",
            "entry_mode": self.entry_mode,
            "auto_be": self.use_auto_be,
            "partial_close": self.use_partial_close,
            "spread_usd": self.spread_usd,
            "slippage_usd": self.slippage_usd,
            "total_trades": total_trades,
            "wins_tp": len(wins_tp_df),
            "breakevens": len(be_df),
            "losses": len(losses_df),
            "wins": len(pos_pnl_df),
            "tp_win_rate_pct": round(tp_win_rate, 1),
            "non_loss_rate_pct": round(non_loss_rate, 1),
            "win_rate_pct": round(raw_win_rate, 1),
            "avg_win_usd": round(avg_win_usd, 2),
            "avg_win_r": round(avg_win_r, 2),
            "avg_loss_usd": round(avg_loss_usd, 2),
            "avg_loss_r": round(avg_loss_r, 2),
            "payoff_ratio": round(payoff_ratio, 2),
            "profit_factor": round(profit_factor, 2),
            "net_profit_usd": round(net_profit, 2),
            "total_r": round(total_r, 2),
            "expectancy_r": round(exp_r, 3),
            "expectancy_usd": round(exp_usd, 2),
            "max_daily_dd_usd": round(max_daily_dd, 2),
            "max_total_dd_usd": round(max_total_dd, 2),
            "max_loss_streak": max_streak,
            "daily_breaches": daily_breaches,
            "floor_breaches": floor_breaches,
            "fundingpips_rules_passed": bool(daily_breaches == 0 and floor_breaches == 0),
            "mae_mfe_duration": {
                "mean_mae_r": round(float(df_t["mae_r"].mean()), 2),
                "median_mae_r": round(float(df_t["mae_r"].median()), 2),
                "mean_mae_usd": round(float(df_t["mae_usd"].mean()), 2),
                "median_mae_usd": round(float(df_t["mae_usd"].median()), 2),
                "mean_mfe_r": round(float(df_t["mfe_r"].mean()), 2),
                "median_mfe_r": round(float(df_t["mfe_r"].median()), 2),
                "mean_mfe_usd": round(float(df_t["mfe_usd"].mean()), 2),
                "median_mfe_usd": round(float(df_t["mfe_usd"].median()), 2),
                "avg_duration_min": round(float(df_t["duration_min"].mean()), 1),
                "median_duration_min": round(float(df_t["duration_min"].median()), 1),
            },
            "diagnostics": diagnostics,
            "long_metrics": sub_metrics(longs),
            "short_metrics": sub_metrics(shorts),
            "in_sample": sub_metrics(is_trades),
            "out_of_sample": sub_metrics(oos_trades),
            "three_way_split": {
                "TRAIN": sub_metrics(train_df),
                "VALIDATION": sub_metrics(val_df),
                "OOS": sub_metrics(oos3_df),
            },
            "walk_forward": walk_forward,
            "monthly_breakdown": monthly_breakdown,
            "session_breakdown": session_breakdown,
        }
        return res


def run_monte_carlo_for_trades(trades: list[dict], n_simulations: int = 10000, n_trades: int = 100) -> dict:
    """
    Simulates 10,000 iterations of 100-trade sequences using empirical bootstrap resampling
    to compute Phase 1 pass rate (10%), Phase 2 pass rate (8%), daily breach rate ($200),
    and max drawdown floor breach rate ($600).
    """
    if not trades:
        return {
            "n_simulations": n_simulations,
            "phase1_pass_pct": 0.0,
            "phase2_pass_pct": 0.0,
            "joint_pass_pct": 0.0,
            "daily_breach_pct": 0.0,
            "max_dd_breach_pct": 0.0,
            "loss_streak_p95": 0,
            "loss_streak_p99": 0,
        }

    pnls = np.array([t["pnl_usd"] for t in trades])
    if len(pnls) == 0:
        return {
            "n_simulations": n_simulations,
            "phase1_pass_pct": 0.0,
            "phase2_pass_pct": 0.0,
            "joint_pass_pct": 0.0,
            "daily_breach_pct": 0.0,
            "max_dd_breach_pct": 0.0,
            "loss_streak_p95": 0,
            "loss_streak_p99": 0,
        }

    np.random.seed(42)
    p1_pass = 0
    p2_pass = 0
    both_pass = 0
    dd_breaches = 0
    daily_breaches = 0
    max_loss_streaks = []

    p1_target = 500.0    # 10% on $5,000
    p2_target = 400.0    # 8% on $5,000
    daily_limit = 200.0  # 4% daily
    max_dd_limit = 600.0 # 12% static floor ($4,400)

    for _ in range(n_simulations):
        # Phase 1 simulation
        sim_pnl = np.random.choice(pnls, size=n_trades, replace=True)
        eq_curve = 5000.0 + np.cumsum(sim_pnl)

        min_eq = np.min(eq_curve)
        breached_dd = min_eq <= (5000.0 - max_dd_limit)
        if breached_dd:
            dd_breaches += 1

        hit_daily = False
        idx = 0
        while idx < n_trades:
            chunk = sim_pnl[idx : idx + np.random.randint(1, 6)]
            if np.sum(chunk) <= -daily_limit:
                hit_daily = True
                break
            idx += len(chunk)
        if hit_daily:
            daily_breaches += 1

        cur_streak = 0
        m_streak = 0
        for p in sim_pnl:
            if p < 0:
                cur_streak += 1
                if cur_streak > m_streak:
                    m_streak = cur_streak
            else:
                cur_streak = 0
        max_loss_streaks.append(m_streak)

        passed_p1 = False
        if not breached_dd and not hit_daily and np.max(eq_curve) >= (5000.0 + p1_target):
            target_idx = np.where(eq_curve >= 5000.0 + p1_target)[0]
            if len(target_idx) > 0:
                first_t_idx = target_idx[0]
                eq_before_target = eq_curve[:first_t_idx + 1]
                if np.min(eq_before_target) > (5000.0 - max_dd_limit):
                    passed_p1 = True
                    p1_pass += 1

        # Phase 2 simulation (conditional on passing Phase 1)
        if passed_p1:
            sim_pnl2 = np.random.choice(pnls, size=n_trades, replace=True)
            eq_curve2 = 5000.0 + np.cumsum(sim_pnl2)
            breached_dd2 = np.min(eq_curve2) <= (5000.0 - max_dd_limit)
            hit_daily2 = False
            idx = 0
            while idx < n_trades:
                chunk2 = sim_pnl2[idx : idx + np.random.randint(1, 6)]
                if np.sum(chunk2) <= -daily_limit:
                    hit_daily2 = True
                    break
                idx += len(chunk2)

            if not breached_dd2 and not hit_daily2 and np.max(eq_curve2) >= (5000.0 + p2_target):
                target_idx2 = np.where(eq_curve2 >= 5000.0 + p2_target)[0]
                if len(target_idx2) > 0:
                    first_t_idx2 = target_idx2[0]
                    eq_before_target2 = eq_curve2[:first_t_idx2 + 1]
                    if np.min(eq_before_target2) > (5000.0 - max_dd_limit):
                        p2_pass += 1
                        both_pass += 1

    return {
        "n_simulations": n_simulations,
        "phase1_pass_pct": round((p1_pass / n_simulations) * 100.0, 2),
        "phase2_pass_pct": round((p2_pass / max(1, p1_pass)) * 100.0, 2),
        "joint_pass_pct": round((both_pass / n_simulations) * 100.0, 2),
        "daily_breach_pct": round((daily_breaches / n_simulations) * 100.0, 2),
        "max_dd_breach_pct": round((dd_breaches / n_simulations) * 100.0, 2),
        "loss_streak_p95": int(np.percentile(max_loss_streaks, 95)) if max_loss_streaks else 0,
        "loss_streak_p99": int(np.percentile(max_loss_streaks, 99)) if max_loss_streaks else 0,
    }


def run_full_suite():
    df_m5, df_m15, df_h1 = load_candles()
    df_m15["atr"] = calculate_atr(df_m15, 14)
    df_h1["atr"] = calculate_atr(df_h1, 14)

    results = []

    print("\n>>> 1. BASELINE vs ENTRY MODELS (CONTROL, LEGACY, A, B, A+B, C) <<<", flush=True)
    model_configs = [
        ("control_no_trade", 0.0025, False, "Control (0 Trades)", "dynamic", 0.02, True),
        ("baseline_legacy", 0.0080, True, "Baseline Legacy (Pre-Audit 0.8% / Forced 0.02 Lot / $10 Min SL)", "dynamic", 0.02, False),
        ("model_a", 0.0025, False, "Model A (Sweep -> MSS -> FVG @ 0.25%)", "dynamic", 0.02, True),
        ("model_a", 0.0050, False, "Model A (Sweep -> MSS -> FVG @ 0.50%)", "dynamic", 0.02, True),
        ("model_b", 0.0025, False, "Model B (Trend OB Pullback @ 0.25%, 1 Trade/OB)", "dynamic", 0.02, True),
        ("model_b", 0.0050, False, "Model B (Trend OB Pullback @ 0.50%, 1 Trade/OB)", "dynamic", 0.02, True),
        ("model_a_plus_b", 0.0025, False, "Model A+B Combined (@ 0.25%, Dynamic Risk)", "dynamic", 0.02, True),
        ("model_a_plus_b", 0.0050, False, "Model A+B Combined (@ 0.50%, Dynamic Risk)", "dynamic", 0.02, True),
        ("model_b_multi_touch", 0.0025, False, "Model B Multi-Touch (Unfiltered OB Re-entry @ 0.25%)", "dynamic", 0.02, True),
        ("model_c", 0.0025, False, "Model C (Breakout Retest @ 0.25%)", "dynamic", 0.02, True),
    ]
    for m_name, r_pct, is_leg, label, l_mode, f_lot, n_filt in model_configs:
        sim = IntradaySimulator(
            df_m5,
            df_m15,
            df_h1,
            model=m_name,
            risk_pct=r_pct,
            rr_target=2.0,
            entry_mode="market",
            use_auto_be=(not is_leg),
            legacy_mode=is_leg,
            lot_mode=l_mode,
            fixed_lot=f_lot,
            use_news_filter=n_filt,
        )
        res = sim.run()
        print(
            f"{label}: Trades={res.get('total_trades')} "
            f"(TP={res.get('wins_tp')}, BE={res.get('breakevens')}, SL={res.get('losses')}) | "
            f"TP_WR={res.get('tp_win_rate_pct')}% | NonLoss={res.get('non_loss_rate_pct')}% | "
            f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | "
            f"ExpR={res.get('expectancy_r')}R | MaxDD=${res.get('max_total_dd_usd')} | "
            f"Passed={res.get('fundingpips_rules_passed')}",
            flush=True,
        )
        results.append({"category": "Model Comparison", "config": label, "result": res})

    print("\n>>> 2. FIXED LOT 0.02 vs DYNAMIC RISK SIZING (0.25%, 0.50%) <<<", flush=True)
    sizing_configs = [
        ("model_b", "fixed", 0.02, 0.0050, "Model B Fixed Lot 0.02 (0.50% Hard Cap $25)"),
        ("model_b", "dynamic", 0.02, 0.0025, "Model B Dynamic Risk 0.25%"),
        ("model_b", "dynamic", 0.02, 0.0050, "Model B Dynamic Risk 0.50%"),
        ("model_a", "fixed", 0.02, 0.0050, "Model A Fixed Lot 0.02 (0.50% Hard Cap $25)"),
        ("model_a", "dynamic", 0.02, 0.0050, "Model A Dynamic Risk 0.50%"),
        ("model_a_plus_b", "fixed", 0.02, 0.0050, "Model A+B Fixed Lot 0.02 (0.50% Hard Cap $25)"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0025, "Model A+B Dynamic Risk 0.25%"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0050, "Model A+B Dynamic Risk 0.50%"),
    ]
    for m_name, l_mode, f_lot, r_pct, label in sizing_configs:
        sim = IntradaySimulator(
            df_m5,
            df_m15,
            df_h1,
            model=m_name,
            risk_pct=r_pct,
            rr_target=2.0,
            lot_mode=l_mode,
            fixed_lot=f_lot,
            use_auto_be=True,
            use_news_filter=True,
        )
        res = sim.run()
        print(
            f"{label}: Trades={res.get('total_trades')} | "
            f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | "
            f"MaxDD=${res.get('max_total_dd_usd')} | ExpR={res.get('expectancy_r')}R | "
            f"Passed={res.get('fundingpips_rules_passed')}",
            flush=True,
        )
        results.append({"category": "Lot Sizing Comparison", "config": label, "result": res})

    print("\n>>> 3. NEWS FILTER IMPACT (NEWS FILTER OFF vs ON +/-30m) <<<", flush=True)
    news_configs = [
        ("model_b", 0.0025, False, "Model B (News Filter OFF)"),
        ("model_b", 0.0025, True, "Model B (News Filter ON +/-30m)"),
        ("model_a", 0.0050, False, "Model A (News Filter OFF)"),
        ("model_a", 0.0050, True, "Model A (News Filter ON +/-30m)"),
        ("model_a_plus_b", 0.0050, False, "Model A+B (News Filter OFF)"),
        ("model_a_plus_b", 0.0050, True, "Model A+B (News Filter ON +/-30m)"),
    ]
    for m_name, r_pct, n_filt, label in news_configs:
        sim = IntradaySimulator(
            df_m5,
            df_m15,
            df_h1,
            model=m_name,
            risk_pct=r_pct,
            rr_target=2.0,
            use_auto_be=True,
            use_news_filter=n_filt,
        )
        res = sim.run()
        diag = res.get("diagnostics", {})
        print(
            f"{label}: Trades={res.get('total_trades')} (NewsBlocked={diag.get('rejected_news_filter', 0)}) | "
            f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | "
            f"MaxDD=${res.get('max_total_dd_usd')}",
            flush=True,
        )
        results.append({"category": "News Filter Comparison", "config": label, "result": res})

    print("\n>>> 4. COMPARING RR TARGETS (1:1.5 vs 1:2.0 vs 1:2.5) <<<", flush=True)
    rr_matrix = [
        ("model_b", "dynamic", 0.02, 0.0025),
        ("model_a", "dynamic", 0.02, 0.0050),
        ("model_a_plus_b", "dynamic", 0.02, 0.0025),
        ("model_a_plus_b", "dynamic", 0.02, 0.0050),
        ("model_a_plus_b", "fixed", 0.02, 0.0050),
    ]
    for m_name, l_mode, f_lot, r_pct in rr_matrix:
        for rr in [1.5, 2.0, 2.5]:
            sim = IntradaySimulator(
                df_m5,
                df_m15,
                df_h1,
                model=m_name,
                risk_pct=r_pct,
                rr_target=rr,
                entry_mode="market",
                use_auto_be=True,
                lot_mode=l_mode,
                fixed_lot=f_lot,
                use_news_filter=True,
            )
            res = sim.run()
            mode_desc = f"Fixed002" if l_mode == "fixed" else f"{r_pct*100:.2f}%"
            print(
                f"{m_name.upper()} ({mode_desc}) RR 1:{rr}: Trades={res.get('total_trades')} | "
                f"TP_WR={res.get('tp_win_rate_pct')}% | NonLoss={res.get('non_loss_rate_pct')}% | "
                f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R",
                flush=True,
            )
            results.append({"category": f"RR Comparison ({m_name}_{mode_desc})", "config": f"1:{rr}", "result": res})

    print("\n>>> 5. COMPARING ENTRY MODES (Market vs Limit FVG) <<<", flush=True)
    for m_name, r_pct in [("model_b", 0.0025), ("model_a", 0.0050), ("model_a_plus_b", 0.0050)]:
        for em in ["market", "limit_fvg"]:
            sim = IntradaySimulator(
                df_m5,
                df_m15,
                df_h1,
                model=m_name,
                risk_pct=r_pct,
                rr_target=2.0,
                entry_mode=em,
                use_auto_be=True,
                use_news_filter=True,
            )
            res = sim.run()
            diag = res.get("diagnostics", {})
            print(
                f"{m_name.upper()} Entry {em}: Trades={res.get('total_trades')} "
                f"(Unfilled={diag.get('orders_unfilled_expired', 0)}) | "
                f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')}",
                flush=True,
            )
            results.append({"category": "Entry Mode Comparison", "config": f"{m_name}_{em}", "result": res})

    print("\n>>> 6. COMPARING POSITION MANAGEMENT (No BE vs Auto-BE vs BE + Partials) <<<", flush=True)
    for m_name, r_pct in [("model_b", 0.0025), ("model_a", 0.0050), ("model_a_plus_b", 0.0050)]:
        for be, part, desc in [
            (False, False, "No BE / No Partials"),
            (True, False, "Auto-BE at 1.0R Only"),
            (True, True, "Auto-BE + 50% Partials at 1.0R"),
        ]:
            sim = IntradaySimulator(
                df_m5,
                df_m15,
                df_h1,
                model=m_name,
                risk_pct=r_pct,
                rr_target=2.0,
                entry_mode="market",
                use_auto_be=be,
                use_partial_close=part,
                use_news_filter=True,
            )
            res = sim.run()
            print(
                f"{m_name.upper()} Management ({desc}): Trades={res.get('total_trades')} | "
                f"TP_WR={res.get('tp_win_rate_pct')}% | NonLoss={res.get('non_loss_rate_pct')}% | "
                f"PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')}",
                flush=True,
            )
            results.append({"category": f"Management Comparison ({m_name})", "config": desc, "result": res})

    print("\n>>> 7. FILTER ABLATION STUDY (Section 20.7) <<<", flush=True)
    for m_name, r_pct in [("model_b", 0.0025), ("model_a_plus_b", 0.0050)]:
        ablations = [
            (f"Full {m_name} (All Filters ON)", True, True, True, True),
            (f"{m_name} Ablation 1: No KillZone Filter (24h Intraday)", False, True, True, True),
            (f"{m_name} Ablation 2: No H1 Bias Filter", True, False, True, True),
            (f"{m_name} Ablation 3: No Discount/Premium Filter", True, True, False, True),
            (f"{m_name} Ablation 4: No Auto-BE Protection", True, True, True, False),
        ]
        for ab_label, kz_f, h1_f, dp_f, be_f in ablations:
            sim = IntradaySimulator(
                df_m5,
                df_m15,
                df_h1,
                model=m_name,
                risk_pct=r_pct,
                rr_target=2.0,
                entry_mode="market",
                use_auto_be=be_f,
                use_killzone_filter=kz_f,
                use_h1_bias_filter=h1_f,
                use_discount_premium_filter=dp_f,
                use_news_filter=True,
            )
            res = sim.run()
            print(
                f"{ab_label}: Trades={res.get('total_trades')} | PF={res.get('profit_factor')} | "
                f"Net=${res.get('net_profit_usd')} | MaxDD=${res.get('max_total_dd_usd')} | "
                f"Passed={res.get('fundingpips_rules_passed')}",
                flush=True,
            )
            results.append({"category": "Filter Ablation Study", "config": ab_label, "result": res})

    print("\n>>> 8. TRANSACTION COST & SLIPPAGE STRESS TESTS (Section 20.11) <<<", flush=True)
    for m_name, r_pct in [("model_b", 0.0025), ("model_a", 0.0050), ("model_a_plus_b", 0.0050)]:
        stress_scenarios = [
            (f"{m_name} Base Costs ($0.35 Spread, $0.00 Slippage)", 0.35, 0.00),
            (f"{m_name} Moderate Stress ($0.50 Spread, $0.15 Slippage)", 0.50, 0.15),
            (f"{m_name} Severe Stress ($0.75 Spread, $0.30 Slippage)", 0.75, 0.30),
        ]
        for st_label, sp_usd, slp_usd in stress_scenarios:
            sim = IntradaySimulator(
                df_m5,
                df_m15,
                df_h1,
                model=m_name,
                risk_pct=r_pct,
                rr_target=2.0,
                entry_mode="market",
                use_auto_be=True,
                spread_usd=sp_usd,
                slippage_usd=slp_usd,
                use_news_filter=True,
            )
            res = sim.run()
            print(
                f"{st_label}: Trades={res.get('total_trades')} | PF={res.get('profit_factor')} | "
                f"Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R | "
                f"MaxDD=${res.get('max_total_dd_usd')}",
                flush=True,
            )
            results.append({"category": "Cost Stress Test", "config": st_label, "result": res})

    print("\n>>> 9. MONTE CARLO PROP DESK CHALLENGE BREACH & PASS SIMULATION (10,000 ITERATIONS) <<<", flush=True)
    mc_configs = [
        ("model_b", "dynamic", 0.02, 0.0025, 2.0, "Model B @ 0.25% (RR 1:2.0)"),
        ("model_b", "dynamic", 0.02, 0.0025, 1.5, "Model B @ 0.25% (RR 1:1.5)"),
        ("model_a", "dynamic", 0.02, 0.0050, 2.0, "Model A @ 0.50% (RR 1:2.0)"),
        ("model_a", "dynamic", 0.02, 0.0050, 1.5, "Model A @ 0.50% (RR 1:1.5)"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0025, 2.0, "Model A+B @ 0.25% (RR 1:2.0)"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0050, 2.0, "Model A+B @ 0.50% (RR 1:2.0)"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0050, 1.5, "Model A+B @ 0.50% (RR 1:1.5)"),
        ("model_a_plus_b", "dynamic", 0.02, 0.0050, 2.5, "Model A+B @ 0.50% (RR 1:2.5)"),
        ("model_a_plus_b", "fixed", 0.02, 0.0050, 2.0, "Model A+B Fixed Lot 0.02 (RR 1:2.0)"),
        ("model_a_plus_b", "fixed", 0.02, 0.0050, 1.5, "Model A+B Fixed Lot 0.02 (RR 1:1.5)"),
        ("model_a_plus_b", "fixed", 0.02, 0.0050, 2.5, "Model A+B Fixed Lot 0.02 (RR 1:2.5)"),
    ]
    mc_results = []
    for m_name, l_mode, f_lot, r_pct, rr, mc_label in mc_configs:
        sim = IntradaySimulator(
            df_m5,
            df_m15,
            df_h1,
            model=m_name,
            risk_pct=r_pct,
            rr_target=rr,
            entry_mode="market",
            use_auto_be=True,
            lot_mode=l_mode,
            fixed_lot=f_lot,
            use_news_filter=True,
        )
        # Capture raw trades
        sim_res = sim.run()
        # Re-run or extract trade objects
        # We can extract trades from sim
        # Let's run MC on the sim's internal trades
        mc_stat = run_monte_carlo_for_trades(getattr(sim, "_last_trades", []))
        print(
            f"MC [{mc_label}]: Phase1 Pass={mc_stat['phase1_pass_pct']}% | "
            f"Joint Pass={mc_stat['joint_pass_pct']}% | Daily Breach={mc_stat['daily_breach_pct']}% | "
            f"MaxDD Breach={mc_stat['max_dd_breach_pct']}% | Streak 95th={mc_stat['loss_streak_p95']}",
            flush=True,
        )
        mc_results.append({"config": mc_label, "metrics": mc_stat, "backtest_summary": {
            "trades": sim_res.get("total_trades"),
            "pf": sim_res.get("profit_factor"),
            "net_usd": sim_res.get("net_profit_usd"),
            "max_dd_usd": sim_res.get("max_total_dd_usd"),
        }})
        results.append({"category": "Monte Carlo Challenge Simulation", "config": mc_label, "result": mc_stat})

    data_out_path = os.path.join(ROOT_DIR, "data", "intraday_suite_results.json")
    docs_out_path = os.path.join(ROOT_DIR, "docs", "intraday_suite_results.json")
    os.makedirs(os.path.dirname(data_out_path), exist_ok=True)
    os.makedirs(os.path.dirname(docs_out_path), exist_ok=True)

    with open(data_out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(docs_out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nAll simulation results saved to:\n  - {data_out_path}\n  - {docs_out_path}", flush=True)


if __name__ == "__main__":
    run_full_suite()
