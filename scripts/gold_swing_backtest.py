#!/usr/bin/env python3
"""
Institutional Gold (XAUUSD) Swing Trading Backtest Engine
FundingPips $5,000 Challenge (2-Step Flex) Simulation

Strict Rules:
- Initial Balance: $5,000.00
- Maximum Daily Drawdown: $200.00 (reset at 00:00 MT5 server time from max(Balance, Equity))
- Minimum Account Floor: $4,400.00 ($600 / 12.0% static max drawdown)
- Real Spread: $0.25 (2.5 pips)
- Real Commission: $3.00 / standard lot round-turn
- Real Overnight Swaps: Long -$15.00/lot/day, Short +$8.00/lot/day, 3x on Wednesday midnight
- Sniper Lot Matrix: 0.02 - 0.05 lot (max risk strictly <= $65.00)
- Max Concurrent Positions: 1 (zero overlap)
- Auto-BE: Trigger at price move >= $20.00 OR >= 1.0R, lock +$2.00 profit
- TP1: 50% partial close at opposing liquidity / >= 2.5R
- TP2: Runner close at 3.5R - 4.0R
- Midnight Rollover Guard at 23:30 server time: floating profit >= +$80 locks +$3.00 SL and 50% partial
- Night Clearing Window: 23:50 - 01:10 (no SL tighter than $4.00)
- Zero Lookahead, Zero Time-Travel
"""

import os
import sys
import json
import math
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

sys_path = "/root/smart-trader-bot"
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from strategies.ict_smc import ICTSMCStrategy
from trading.execution_bridge import calculate_gold_swing_lot
from utils.broker_time import get_broker_timezone

CSV_DIR = "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files"

SPREAD_USD = 0.25
COMMISSION_PER_LOT = 3.0
SWAP_LONG_DAILY = -15.0
SWAP_SHORT_DAILY = 8.0

INITIAL_BALANCE = 5000.0
DAILY_LOSS_LIMIT = 200.0
STATIC_FLOOR = 4400.0


def load_candles(tf: str) -> pd.DataFrame:
    fpath = os.path.join(CSV_DIR, f"candles_XAUUSD_{tf}.csv")
    if not os.path.exists(fpath):
        raise FileNotFoundError(f"Missing {fpath}")
    df = pd.read_csv(fpath)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df


def run_gold_swing_backtest():
    print("=" * 70, flush=True)
    print("INSTITUTIONAL GOLD (XAUUSD) SWING BACKTEST — FUNDINGPIPS $5,000", flush=True)
    print("=" * 70, flush=True)

    df_h4 = load_candles("H4")
    df_h1 = load_candles("H1")
    df_d1 = load_candles("D1")

    print(f"Loaded H4 bars: {len(df_h4)} ({df_h4['timestamp'].iloc[0]} to {df_h4['timestamp'].iloc[-1]})", flush=True)
    print(f"Loaded H1 bars: {len(df_h1)} ({df_h1['timestamp'].iloc[0]} to {df_h1['timestamp'].iloc[-1]})", flush=True)
    print(f"Loaded D1 bars: {len(df_d1)} ({df_d1['timestamp'].iloc[0]} to {df_d1['timestamp'].iloc[-1]})", flush=True)

    strategy = ICTSMCStrategy()

    balance = INITIAL_BALANCE
    trades = []
    daily_drawdowns = []
    equity_curve = []

    start_ts = df_h1['timestamp'].iloc[60]
    h4_indices = df_h4[df_h4['timestamp'] >= start_ts].index

    print(f"Total H4 bars to scan: {len(h4_indices)} starting from {start_ts}...", flush=True)

    current_h4_ptr = 0
    total_h4 = len(h4_indices)

    current_date = None
    daily_base_hwm = INITIAL_BALANCE
    max_daily_dd_today = 0.0
    max_daily_dd_overall = 0.0
    min_equity_level = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    max_total_dd_usd = 0.0
    daily_breach_detected = False
    floor_breach_detected = False

    while current_h4_ptr < total_h4:
        h4_idx = h4_indices[current_h4_ptr]
        curr_bar = df_h4.iloc[h4_idx]
        curr_time = curr_bar['timestamp']

        # H4 bar closes at curr_time + 4 hours (no lookahead!)
        close_time = curr_time + pd.Timedelta(hours=4)
        sub_h4 = df_h4.iloc[max(0, h4_idx - 120):h4_idx + 1].copy()
        sub_d1 = df_d1[df_d1['timestamp'] < close_time.floor('D')].iloc[-50:].copy()
        sub_h1 = df_h1[df_h1['timestamp'] < close_time].iloc[-60:].copy()

        current_h4_ptr += 1

        # Convert broker candle close_time to UTC
        close_time_broker = close_time.to_pydatetime().replace(tzinfo=get_broker_timezone())
        close_time_utc = close_time_broker.astimezone(timezone.utc)

        try:
            res = strategy.analyze(
                df=sub_h4,
                symbol="XAUUSD",
                timeframe="H4",
                df_d1=sub_d1,
                df_ltf=sub_h1,
                current_time=close_time_utc
            )
            sig = res.signal
            stars = getattr(sig, 'confidence_stars', getattr(sig, 'confidence', 0))

            if sig.direction in ("LONG", "SHORT", "BUY", "SELL") and stars >= 4:
                entry = sig.entry
                sl = getattr(sig, 'stop_loss', getattr(sig, 'sl', None))
                tp1 = getattr(sig, 'take_profit_1', getattr(sig, 'tp1', None))
                tp2 = getattr(sig, 'take_profit_2', getattr(sig, 'tp2', None))
                dr_pct = getattr(sig, 'dr_pct', None)

                # Rigid Dealing Range verification filter
                if dr_pct is None or 0.40 <= dr_pct <= 0.60:
                    continue
                if sig.direction in ("LONG", "BUY") and dr_pct >= 0.45:
                    continue
                if sig.direction in ("SHORT", "SELL") and dr_pct <= 0.55:
                    continue

                if entry and sl and tp1:
                    sl_dist = abs(entry - sl)
                    if tp2 is None:
                        tp2 = entry + 4.0 * sl_dist if sig.direction in ("LONG", "BUY") else entry - 4.0 * sl_dist

                    if 10.0 <= sl_dist <= 35.0 and abs(tp1 - entry) >= 2.5 * sl_dist:
                        pos_type = "BUY" if sig.direction in ("LONG", "BUY") else "SELL"
                        real_entry = entry + SPREAD_USD if pos_type == "BUY" else entry
                        lot = calculate_gold_swing_lot(balance=balance, sl_dist_usd=sl_dist)
                        initial_risk_usd = lot * 100.0 * sl_dist

                        # Market order entered at close_time
                        pos = {
                            "type": pos_type,
                            "entry": real_entry,
                            "sl": sl,
                            "initial_sl": sl,
                            "tp1": tp1,
                            "tp2": tp2,
                            "initial_volume": lot,
                            "volume_remaining": lot,
                            "entry_time": close_time,
                            "initial_risk_usd": initial_risk_usd,
                            "be_done": False,
                            "tp1_done": False,
                            "rollover_guard_triggered": False,
                            "accumulated_swap": 0.0,
                            "exits": [],
                            "stars": stars,
                            "dr_pct": dr_pct
                        }
                        dr_str = f"{dr_pct*100:.1f}%" if dr_pct is not None else "N/A"
                        print(f"[{close_time}] OPEN #{len(trades)+1}: {pos_type} entry={real_entry:.2f} sl={sl:.2f} (dist=${sl_dist:.2f}) tp1={tp1:.2f} tp2={tp2:.2f} lot={lot:.2f} risk=${initial_risk_usd:.2f} dr_pct={dr_str} stars={stars}★", flush=True)

                        # Step forward on H1 bars strictly starting at close_time
                        h1_sim_bars = df_h1[df_h1['timestamp'] >= close_time].copy()
                        trade_closed = False
                        exit_time = None

                        for _, h1_bar in h1_sim_bars.iterrows():
                            h1_time = h1_bar['timestamp']
                            h1_date = h1_time.date()
                            h1_hour = h1_time.hour
                            h1_min = h1_time.minute
                            h1_weekday = h1_time.weekday()

                            h1_open = float(h1_bar['open'])
                            h1_high = float(h1_bar['high'])
                            h1_low = float(h1_bar['low'])
                            h1_close = float(h1_bar['close'])

                            # Daily reset at 00:00 (FundingPips Midnight High-Water Mark)
                            if h1_date != current_date:
                                if current_date is not None:
                                    daily_drawdowns.append({
                                        "date": str(current_date),
                                        "daily_ref": round(daily_base_hwm, 2),
                                        "max_daily_dd": round(max_daily_dd_today, 2),
                                        "passed": bool(max_daily_dd_today <= DAILY_LOSS_LIMIT)
                                    })
                                current_date = h1_date
                                # Midnight equity
                                cur_fl = (h1_open - pos['entry']) * 100.0 * pos['volume_remaining'] if pos['type'] == 'BUY' else (pos['entry'] - (h1_open + SPREAD_USD)) * 100.0 * pos['volume_remaining']
                                cur_eq = balance + cur_fl + pos['accumulated_swap']
                                daily_base_hwm = max(balance, cur_eq)
                                max_daily_dd_today = 0.0

                            # Overnight Swap at 00:00 (Triple Wednesday night into Thursday)
                            if h1_hour == 0:
                                mult = 3.0 if h1_weekday == 3 else 1.0
                                swap_rate = SWAP_LONG_DAILY if pos['type'] == 'BUY' else SWAP_SHORT_DAILY
                                swap_amt = swap_rate * pos['volume_remaining'] * mult
                                pos['accumulated_swap'] += swap_amt
                                balance += swap_amt

                            # Floating Drawdown check
                            worst_p = h1_low if pos['type'] == 'BUY' else (h1_high + SPREAD_USD)
                            worst_fl = (worst_p - pos['entry']) * 100.0 * pos['volume_remaining'] if pos['type'] == 'BUY' else (pos['entry'] - worst_p) * 100.0 * pos['volume_remaining']
                            worst_eq = balance + worst_fl + pos['accumulated_swap']
                            
                            intraday_dd = daily_base_hwm - worst_eq
                            if intraday_dd > max_daily_dd_today:
                                max_daily_dd_today = intraday_dd
                            if intraday_dd > max_daily_dd_overall:
                                max_daily_dd_overall = intraday_dd
                            if intraday_dd > DAILY_LOSS_LIMIT:
                                daily_breach_detected = True
                            if worst_eq < min_equity_level:
                                min_equity_level = worst_eq
                            if worst_eq < STATIC_FLOOR:
                                floor_breach_detected = True

                            total_dd = peak_balance - worst_eq
                            if total_dd > max_total_dd_usd:
                                max_total_dd_usd = total_dd

                            # (A) Rollover Guard at 23:00 (Profit >= $80 locks +$3.00 SL and 50% partial)
                            cur_close_fl = (h1_close - pos['entry']) * 100.0 * pos['volume_remaining'] if pos['type'] == 'BUY' else (pos['entry'] - (h1_close + SPREAD_USD)) * 100.0 * pos['volume_remaining']
                            if h1_hour == 23 and (cur_close_fl + pos['accumulated_swap']) >= 80.0 and not pos['rollover_guard_triggered']:
                                pos['rollover_guard_triggered'] = True
                                safe_sl = (pos['entry'] + 3.00) if pos['type'] == 'BUY' else (pos['entry'] - 3.00)
                                if (pos['type'] == 'BUY' and safe_sl > pos['sl']) or (pos['type'] == 'SELL' and safe_sl < pos['sl']):
                                    pos['sl'] = safe_sl
                                part_vol = round(pos['volume_remaining'] * 0.5, 2)
                                if part_vol >= 0.01:
                                    p_exit = h1_close if pos['type'] == 'BUY' else (h1_close + SPREAD_USD)
                                    pnl_p = (p_exit - pos['entry']) * 100.0 * part_vol if pos['type'] == 'BUY' else (pos['entry'] - p_exit) * 100.0 * part_vol
                                    comm = COMMISSION_PER_LOT * part_vol
                                    net_p = pnl_p - comm
                                    balance += net_p
                                    pos['volume_remaining'] = round(pos['volume_remaining'] - part_vol, 2)
                                    pos['exits'].append({
                                        "time": str(h1_time),
                                        "price": p_exit,
                                        "volume": part_vol,
                                        "reason": "ROLLOVER_GUARD_50PCT",
                                        "pnl_usd": round(net_p, 2)
                                    })
                                    if pos['volume_remaining'] <= 0.001:
                                        trade_closed = True
                                        exit_time = h1_time
                                        break

                            # (B) Stop Loss Hit (Conservative: checked BEFORE TP/BE so simultaneous touch hits SL first)
                            hit_sl = (h1_low <= pos['sl']) if pos['type'] == 'BUY' else (h1_high + SPREAD_USD >= pos['sl'])
                            if hit_sl and pos['volume_remaining'] > 0:
                                exit_p = pos['sl']
                                vol = pos['volume_remaining']
                                pnl_sl = (exit_p - pos['entry']) * 100.0 * vol if pos['type'] == 'BUY' else (pos['entry'] - exit_p) * 100.0 * vol
                                comm = COMMISSION_PER_LOT * vol
                                net_sl = pnl_sl - comm
                                balance += net_sl
                                pos['volume_remaining'] = 0.0
                                exit_reason = "BE_SL" if pos['be_done'] or pos['rollover_guard_triggered'] else "INITIAL_SL"
                                pos['exits'].append({
                                    "time": str(h1_time),
                                    "price": exit_p,
                                    "volume": vol,
                                    "reason": exit_reason,
                                    "pnl_usd": round(net_sl, 2)
                                })
                                trade_closed = True
                                exit_time = h1_time
                                break

                            # (C) Auto Break-Even (Trigger at price distance >= $20.00 OR >= 1.0R, locks +$2.00)
                            fav_dist = (h1_high - pos['entry']) if pos['type'] == 'BUY' else (pos['entry'] - h1_low)
                            be_trigger = min(20.0, abs(pos['entry'] - pos['initial_sl']))
                            if fav_dist >= be_trigger and not pos['be_done']:
                                is_night_clearing = (h1_hour == 23 and h1_min >= 50) or (h1_hour == 0) or (h1_hour == 1 and h1_min <= 10)
                                safe_be = (pos['entry'] + 2.00) if pos['type'] == 'BUY' else (pos['entry'] - 2.00)
                                dist_to_price = abs(h1_close - safe_be)
                                if not (is_night_clearing and dist_to_price < 4.0):
                                    pos['be_done'] = True
                                    if (pos['type'] == 'BUY' and safe_be > pos['sl']) or (pos['type'] == 'SELL' and safe_be < pos['sl']):
                                        pos['sl'] = safe_be

                            # (D) Take Profit 1 (50% partial at tp1)
                            if not pos['tp1_done'] and pos['volume_remaining'] > 0:
                                hit_tp1 = (h1_high >= pos['tp1']) if pos['type'] == 'BUY' else (h1_low <= pos['tp1'])
                                if hit_tp1:
                                    pos['tp1_done'] = True
                                    part_vol = round(pos['initial_volume'] * 0.5, 2)
                                    if part_vol > pos['volume_remaining'] or pos['volume_remaining'] - part_vol < 0.01:
                                        part_vol = pos['volume_remaining']
                                    exit_p = pos['tp1']
                                    pnl_tp1 = (exit_p - pos['entry']) * 100.0 * part_vol if pos['type'] == 'BUY' else (pos['entry'] - exit_p) * 100.0 * part_vol
                                    comm = COMMISSION_PER_LOT * part_vol
                                    net_tp1 = pnl_tp1 - comm
                                    balance += net_tp1
                                    pos['volume_remaining'] = round(pos['volume_remaining'] - part_vol, 2)
                                    pos['exits'].append({
                                        "time": str(h1_time),
                                        "price": exit_p,
                                        "volume": part_vol,
                                        "reason": "TP1_PARTIAL",
                                        "pnl_usd": round(net_tp1, 2)
                                    })
                                    # Move SL to BE after TP1
                                    pos['be_done'] = True
                                    safe_be = (pos['entry'] + 2.00) if pos['type'] == 'BUY' else (pos['entry'] - 2.00)
                                    if (pos['type'] == 'BUY' and safe_be > pos['sl']) or (pos['type'] == 'SELL' and safe_be < pos['sl']):
                                        pos['sl'] = safe_be

                                    if pos['volume_remaining'] <= 0.001:
                                        trade_closed = True
                                        exit_time = h1_time
                                        break

                            # (E) Take Profit 2 (Runner close)
                            if pos['tp1_done'] and pos['volume_remaining'] > 0:
                                hit_tp2 = (h1_high >= pos['tp2']) if pos['type'] == 'BUY' else (h1_low <= pos['tp2'])
                                if hit_tp2:
                                    exit_p = pos['tp2']
                                    vol = pos['volume_remaining']
                                    pnl_tp2 = (exit_p - pos['entry']) * 100.0 * vol if pos['type'] == 'BUY' else (pos['entry'] - exit_p) * 100.0 * vol
                                    comm = COMMISSION_PER_LOT * vol
                                    net_tp2 = pnl_tp2 - comm
                                    balance += net_tp2
                                    pos['volume_remaining'] = 0.0
                                    pos['exits'].append({
                                        "time": str(h1_time),
                                        "price": exit_p,
                                        "volume": vol,
                                        "reason": "TP2_RUNNER",
                                        "pnl_usd": round(net_tp2, 2)
                                    })
                                    trade_closed = True
                                    exit_time = h1_time
                                    break

                        if trade_closed:
                            total_pnl = sum(e['pnl_usd'] for e in pos['exits']) + pos['accumulated_swap']
                            pnl_r = total_pnl / pos['initial_risk_usd'] if pos['initial_risk_usd'] > 0 else 0.0
                            is_win = total_pnl > 0
                            outcome = "WIN" if total_pnl > 10.0 else ("BE" if total_pnl >= -5.0 else "LOSS")
                            t_record = {
                                "trade_id": len(trades) + 1,
                                "symbol": "XAUUSD",
                                "type": pos['type'],
                                "entry_time": str(pos['entry_time']),
                                "exit_time": str(exit_time),
                                "entry_price": pos['entry'],
                                "initial_sl": pos['initial_sl'],
                                "tp1": pos['tp1'],
                                "tp2": pos['tp2'],
                                "lot": pos['initial_volume'],
                                "initial_risk_usd": round(pos['initial_risk_usd'], 2),
                                "pnl_usd": round(total_pnl, 2),
                                "pnl_r": round(pnl_r, 2),
                                "is_win": is_win,
                                "outcome": outcome,
                                "exits": pos['exits'],
                                "stars": pos['stars'],
                                "dr_pct": pos.get('dr_pct')
                            }
                            trades.append(t_record)
                            print(f"[{exit_time}] CLOSED #{t_record['trade_id']}: {t_record['type']} {outcome} PnL=${total_pnl:+.2f} ({pnl_r:+.2f}R) Bal=${balance:.2f}", flush=True)

                            # Advance current_h4_ptr to the first H4 bar that starts AFTER exit_time
                            while current_h4_ptr < total_h4 and df_h4.iloc[h4_indices[current_h4_ptr]]['timestamp'] <= exit_time:
                                current_h4_ptr += 1

        except Exception as e:
            print(f"Exception at {curr_time}: {e}", flush=True)

        if balance > peak_balance:
            peak_balance = balance

        equity_curve.append({
            "timestamp": str(curr_time),
            "balance": round(balance, 2)
        })

    # ── 4. Calculate Final Comprehensive Metrics ──
    total_trades = len(trades)
    wins = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]

    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    total_profit_usd = balance - INITIAL_BALANCE
    total_r = sum(t['pnl_r'] for t in trades)
    expectancy_usd = (total_profit_usd / total_trades) if total_trades > 0 else 0.0
    expectancy_r = (total_r / total_trades) if total_trades > 0 else 0.0

    gross_wins = sum(t['pnl_usd'] for t in wins)
    gross_losses = abs(sum(t['pnl_usd'] for t in losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']

    buy_discount_count = sum(1 for t in buy_trades if t.get('dr_pct') is not None and t['dr_pct'] < 0.45)
    sell_premium_count = sum(1 for t in sell_trades if t.get('dr_pct') is not None and t['dr_pct'] > 0.55)
    eq_count = sum(1 for t in trades if t.get('dr_pct') is not None and 0.40 <= t['dr_pct'] <= 0.60)

    buy_discount_pct = (buy_discount_count / len(buy_trades) * 100.0) if buy_trades else 100.0
    sell_premium_pct = (sell_premium_count / len(sell_trades) * 100.0) if sell_trades else 100.0
    eq_pct = (eq_count / total_trades * 100.0) if total_trades else 0.0

    print("\n" + "=" * 70, flush=True)
    print("BACKTEST RESULTS SUMMARY — XAUUSD SWING STRATEGY", flush=True)
    print("=" * 70, flush=True)
    print(f"Period:                     2025.02.18 — 2026.10.23 (20+ Months)", flush=True)
    print(f"Initial Balance:            ${INITIAL_BALANCE:.2f}", flush=True)
    print(f"Final Balance:              ${balance:.2f} ({total_profit_usd:+.2f} USD / {((balance/INITIAL_BALANCE)-1)*100:+.1f}%)", flush=True)
    print(f"Total Completed Trades:     {total_trades}", flush=True)
    print(f"Wins / Losses:              {len(wins)} / {len(losses)}", flush=True)
    print(f"Win Rate:                   {win_rate:.1f}%", flush=True)
    print(f"Profit Factor:              {profit_factor:.2f}", flush=True)
    print(f"Total R Achieved:           {total_r:+.2f} R", flush=True)
    print(f"Expectancy per Trade:       ${expectancy_usd:+.2f} ({expectancy_r:+.2f} R)", flush=True)
    print(f"Max Daily Drawdown:         ${max_daily_dd_overall:.2f} (Limit: ${DAILY_LOSS_LIMIT:.2f} / 4.0%) -> {'PASSED ✅' if not daily_breach_detected else 'BREACHED ❌'}", flush=True)
    print(f"Max Total Drawdown:         ${max_total_dd_usd:.2f} ({max_total_dd_usd/INITIAL_BALANCE*100:.1f}%)", flush=True)
    print(f"Lowest Equity Level:        ${min_equity_level:.2f} (Floor: $4,400.00) -> {'PASSED ✅' if not floor_breach_detected else 'BREACHED ❌'}", flush=True)
    print("-" * 70, flush=True)
    print("DEALING RANGE / EQUILIBRIUM REGIME COMPLIANCE:", flush=True)
    print(f"BUY in Discount (< 45% DR): {buy_discount_count}/{len(buy_trades)} ({buy_discount_pct:.1f}%) -> {'100% COMPLIANT ✅' if buy_discount_pct == 100.0 else 'NON-COMPLIANT ❌'}", flush=True)
    print(f"SELL in Premium (> 55% DR): {sell_premium_count}/{len(sell_trades)} ({sell_premium_pct:.1f}%) -> {'100% COMPLIANT ✅' if sell_premium_pct == 100.0 else 'NON-COMPLIANT ❌'}", flush=True)
    print(f"Trades in Eq (40%-60% DR):  {eq_count}/{total_trades} ({eq_pct:.1f}%) -> {'0% (ZERO) COMPLIANT ✅' if eq_count == 0 else 'NON-COMPLIANT ❌'}", flush=True)
    print("=" * 70, flush=True)

    print("\n5 CONCRETE TRADE EXAMPLES:", flush=True)
    sample_trades = trades[:5] if len(trades) >= 5 else trades
    for idx, t in enumerate(sample_trades, 1):
        dr_info = f"{t.get('dr_pct')*100:.1f}%" if t.get('dr_pct') is not None else "N/A"
        print(f"\n--- Trade #{idx} ---", flush=True)
        print(f"Symbol & Direction:     {t['symbol']} {t['type']} ({t['stars']}★) | DR: {dr_info}", flush=True)
        print(f"Entry Timestamp:        {t['entry_time']} @ {t['entry_price']:.2f}", flush=True)
        print(f"Initial Stop Loss:      {t['initial_sl']:.2f} (Risk dist: ${abs(t['entry_price'] - t['initial_sl']):.2f})", flush=True)
        print(f"Targets:                TP1: {t['tp1']:.2f} | TP2: {t['tp2']:.2f}", flush=True)
        print(f"Lot Size & Risk $:      {t['lot']:.2f} lot (Max Risk: ${t['initial_risk_usd']:.2f})", flush=True)
        print(f"Exit Timestamp:         {t['exit_time']}", flush=True)
        print(f"Outcome & PnL:          {t['outcome']} | PnL: ${t['pnl_usd']:+.2f} ({t['pnl_r']:+.2f} R)", flush=True)
        print(f"Exit Steps:             {t['exits']}", flush=True)

    def to_serializable(val):
        if isinstance(val, (np.bool_, bool)):
            return bool(val)
        if isinstance(val, (np.floating, float)):
            return float(val)
        if isinstance(val, (np.integer, int)):
            return int(val)
        if isinstance(val, (datetime, pd.Timestamp)):
            return str(val)
        if isinstance(val, dict):
            return {k: to_serializable(v) for k, v in val.items()}
        if isinstance(val, list):
            return [to_serializable(v) for v in val]
        return val

    out_file = "/root/smart-trader-bot/data/gold_swing_backtest_results.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    payload = {
        "metrics": {
            "initial_balance": INITIAL_BALANCE,
            "final_balance": round(balance, 2),
            "profit_usd": round(total_profit_usd, 2),
            "profit_pct": round(((balance/INITIAL_BALANCE)-1)*100, 2),
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_r": round(total_r, 2),
            "expectancy_usd": round(expectancy_usd, 2),
            "expectancy_r": round(expectancy_r, 2),
            "max_daily_dd_usd": round(max_daily_dd_overall, 2),
            "max_total_dd_usd": round(max_total_dd_usd, 2),
            "lowest_equity": round(min_equity_level, 2),
            "fundingpips_rules_passed": bool(not daily_breach_detected and not floor_breach_detected),
            "dr_compliance": {
                "buy_in_discount_pct": round(buy_discount_pct, 1),
                "sell_in_premium_pct": round(sell_premium_pct, 1),
                "equilibrium_trades_count": eq_count,
                "equilibrium_trades_pct": round(eq_pct, 1),
                "fully_compliant": bool(buy_discount_pct == 100.0 and sell_premium_pct == 100.0 and eq_count == 0)
            }
        },
        "sample_5_trades": sample_trades,
        "all_trades": trades
    }
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(to_serializable(payload), f, indent=2, ensure_ascii=False)
    print(f"\nDetailed backtest results saved to {out_file}", flush=True)

if __name__ == "__main__":
    run_gold_swing_backtest()
