"""
Smart Trader Bot — Signal Tracker.
Фоновая задача: проверяет открытые сигналы, достигли ли они TP или SL.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from market.data_fetcher import DataFetcher
from db.database import (
    get_open_signals, update_signal_status, check_signal_exists,
    save_signal, get_stats, init_db
)

logger = logging.getLogger(__name__)


class SignalTracker:
    """Проверяет открытые сигналы и обновляет их статус."""

    EXPIRE_HOURS = 48  # Сигнал истекает через 48 часов

    def __init__(self, check_interval_minutes: int = 15):
        self.check_interval = check_interval_minutes
        self.fetcher = DataFetcher()
        self.is_running = False

    async def start(self):
        """Запускает фоновый цикл проверки сигналов."""
        await init_db()
        self.is_running = True
        logger.info("SignalTracker started. Checking every %d min.", self.check_interval)

        while self.is_running:
            try:
                await self.check_open_signals()
            except Exception as e:
                logger.error("SignalTracker error: %s", e, exc_info=True)
            await asyncio.sleep(self.check_interval * 60)

    async def stop(self):
        self.is_running = False

    async def check_open_signals(self):
        """Проверяет все открытые сигналы."""
        open_signals = await get_open_signals()
        if not open_signals:
            return

        logger.info("Checking %d open signals...", len(open_signals))

        for sig in open_signals:
            try:
                await self._check_one_signal(sig)
            except Exception as e:
                logger.error("Error checking signal %d: %s", sig['id'], e)

    async def _check_one_signal(self, sig: dict):
        """Проверяет один сигнал."""
        signal_id = sig['id']
        symbol = sig['symbol']
        direction = sig['direction']
        entry = sig['entry_price']
        sl = sig['stop_loss']
        tp1 = sig['take_profit_1']

        # Проверяем истечение срока
        created = datetime.fromisoformat(sig['created_at'])
        now = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        hours_open = (now - created).total_seconds() / 3600
        if hours_open > self.EXPIRE_HOURS:
            # Получаем текущую цену
            df = await self.fetcher.fetch_ohlcv(symbol, "H1", limit=5)
            close_price = df.iloc[-1]['close'] if df is not None and not df.empty else entry

            # Считаем PnL в пипсах
            pnl = self._calc_pips(symbol, entry, close_price, direction)

            await update_signal_status(
                signal_id, "EXPIRED",
                close_price=close_price,
                pnl_pips=pnl,
                result=f"Истёк через {self.EXPIRE_HOURS}ч"
            )
            logger.info("Signal %d EXPIRED: %s %s, PnL: %.1f pips", signal_id, symbol, direction, pnl)
            return

        if not entry or not sl or not tp1:
            return

        # Получаем текущие данные
        df = await self.fetcher.fetch_ohlcv(symbol, "H1", limit=10)
        if df is None or df.empty:
            return

        # Проверяем последние свечи (с момента создания сигнала)
        current_price = df.iloc[-1]['close']
        recent_high = df['high'].max()
        recent_low = df['low'].min()

        if direction == "LONG":
            # SL hit?
            if recent_low <= sl:
                pnl = self._calc_pips(symbol, entry, sl, "LONG")
                await update_signal_status(
                    signal_id, "SL_HIT",
                    close_price=sl, pnl_pips=pnl,
                    result="Стоп-лосс сработал"
                )
                logger.info("Signal %d SL_HIT: %s LONG, PnL: %.1f pips", signal_id, symbol, pnl)
            # TP1 hit?
            elif recent_high >= tp1:
                pnl = self._calc_pips(symbol, entry, tp1, "LONG")
                await update_signal_status(
                    signal_id, "TP1_HIT",
                    close_price=tp1, pnl_pips=pnl,
                    result="Цель 1 достигнута!"
                )
                logger.info("Signal %d TP1_HIT: %s LONG, PnL: +%.1f pips", signal_id, symbol, pnl)

        elif direction == "SHORT":
            # SL hit?
            if recent_high >= sl:
                pnl = self._calc_pips(symbol, entry, sl, "SHORT")
                await update_signal_status(
                    signal_id, "SL_HIT",
                    close_price=sl, pnl_pips=pnl,
                    result="Стоп-лосс сработал"
                )
                logger.info("Signal %d SL_HIT: %s SHORT, PnL: %.1f pips", signal_id, symbol, pnl)
            # TP1 hit?
            elif recent_low <= tp1:
                pnl = self._calc_pips(symbol, entry, tp1, "SHORT")
                await update_signal_status(
                    signal_id, "TP1_HIT",
                    close_price=tp1, pnl_pips=pnl,
                    result="Цель 1 достигнута!"
                )
                logger.info("Signal %d TP1_HIT: %s SHORT, PnL: +%.1f pips", signal_id, symbol, pnl)

    @staticmethod
    def _calc_pips(symbol: str, entry: float, close: float, direction: str) -> float:
        """Считает PnL в пипсах."""
        if not entry or not close:
            return 0.0

        if 'JPY' in symbol:
            mult = 100
        elif symbol == 'XAUUSD':
            mult = 1
        else:
            mult = 10000

        if direction == "LONG":
            return (close - entry) * mult
        else:
            return (entry - close) * mult
