import os, sys, datetime, json
import pandas as pd
import numpy as np

sys_path = "/root/smart-trader-bot"
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from strategies.ict_smc import ICTSMCStrategy

CSV_DIR = "/home/trader/.wine/drive_c/Program Files/MetaTrader 5/MQL5/Files"
SYMBOLS = ["EURUSD", "GBPUSD", "USDCAD", "USDJPY", "AUDUSD", "NZDUSD", "USDCHF"]

COMMISSION_PIPS = 0.3  # $3/lot = ~0.3 pips
SPREAD_PIPS = {
    "EURUSD": 0.8,
    "GBPUSD": 1.2,
    "USDCAD": 1.4,
    "USDJPY": 1.0,
    "AUDUSD": 1.1,
    "NZDUSD": 1.5,
    "USDCHF": 1.3
}

def load_data(symbol, tf):
    fpath = os.path.join(CSV_DIR, f"candles_{symbol}_{tf}.csv")
    if not os.path.exists(fpath):
        return None
    try:
        df = pd.read_csv(fpath)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Error loading {fpath}: {e}")
        return None

def run_backtest_for_symbol(symbol, filter_mode="all"):
    df_m15 = load_data(symbol, "M15")
    df_d1 = load_data(symbol, "D1")

    if df_m15 is None or len(df_m15) < 300:
        return []

    strategy = ICTSMCStrategy()
    trades = []
    
    pip_mult = 100.0 if "JPY" in symbol else 10000.0
    spread = SPREAD_PIPS.get(symbol, 1.2) / pip_mult
    comm = COMMISSION_PIPS / pip_mult

    in_position = False
    pos_type = None
    pos_entry = 0.0
    pos_sl = 0.0
    pos_tp = 0.0
    pos_entry_bar = 0
    pos_time = None
    initial_risk = 0.0

    total_bars = len(df_m15)
    
    # Stride of 4 bars (hourly evaluation) to simulate realistic bot scan cycles
    for i in range(250, total_bars - 1, 2):
        curr_bar = df_m15.iloc[i]
        curr_time = curr_bar['timestamp']

        # 1. Manage open position
        if in_position:
            # Check bars between last check and current
            next_bars = df_m15.iloc[pos_entry_bar:i + 1]
            high_p = next_bars['high'].max()
            low_p = next_bars['low'].min()

            exit_price = None
            exit_reason = None
            
            if pos_type == "BUY":
                if low_p <= pos_sl:
                    exit_price = pos_sl
                    exit_reason = "SL"
                elif high_p >= pos_tp:
                    exit_price = pos_tp
                    exit_reason = "TP"
            elif pos_type == "SELL":
                if high_p >= pos_sl:
                    exit_price = pos_sl
                    exit_reason = "SL"
                elif low_p <= pos_tp:
                    exit_price = pos_tp
                    exit_reason = "TP"

            if exit_price is not None:
                if pos_type == "BUY":
                    pnl_raw = (exit_price - pos_entry) - spread - comm
                else:
                    pnl_raw = (pos_entry - exit_price) - spread - comm
                
                pnl_r = pnl_raw / initial_risk if initial_risk > 0 else 0.0
                trades.append({
                    "symbol": symbol,
                    "entry_time": str(pos_time),
                    "exit_time": str(curr_time),
                    "type": pos_type,
                    "entry": pos_entry,
                    "sl": pos_sl,
                    "tp": pos_tp,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "pnl_r": round(pnl_r, 2),
                    "pnl_pips": round(pnl_raw * pip_mult, 1),
                    "is_win": bool(pnl_r > 0),
                    "bars_held": (i + 1) - pos_entry_bar
                })
                in_position = False
            continue

        hour = curr_time.hour
        is_kz = (7 <= hour < 10) or (12 <= hour < 15)
        if filter_mode != "no_kz" and not is_kz:
            continue

        df_slice = df_m15.iloc[max(0, i - 180):i + 1].copy()
        df_d1_slice = None
        if df_d1 is not None:
            df_d1_slice = df_d1[df_d1['timestamp'] <= curr_time].copy()
            if len(df_d1_slice) < 5:
                df_d1_slice = None

        try:
            res = strategy.analyze(df_slice, symbol, "M15", df_d1=df_d1_slice)
            sig = res.signal
            
            if sig.direction in ("LONG", "BUY") and sig.confidence >= 4:
                entry = sig.entry
                sl = sig.stop_loss
                tp = sig.take_profit_1
                if entry and sl and tp and (entry - sl) > 0:
                    pos_type = "BUY"
                    pos_entry = entry + spread
                    pos_sl = sl
                    pos_tp = tp
                    initial_risk = entry - sl
                    pos_entry_bar = i + 1
                    pos_time = curr_time
                    in_position = True
            elif sig.direction in ("SHORT", "SELL") and sig.confidence >= 4:
                entry = sig.entry
                sl = sig.stop_loss
                tp = sig.take_profit_1
                if entry and sl and tp and (sl - entry) > 0:
                    pos_type = "SELL"
                    pos_entry = entry
                    pos_sl = sl + spread
                    pos_tp = tp
                    initial_risk = sl - entry
                    pos_entry_bar = i + 1
                    pos_time = curr_time
                    in_position = True
        except Exception:
            pass

    return trades

def compute_metrics(trades):
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "expectancy_r": 0.0, "total_r": 0.0, "max_dd_r": 0.0, "max_consec_losses": 0
        }
    
    total = len(trades)
    wins = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    
    win_rate = (len(wins) / total) * 100.0 if total > 0 else 0.0
    gross_win_r = sum(t['pnl_r'] for t in wins)
    gross_loss_r = abs(sum(t['pnl_r'] for t in losses))
    
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else (99.0 if gross_win_r > 0 else 0.0)
    total_r = sum(t['pnl_r'] for t in trades)
    expectancy_r = total_r / total if total > 0 else 0.0

    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    curr_loss_streak = 0
    max_loss_streak = 0

    for t in trades:
        cum_r += t['pnl_r']
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd
        
        if not t['is_win']:
            curr_loss_streak += 1
            if curr_loss_streak > max_loss_streak:
                max_loss_streak = curr_loss_streak
        else:
            curr_loss_streak = 0

    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "expectancy_r": round(expectancy_r, 2),
        "total_r": round(total_r, 2),
        "max_dd_r": round(max_dd_r, 2),
        "max_consec_losses": max_loss_streak
    }

def main():
    print("=== RUNNING FULL MT5 HISTORICAL BACKTEST (ALL 7 MAJORS) ===")
    all_trades = []
    symbol_reports = {}

    for sym in SYMBOLS:
        t_list = run_backtest_for_symbol(sym, filter_mode="all")
        m = compute_metrics(t_list)
        symbol_reports[sym] = m
        all_trades.extend(t_list)
        print(f"[{sym:6s}] Trades: {m['trades']:3d} | WR: {m['win_rate']:4.1f}% | PF: {m['profit_factor']:4.2f} | Exp: {m['expectancy_r']:+4.2f}R | TotalR: {m['total_r']:+5.1f}R | MaxDD: {m['max_dd_r']:4.1f}R | MaxLossStreak: {m['max_consec_losses']}")

    total_m = compute_metrics(all_trades)
    print("\n=== AGGREGATED PORTFOLIO METRICS ===")
    print(f"Total Trades: {total_m['trades']}")
    print(f"Win Rate: {total_m['win_rate']}% ({total_m['wins']} wins / {total_m['losses']} losses)")
    print(f"Profit Factor: {total_m['profit_factor']}")
    print(f"Expectancy: {total_m['expectancy_r']:+.2f} R per trade")
    print(f"Total Return: {total_m['total_r']:+.1f} R")
    print(f"Max Drawdown: {total_m['max_dd_r']:.1f} R")
    print(f"Max Consecutive Losses: {total_m['max_consec_losses']}")

    # Save to JSON
    with open('/root/smart-trader-bot/data/backtest_full_results.json', 'w', encoding='utf-8') as f:
        json.dump({
            "portfolio": total_m,
            "by_symbol": symbol_reports,
            "trades_sample": all_trades[:50]
        }, f, ensure_ascii=False, indent=2)
    print("Saved /root/smart-trader-bot/data/backtest_full_results.json")

if __name__ == "__main__":
    main()
