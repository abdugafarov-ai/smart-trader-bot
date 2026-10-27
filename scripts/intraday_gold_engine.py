#!/usr/bin/env python3
"""
High-Performance Quantitative Intraday Gold (XAUUSD) Research & Backtesting Engine
Strictly Intraday, Zero-Lookahead, M5/M15/H1 Multi-Timeframe Architecture
Optimized with np.searchsorted for instant O(log N) temporal alignment.

Models Tested:
- Model A: Liquidity Sweep -> Displacement -> MSS/CHOCH -> FVG Entry
- Model B: Trend Pullback -> Order Block in Discount/Premium -> M5 Confirmation
- Model C: Breakout + Momentum -> Retest of Structural Flip

Risk & Prop Rules:
- Initial Balance: $5,000.00
- Risk per trade: 0.25% ($12.50) vs 0.50% ($25.00)
- Hard Min Lot Check: If 0.01 * sl_dist * 100 > risk_budget -> NO TRADE (Reject)
- Prop Daily DD Limit: $200.00 (4.0%)
- Prop Max Floor: $4,400.00 ($600 / 12.0%)
- Transaction Costs: Spread = $0.35/oz, Commission = $3.00/lot ($0.03 per 0.01 lot)
- Strictly Intraday: Killzones only (London 07:00-10:30 UTC, NY 12:30-15:30 UTC), EOD Flat at 20:30 UTC
"""

import os
import sys
import json
import math
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

SPREAD_USD = 0.35            # $0.35 / oz ($35.00 per standard lot)
COMMISSION_PER_LOT = 3.00    # $3.00 / lot ($0.03 per 0.01 lot)
TICK_SIZE = 0.01
TICK_VALUE = 1.00            # $1.00 per 1.0 lot per 0.01 move = $100 per $1.00 move
CONTRACT_SIZE = 100.0        # 1 lot = 100 oz

INITIAL_BALANCE = 5000.0
DAILY_LOSS_LIMIT = 200.0
STATIC_FLOOR = 4400.0

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gold_candles")


def load_candles():
    m5_file = os.path.join(DATA_DIR, "candles_XAUUSD_M5.csv")
    m15_file = os.path.join(DATA_DIR, "candles_XAUUSD_M15.csv")
    h1_file = os.path.join(DATA_DIR, "candles_XAUUSD_H1.csv")

    if not os.path.exists(m5_file):
        raise FileNotFoundError(f"Missing M5 candle file: {m5_file}")

    print("Loading M5 candles...", flush=True)
    df_m5 = pd.read_csv(m5_file)
    df_m5['time'] = pd.to_datetime(df_m5['timestamp'])
    df_m5 = df_m5.sort_values('time').reset_index(drop=True)

    print("Loading M15 candles...", flush=True)
    df_m15 = pd.read_csv(m15_file)
    df_m15['time'] = pd.to_datetime(df_m15['timestamp'])
    df_m15 = df_m15.sort_values('time').reset_index(drop=True)

    print("Loading H1 candles...", flush=True)
    df_h1 = pd.read_csv(h1_file)
    df_h1['time'] = pd.to_datetime(df_h1['timestamp'])
    df_h1 = df_h1.sort_values('time').reset_index(drop=True)

    return df_m5, df_m15, df_h1


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def is_killzone(dt: datetime) -> bool:
    if dt.weekday() > 4:
        return False
    if dt.weekday() == 4 and dt.hour >= 19:
        return False
    minute_of_day = dt.hour * 60 + dt.minute
    london_active = (7 * 60 <= minute_of_day <= 10 * 60 + 30)
    ny_active = (12 * 60 + 30 <= minute_of_day <= 15 * 60 + 30)
    return london_active or ny_active


def is_eod_cutoff(dt: datetime) -> bool:
    return (dt.hour == 20 and dt.minute >= 30) or (dt.hour > 20)


def calculate_lot(balance: float, risk_pct: float, sl_distance_usd: float) -> float:
    if sl_distance_usd <= 0.01:
        return 0.0
    risk_budget = balance * risk_pct
    min_lot_risk = 0.01 * sl_distance_usd * 100.0
    if min_lot_risk > risk_budget:
        return 0.0  # Min lot risk exceeds budget -> REJECT

    raw_lot = risk_budget / (sl_distance_usd * 100.0)
    lot = math.floor(raw_lot * 100.0) / 100.0
    return max(0.01, min(0.50, lot))


class IntradaySimulator:
    def __init__(
        self,
        df_m5: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_h1: pd.DataFrame,
        model: str = "model_a",
        risk_pct: float = 0.0025,
        rr_target: float = 2.0,
        entry_mode: str = "market",
        sl_buffer_mode: str = "structural",
        use_auto_be: bool = True,
        use_partial_close: bool = False,
        sample_split_date: str = "2026-06-01"
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
        self.split_date = pd.to_datetime(sample_split_date)

    def run(self):
        df_m5 = self.df_m5
        df_m15 = self.df_m15
        df_h1 = self.df_h1

        m5_times = df_m5['time'].values
        m15_times = df_m15['time'].values
        h1_times = df_h1['time'].values

        m5_opens = df_m5['open'].values
        m5_highs = df_m5['high'].values
        m5_lows = df_m5['low'].values
        m5_closes = df_m5['close'].values

        m15_opens = df_m15['open'].values
        m15_highs = df_m15['high'].values
        m15_lows = df_m15['low'].values
        m15_closes = df_m15['close'].values
        m15_atrs = df_m15['atr'].values

        h1_highs = df_h1['high'].values
        h1_lows = df_h1['low'].values
        h1_closes = df_h1['close'].values

        trades = []
        open_pos = None
        pending_order = None

        balance = INITIAL_BALANCE
        equity = balance
        peak_equity = balance
        current_day = None
        day_start_equity = balance

        max_daily_dd = 0.0
        max_total_dd = 0.0
        daily_breaches = 0
        floor_breaches = 0

        td_15m = np.timedelta64(15, 'm')
        td_1h = np.timedelta64(1, 'h')

        n_m5 = len(df_m5)

        for i in range(100, n_m5):
            t_np = m5_times[i]
            dt = pd.Timestamp(t_np).to_pydatetime()

            bar_date = dt.date()
            if bar_date != current_day:
                current_day = bar_date
                day_start_equity = max(balance, equity)

            b_open = m5_opens[i]
            b_high = m5_highs[i]
            b_low = m5_lows[i]
            b_close = m5_closes[i]

            # 1. Manage open position
            if open_pos is not None:
                pos_type = open_pos['type']
                pos_entry = open_pos['entry']
                pos_sl = open_pos['sl']
                pos_tp = open_pos['tp']
                pos_lot = open_pos['lot']
                r_dist = open_pos['r_dist']

                closed = False
                close_price = 0.0
                close_reason = ""

                # EOD Flat
                if is_eod_cutoff(dt):
                    closed = True
                    close_price = b_close
                    close_reason = "EOD_FLAT"
                elif pos_type == "BUY" and b_low <= pos_sl:
                    closed = True
                    close_price = pos_sl
                    close_reason = "SL"
                elif pos_type == "SELL" and b_high >= pos_sl:
                    closed = True
                    close_price = pos_sl
                    close_reason = "SL"
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
                        if self.use_auto_be and not open_pos['be_moved']:
                            be_level = pos_entry + (SPREAD_USD if pos_type == "BUY" else -SPREAD_USD)
                            open_pos['sl'] = be_level
                            open_pos['be_moved'] = True

                        if self.use_partial_close and not open_pos['partial_taken'] and pos_lot >= 0.02:
                            partial_lot = round(pos_lot / 2.0, 2)
                            p_gain = r_dist * partial_lot * 100.0
                            comm = partial_lot * COMMISSION_PER_LOT
                            net_p = p_gain - comm
                            balance += net_p
                            open_pos['lot'] -= partial_lot
                            open_pos['partial_taken'] = True
                            open_pos['realized_partial'] = net_p

                if closed:
                    diff = (close_price - pos_entry) if pos_type == "BUY" else (pos_entry - close_price)
                    gross_pnl = diff * open_pos['lot'] * 100.0
                    comm = open_pos['lot'] * COMMISSION_PER_LOT
                    net_pnl = gross_pnl - comm

                    total_trade_pnl = net_pnl + open_pos.get('realized_partial', 0.0)
                    balance += net_pnl
                    equity = balance

                    r_mult = total_trade_pnl / open_pos['risk_usd']

                    trades.append({
                        "entry_time": open_pos['entry_time'],
                        "exit_time": dt,
                        "type": pos_type,
                        "lot": open_pos['orig_lot'],
                        "entry": pos_entry,
                        "exit": close_price,
                        "sl": open_pos['orig_sl'],
                        "tp": pos_tp,
                        "reason": close_reason,
                        "pnl_usd": round(total_trade_pnl, 2),
                        "r_multiple": round(r_mult, 2),
                        "balance_after": round(balance, 2),
                        "in_sample": bool(open_pos['entry_time'] < self.split_date)
                    })
                    open_pos = None

            # Track drawdowns
            cur_daily_dd = day_start_equity - balance
            if cur_daily_dd > max_daily_dd:
                max_daily_dd = cur_daily_dd
            if cur_daily_dd >= DAILY_LOSS_LIMIT:
                daily_breaches += 1

            if balance > peak_equity:
                peak_equity = balance
            cur_tot_dd = peak_equity - balance
            if cur_tot_dd > max_total_dd:
                max_total_dd = cur_tot_dd
            if balance <= STATIC_FLOOR:
                floor_breaches += 1

            # Manage pending limit orders
            if pending_order is not None:
                p_age = i - pending_order['bar_index']
                if p_age > 6 or not is_killzone(dt):
                    pending_order = None
                else:
                    p_type = pending_order['type']
                    p_limit = pending_order['limit_price']
                    filled = False
                    if p_type == "BUY" and b_low <= p_limit:
                        filled = True
                        actual_entry = p_limit + SPREAD_USD
                    elif p_type == "SELL" and b_high >= p_limit:
                        filled = True
                        actual_entry = p_limit - SPREAD_USD

                    if filled:
                        open_pos = {
                            "type": p_type,
                            "entry": actual_entry,
                            "sl": pending_order['sl'],
                            "tp": pending_order['tp'],
                            "lot": pending_order['lot'],
                            "orig_lot": pending_order['lot'],
                            "orig_sl": pending_order['sl'],
                            "r_dist": pending_order['r_dist'],
                            "risk_usd": pending_order['risk_usd'],
                            "entry_time": dt,
                            "be_moved": False,
                            "partial_taken": False
                        }
                        pending_order = None

            # Signal scanning (if flat and in killzone)
            if open_pos is None and pending_order is None and is_killzone(dt):
                # Binary search for closed M15 and H1 bars
                m15_end = np.searchsorted(m15_times, t_np - td_15m, side='right')
                h1_end = np.searchsorted(h1_times, t_np - td_1h, side='right')

                if m15_end < 25 or h1_end < 20:
                    continue

                # H1 Bias
                h1_h_slice = h1_highs[h1_end - 20 : h1_end]
                h1_l_slice = h1_lows[h1_end - 20 : h1_end]
                h1_eq = (np.max(h1_h_slice) + np.min(h1_l_slice)) / 2.0
                h1_last_c = h1_closes[h1_end - 1]

                h1_bull = h1_last_c > h1_eq
                h1_bear = h1_last_c < h1_eq

                # M15 Setup
                m15_h_slice = m15_highs[m15_end - 15 : m15_end]
                m15_l_slice = m15_lows[m15_end - 15 : m15_end]
                m15_c_slice = m15_closes[m15_end - 15 : m15_end]
                m15_o_slice = m15_opens[m15_end - 15 : m15_end]

                m15_prev_h = np.max(m15_highs[m15_end - 12 : m15_end - 2])
                m15_prev_l = np.min(m15_lows[m15_end - 12 : m15_end - 2])

                last_m15_c = m15_closes[m15_end - 1]
                cur_atr = m15_atrs[m15_end - 1]
                if np.isnan(cur_atr):
                    cur_atr = 3.5

                sig = None

                if self.model == "model_a":
                    # Model A: Sweep -> Displacement -> MSS -> FVG
                    bull_swp = (np.min(m15_l_slice) < m15_prev_l) and (last_m15_c > m15_prev_l)
                    bear_swp = (np.max(m15_h_slice) > m15_prev_h) and (last_m15_c < m15_prev_h)

                    # M15 3-bar FVG
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
                        entry = b_close
                        limit_p = (c1_h + c3_l) / 2.0
                        sig = {"type": "BUY", "entry": entry, "sl": sl, "limit_price": limit_p}

                    elif bear_swp and bear_fvg and h1_bear:
                        sl = np.max(m15_h_slice) + (0.50 if self.sl_buffer_mode == "structural" else 1.2 * cur_atr)
                        entry = b_close
                        limit_p = (c1_l + c3_h) / 2.0
                        sig = {"type": "SELL", "entry": entry, "sl": sl, "limit_price": limit_p}

                elif self.model == "model_b":
                    # Model B: Trend Pullback -> Order Block in Discount/Premium
                    m15_eq = (np.max(m15_h_slice) + np.min(m15_l_slice)) / 2.0
                    in_discount = last_m15_c < m15_eq
                    in_premium = last_m15_c > m15_eq

                    # Find OB (last opposing candle in last 5 M15 bars)
                    down_idx = [k for k in range(m15_end - 6, m15_end - 1) if m15_closes[k] < m15_opens[k]]
                    up_idx = [k for k in range(m15_end - 6, m15_end - 1) if m15_closes[k] > m15_opens[k]]

                    if h1_bull and in_discount and down_idx:
                        ob_k = down_idx[-1]
                        ob_h = m15_highs[ob_k]
                        ob_l = m15_lows[ob_k]
                        if b_close > b_open and b_low <= ob_h:
                            sl = ob_l - 0.50
                            sig = {"type": "BUY", "entry": b_close, "sl": sl, "limit_price": ob_h}

                    elif h1_bear and in_premium and up_idx:
                        ob_k = up_idx[-1]
                        ob_h = m15_highs[ob_k]
                        ob_l = m15_lows[ob_k]
                        if b_close < b_open and b_high >= ob_l:
                            sl = ob_h + 0.50
                            sig = {"type": "SELL", "entry": b_close, "sl": sl, "limit_price": ob_l}

                elif self.model == "model_c":
                    # Model C: Breakout + Retest
                    broke_h = (last_m15_c > m15_prev_h) and ((m15_highs[m15_end - 1] - m15_lows[m15_end - 1]) > 1.0 * cur_atr)
                    broke_l = (last_m15_c < m15_prev_l) and ((m15_highs[m15_end - 1] - m15_lows[m15_end - 1]) > 1.0 * cur_atr)

                    if broke_h and h1_bull:
                        if b_low <= m15_prev_h <= b_high and b_close > m15_prev_h:
                            sl = b_low - 0.50
                            sig = {"type": "BUY", "entry": b_close, "sl": sl, "limit_price": m15_prev_h}

                    elif broke_l and h1_bear:
                        if b_low <= m15_prev_l <= b_high and b_close < m15_prev_l:
                            sl = b_high + 0.50
                            sig = {"type": "SELL", "entry": b_close, "sl": sl, "limit_price": m15_prev_l}

                if sig:
                    sig_type = sig['type']
                    sig_entry = sig['entry']
                    sig_sl = sig['sl']
                    sl_dist = abs(sig_entry - sig_sl)

                    lot = calculate_lot(balance, self.risk_pct, sl_dist)
                    if lot > 0.0:
                        risk_usd = balance * self.risk_pct
                        r_dist = sl_dist
                        tp = sig_entry + (r_dist * self.rr_target if sig_type == "BUY" else -r_dist * self.rr_target)

                        if self.entry_mode == "market":
                            exec_price = sig_entry + (SPREAD_USD if sig_type == "BUY" else -SPREAD_USD)
                            open_pos = {
                                "type": sig_type,
                                "entry": exec_price,
                                "sl": sig_sl,
                                "tp": tp,
                                "lot": lot,
                                "orig_lot": lot,
                                "orig_sl": sig_sl,
                                "r_dist": r_dist,
                                "risk_usd": risk_usd,
                                "entry_time": dt,
                                "be_moved": False,
                                "partial_taken": False
                            }
                        elif self.entry_mode == "limit_fvg":
                            pending_order = {
                                "type": sig_type,
                                "limit_price": sig.get('limit_price', sig_entry),
                                "sl": sig_sl,
                                "tp": tp,
                                "lot": lot,
                                "r_dist": r_dist,
                                "risk_usd": risk_usd,
                                "bar_index": i
                            }

        return self._compile_metrics(trades, max_daily_dd, max_total_dd, daily_breaches, floor_breaches)

    def _compile_metrics(self, trades, max_daily_dd, max_total_dd, daily_breaches, floor_breaches):
        if not trades:
            return {
                "model": self.model.upper(),
                "total_trades": 0,
                "status": "NO_TRADES"
            }

        df_t = pd.DataFrame(trades)
        wins = df_t[df_t['pnl_usd'] > 0]
        losses = df_t[df_t['pnl_usd'] <= 0]

        total_trades = len(df_t)
        win_rate = (len(wins) / total_trades) * 100.0 if total_trades > 0 else 0.0

        gross_profit = wins['pnl_usd'].sum() if len(wins) > 0 else 0.0
        gross_loss = abs(losses['pnl_usd'].sum()) if len(losses) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.0

        net_profit = df_t['pnl_usd'].sum()
        total_r = df_t['r_multiple'].sum()
        exp_r = total_r / total_trades if total_trades > 0 else 0.0
        exp_usd = net_profit / total_trades if total_trades > 0 else 0.0

        longs = df_t[df_t['type'] == "BUY"]
        shorts = df_t[df_t['type'] == "SELL"]

        is_trades = df_t[df_t['in_sample'] == True]
        oos_trades = df_t[df_t['in_sample'] == False]

        def sub_metrics(sub_df):
            if sub_df.empty:
                return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "net_usd": 0.0, "exp_r": 0.0}
            w = sub_df[sub_df['pnl_usd'] > 0]
            l = sub_df[sub_df['pnl_usd'] <= 0]
            gp = w['pnl_usd'].sum() if len(w) > 0 else 0.0
            gl = abs(l['pnl_usd'].sum()) if len(l) > 0 else 0.0
            pf = (gp / gl) if gl > 0 else 99.0
            return {
                "trades": len(sub_df),
                "win_rate": round((len(w) / len(sub_df)) * 100.0, 1),
                "pf": round(pf, 2),
                "net_usd": round(sub_df['pnl_usd'].sum(), 2),
                "exp_r": round(sub_df['r_multiple'].mean(), 2)
            }

        streak = 0
        max_streak = 0
        for p in df_t['pnl_usd']:
            if p <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0

        res = {
            "model": self.model.upper(),
            "risk_pct": f"{self.risk_pct*100:.2f}%",
            "rr_target": f"1:{self.rr_target}",
            "entry_mode": self.entry_mode,
            "auto_be": self.use_auto_be,
            "partial_close": self.use_partial_close,
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "net_profit_usd": round(net_profit, 2),
            "total_r": round(total_r, 2),
            "expectancy_r": round(exp_r, 2),
            "expectancy_usd": round(exp_usd, 2),
            "max_daily_dd_usd": round(max_daily_dd, 2),
            "max_total_dd_usd": round(max_total_dd, 2),
            "max_loss_streak": max_streak,
            "fundingpips_rules_passed": bool(daily_breaches == 0 and floor_breaches == 0),
            "long_metrics": sub_metrics(longs),
            "short_metrics": sub_metrics(shorts),
            "in_sample": sub_metrics(is_trades),
            "out_of_sample": sub_metrics(oos_trades)
        }
        return res


def run_full_suite():
    df_m5, df_m15, df_h1 = load_candles()
    df_m15['atr'] = calculate_atr(df_m15, 14)
    df_h1['atr'] = calculate_atr(df_h1, 14)

    results = []

    print("\n>>> 1. COMPARING ENTRY MODELS (A, B, C) <<<", flush=True)
    for m in ["model_a", "model_b", "model_c"]:
        sim = IntradaySimulator(df_m5, df_m15, df_h1, model=m, risk_pct=0.0025, rr_target=2.0, entry_mode="market", use_auto_be=True)
        res = sim.run()
        print(f"Model {m.upper()}: Trades={res.get('total_trades')} | WR={res.get('win_rate_pct')}% | PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R | Passed={res.get('fundingpips_rules_passed')}", flush=True)
        results.append({"category": "Model Comparison", "config": m, "result": res})

    print("\n>>> 2. COMPARING RISK SIZING (0.25% vs 0.50%) <<<", flush=True)
    for r in [0.0025, 0.0050]:
        sim = IntradaySimulator(df_m5, df_m15, df_h1, model="model_a", risk_pct=r, rr_target=2.0, entry_mode="market", use_auto_be=True)
        res = sim.run()
        print(f"Risk {r*100:.2f}%: Trades={res.get('total_trades')} | Net=${res.get('net_profit_usd')} | MaxDailyDD=${res.get('max_daily_dd_usd')} | MaxTotDD=${res.get('max_total_dd_usd')} | Passed={res.get('fundingpips_rules_passed')}", flush=True)
        results.append({"category": "Risk Comparison", "config": f"{r*100:.2f}%", "result": res})

    print("\n>>> 3. COMPARING RR TARGETS (1:1.5 vs 1:2.0 vs 1:2.5) <<<", flush=True)
    for rr in [1.5, 2.0, 2.5]:
        sim = IntradaySimulator(df_m5, df_m15, df_h1, model="model_a", risk_pct=0.0025, rr_target=rr, entry_mode="market", use_auto_be=True)
        res = sim.run()
        print(f"RR 1:{rr}: Trades={res.get('total_trades')} | WR={res.get('win_rate_pct')}% | PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R", flush=True)
        results.append({"category": "RR Comparison", "config": f"1:{rr}", "result": res})

    print("\n>>> 4. COMPARING ENTRY MODES (Market vs Limit FVG) <<<", flush=True)
    for em in ["market", "limit_fvg"]:
        sim = IntradaySimulator(df_m5, df_m15, df_h1, model="model_a", risk_pct=0.0025, rr_target=2.0, entry_mode=em, use_auto_be=True)
        res = sim.run()
        print(f"Entry {em}: Trades={res.get('total_trades')} | WR={res.get('win_rate_pct')}% | PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R", flush=True)
        results.append({"category": "Entry Mode Comparison", "config": em, "result": res})

    print("\n>>> 5. COMPARING POSITION MANAGEMENT (No BE vs Auto-BE vs BE + Partials) <<<", flush=True)
    for be, part, desc in [(False, False, "No BE / No Partials"), (True, False, "Auto-BE only"), (True, True, "Auto-BE + 50% Partials")]:
        sim = IntradaySimulator(df_m5, df_m15, df_h1, model="model_a", risk_pct=0.0025, rr_target=2.0, entry_mode="market", use_auto_be=be, use_partial_close=part)
        res = sim.run()
        print(f"Management ({desc}): Trades={res.get('total_trades')} | WR={res.get('win_rate_pct')}% | PF={res.get('profit_factor')} | Net=${res.get('net_profit_usd')} | ExpR={res.get('expectancy_r')}R", flush=True)
        results.append({"category": "Management Comparison", "config": desc, "result": res})

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "intraday_suite_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nAll simulation results successfully saved to {out_path}!", flush=True)


if __name__ == "__main__":
    run_full_suite()
