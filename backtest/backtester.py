"""
Smart Trader Bot — Institutional Backtesting Engine.
Симулирует реальное исполнение институциональной стратегии ICT/SMC на исторических данных:
- Поиск фракталов, BOS, CHoCH, Order Block, FVG, OTE
- Лимитные отложенные ордера (LIMIT)
- Breakeven на 1:1
- Partial Close 50% на TP1
- Трейлинг к TP2
"""

import logging
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies.ict_smc import ICTSMCStrategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry_time: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_pips: float
    close_time: str = ""
    close_price: float = 0.0
    status: str = "OPEN"  # TP1_HIT, TP2_HIT, SL_HIT, BREAKEVEN
    pnl_pips: float = 0.0
    pnl_r: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate: float = 0.0
    total_pips: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pips: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


class InstitutionalBacktester:
    def __init__(self):
        self.fetcher = DataFetcher()
        self.strategy = ICTSMCStrategy()

    @staticmethod
    def _get_pip_mult(symbol: str) -> float:
        if "JPY" in symbol:
            return 100.0
        elif "XAU" in symbol:
            return 10.0
        return 10000.0

    async def run_backtest(self, symbol: str = "EURUSD", timeframe: str = "H1", limit: int = 300) -> BacktestResult:
        """Запускает симуляцию на исторических свечах."""
        df = await self.fetcher.fetch_ohlcv(symbol, timeframe, limit=limit)
        if df is None or df.empty or len(df) < 50:
            return BacktestResult(symbol=symbol, timeframe=timeframe)

        df = TechnicalIndicators.calculate_all(df)
        mult = self._get_pip_mult(symbol)

        trades: list[BacktestTrade] = []
        equity_curve: list[float] = [0.0]
        current_equity = 0.0
        max_equity = 0.0
        max_drawdown = 0.0

        i = 35
        while i < len(df) - 15:
            # Окно анализа до текущей свечи
            window_df = df.iloc[:i+1]
            res = self.strategy.analyze(window_df, symbol, timeframe)
            sig = res.signal

            if sig.direction == "NEUTRAL" or not sig.entry or not sig.stop_loss or not sig.take_profit_1:
                i += 1
                continue

            entry = sig.entry
            sl = sig.stop_loss
            tp1 = sig.take_profit_1
            tp2 = sig.take_profit_2 or (entry + (entry - sl) * 4.0 if sig.direction == "LONG" else entry - (sl - entry) * 4.0)
            risk = abs(entry - sl)
            risk_pips = risk * mult

            if risk_pips <= 0:
                i += 1
                continue

            # Симуляция будущих свечей для этой сделки
            activated = False
            active_index = -1
            breakeven_applied = False
            tp1_hit = False

            trade = BacktestTrade(
                symbol=symbol,
                direction=sig.direction,
                entry_time=str(df['timestamp'].iloc[i]) if 'timestamp' in df.columns else str(i),
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_pips=round(risk_pips, 1)
            )

            # Проверяем исполнение в течение следующих до 40 свечей
            j = i + 1
            max_forward = min(len(df), i + 40)

            while j < max_forward:
                candle_high = float(df['high'].iloc[j])
                candle_low = float(df['low'].iloc[j])
                candle_close = float(df['close'].iloc[j])

                # 1. Активация отложенного ордера (LIMIT)
                if not activated:
                    if sig.direction == "LONG" and candle_low <= entry:
                        activated = True
                        active_index = j
                    elif sig.direction == "SHORT" and candle_high >= entry:
                        activated = True
                        active_index = j
                    j += 1
                    continue

                # 2. Обработка активной позиции
                if sig.direction == "LONG":
                    # Breakeven на 1:1
                    if not breakeven_applied and candle_high >= (entry + risk):
                        sl = entry
                        breakeven_applied = True

                    # Проверка Stop Loss
                    if candle_low <= sl:
                        if tp1_hit:
                            # 50% закрыто на TP1, остаток закрыт в безубыток
                            half_tp1 = round(((tp1 - entry) * mult) * 0.5, 1)
                            trade.status = "TP1_HIT"
                            trade.pnl_pips = half_tp1
                            trade.pnl_r = round(half_tp1 / risk_pips, 2)
                        elif breakeven_applied:
                            trade.status = "BREAKEVEN"
                            trade.pnl_pips = 0.0
                            trade.pnl_r = 0.0
                        else:
                            trade.status = "SL_HIT"
                            trade.pnl_pips = -round(risk_pips, 1)
                            trade.pnl_r = -1.0

                        trade.close_price = sl
                        trade.close_time = str(df['timestamp'].iloc[j]) if 'timestamp' in df.columns else str(j)
                        trades.append(trade)
                        break

                    # Проверка TP2
                    if candle_high >= tp2:
                        if tp1_hit:
                            tp1_p = ((tp1 - entry) * mult) * 0.5
                            tp2_p = ((tp2 - entry) * mult) * 0.5
                            total_p = round(tp1_p + tp2_p, 1)
                        else:
                            total_p = round((tp2 - entry) * mult, 1)

                        trade.status = "TP2_HIT"
                        trade.pnl_pips = total_p
                        trade.pnl_r = round(total_p / risk_pips, 2)
                        trade.close_price = tp2
                        trade.close_time = str(df['timestamp'].iloc[j]) if 'timestamp' in df.columns else str(j)
                        trades.append(trade)
                        break

                    # Проверка TP1 (Partial Close 50%)
                    if candle_high >= tp1 and not tp1_hit:
                        tp1_hit = True
                        sl = entry  # Перенос в безубыток

                elif sig.direction == "SHORT":
                    # Breakeven на 1:1
                    if not breakeven_applied and candle_low <= (entry - risk):
                        sl = entry
                        breakeven_applied = True

                    # Проверка Stop Loss
                    if candle_high >= sl:
                        if tp1_hit:
                            half_tp1 = round(((entry - tp1) * mult) * 0.5, 1)
                            trade.status = "TP1_HIT"
                            trade.pnl_pips = half_tp1
                            trade.pnl_r = round(half_tp1 / risk_pips, 2)
                        elif breakeven_applied:
                            trade.status = "BREAKEVEN"
                            trade.pnl_pips = 0.0
                            trade.pnl_r = 0.0
                        else:
                            trade.status = "SL_HIT"
                            trade.pnl_pips = -round(risk_pips, 1)
                            trade.pnl_r = -1.0

                        trade.close_price = sl
                        trade.close_time = str(df['timestamp'].iloc[j]) if 'timestamp' in df.columns else str(j)
                        trades.append(trade)
                        break

                    # Проверка TP2
                    if candle_low <= tp2:
                        if tp1_hit:
                            tp1_p = ((entry - tp1) * mult) * 0.5
                            tp2_p = ((entry - tp2) * mult) * 0.5
                            total_p = round(tp1_p + tp2_p, 1)
                        else:
                            total_p = round((entry - tp2) * mult, 1)

                        trade.status = "TP2_HIT"
                        trade.pnl_pips = total_p
                        trade.pnl_r = round(total_p / risk_pips, 2)
                        trade.close_price = tp2
                        trade.close_time = str(df['timestamp'].iloc[j]) if 'timestamp' in df.columns else str(j)
                        trades.append(trade)
                        break

                    # Проверка TP1 (Partial Close 50%)
                    if candle_low <= tp1 and not tp1_hit:
                        tp1_hit = True
                        sl = entry

                j += 1

            if activated and trades and trades[-1] is trade:
                current_equity += trade.pnl_pips
                equity_curve.append(current_equity)
                if current_equity > max_equity:
                    max_equity = current_equity
                dd = max_equity - current_equity
                if dd > max_drawdown:
                    max_drawdown = dd
                # Переходим вперед за время сделки чтобы избежать повторного входа в тот же бар
                i = max(i + 4, j)
            else:
                i += 2

        # Расчет итоговых метрик
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.status in ["TP1_HIT", "TP2_HIT"])
        losses = sum(1 for t in trades if t.status == "SL_HIT")
        breakevens = sum(1 for t in trades if t.status == "BREAKEVEN")
        win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0

        total_pips = round(sum(t.pnl_pips for t in trades), 1)
        total_r = round(sum(t.pnl_r for t in trades), 2)

        gross_profit = sum(t.pnl_pips for t in trades if t.pnl_pips > 0)
        gross_loss = abs(sum(t.pnl_pips for t in trades if t.pnl_pips < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            breakevens=breakevens,
            win_rate=win_rate,
            total_pips=total_pips,
            total_r=total_r,
            profit_factor=profit_factor,
            max_drawdown_pips=round(max_drawdown, 1),
            trades=trades,
            equity_curve=equity_curve
        )
