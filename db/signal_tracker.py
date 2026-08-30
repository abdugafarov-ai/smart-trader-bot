"""
Real-time Signal Tracker & Multi-Stage Lifecycle Manager.
Отслеживает жизненный цикл ордеров:
- ЭТАП 1: Выставлен отложенный ордер (PENDING)
- ЭТАП 2: Цена коснулась входа в реальном времени (PENDING -> ACTIVE)
- ЭТАП 3: Фиксация Take Profit / Stop Loss (ACTIVE -> TP/SL/EXPIRED)

Строго фильтрует свечи по реальному времени, исключая ложные ретроспективные срабатывания.
"""

import asyncio
import logging
from datetime import datetime, timezone
import pandas as pd

import config
from market.data_fetcher import DataFetcher
from db.database import (
    get_pending_signals, get_active_signals, activate_signal, update_signal_status
)
from db.users import get_approved_user_ids
from utils.formatters import format_order_activated, format_signal_result

logger = logging.getLogger(__name__)


class SignalTracker:
    """Фоновый трекер сигналов в реальном времени."""

    def __init__(self, bot=None, check_interval_minutes: int = 5):
        self.bot = bot
        self.check_interval = check_interval_minutes
        self.fetcher = DataFetcher()
        self.is_running = False
        self.PENDING_EXPIRE_HOURS = 24.0  # Отложенный ордер истекает через 24 часа
        self.ACTIVE_EXPIRE_HOURS = 48.0   # Позиция в рынке удерживается до 48 часов

    async def start(self):
        """Запускает непрерывный мониторинг рынка."""
        self.is_running = True
        logger.info("SignalTracker started with Live Alerts. Checking every %d min.", self.check_interval)

        while self.is_running:
            try:
                if not DataFetcher.is_weekend():
                    await self.process_pending_signals()
                    await self.process_active_signals()
            except Exception as e:
                logger.error("SignalTracker loop error: %s", e, exc_info=True)
            await asyncio.sleep(self.check_interval * 60)

    async def stop(self):
        self.is_running = False

    async def _send_to_all(self, text: str):
        """Рассылает уведомление всем одобренным пользователям в HTML режиме."""
        if not self.bot:
            return
        try:
            recipients = await get_approved_user_ids()
            if config.ADMIN_ID and config.ADMIN_ID not in recipients:
                recipients.append(config.ADMIN_ID)
            for uid in recipients:
                try:
                    await self.bot.send_message(uid, text, parse_mode="HTML")
                except Exception as err:
                    logger.error("Failed to send tracker alert to %d: %s", uid, err)
        except Exception as e:
            logger.error("Error broadcasting tracker alert: %s", e)

    # ── 1. Проверка отложенных ордеров (PENDING -> ACTIVE) ──
    async def process_pending_signals(self):
        pending = await get_pending_signals()
        if not pending:
            return

        for sig in pending:
            try:
                await self._check_pending_signal(sig)
            except Exception as e:
                logger.error("Error checking pending signal %d: %s", sig.get('id'), e)

    async def _check_pending_signal(self, sig: dict):
        signal_id = sig['id']
        symbol = sig['symbol']
        direction = sig['direction']
        order_type = sig.get('order_type') or 'BUY_LIMIT'
        entry = sig['entry_price']

        if not entry:
            return

        # Проверка на истечение срока ожидания активации (24 часа)
        created = datetime.fromisoformat(sig['created_at'])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        hours_pending = (now - created).total_seconds() / 3600

        if hours_pending > self.PENDING_EXPIRE_HOURS:
            await update_signal_status(
                signal_id, "EXPIRED",
                close_price=entry,
                pnl_pips=0.0,
                result="Истек срок ожидания входа (24ч)"
            )
            msg = format_signal_result(sig, "EXPIRED", entry, 0.0)
            await self._send_to_all(msg)
            logger.info("Signal #%d expired waiting for entry.", signal_id)
            return

        # Получаем свечи M15
        df = await self.fetcher.fetch_ohlcv(symbol, "M15", limit=10)
        if df is None or df.empty:
            return

        created_naive = created.replace(tzinfo=None)
        
        # Получаем цены СТРОГО после времени создания сигнала
        recent_high, recent_low, current_close = self._get_post_event_prices(df, created_naive)

        is_triggered = False

        # Условия активации ордеров:
        if "LIMIT" in order_type:
            # BUY LIMIT: цена опустилась до entry или ниже
            if direction == "LONG" and recent_low <= entry:
                is_triggered = True
            # SELL LIMIT: цена поднялась до entry или выше
            elif direction == "SHORT" and recent_high >= entry:
                is_triggered = True
        else:  # STOP ордера
            # BUY STOP: цена пробила entry вверх
            if direction == "LONG" and recent_high >= entry:
                is_triggered = True
            # SELL STOP: цена пробила entry вниз
            elif direction == "SHORT" and recent_low <= entry:
                is_triggered = True

        if is_triggered:
            await activate_signal(signal_id)
            sig['status'] = 'ACTIVE'
            sig['activated_at'] = now.isoformat()
            msg = format_order_activated(sig)
            await self._send_to_all(msg)
            logger.info("Signal #%d ACTIVATED at %.5f! Sent alert with tag %s", signal_id, entry, sig.get('tag_emoji'))

    # ── 2. Проверка сделок в рынке (ACTIVE -> TP / SL) ──
    async def process_active_signals(self):
        active = await get_active_signals()
        if not active:
            return

        for sig in active:
            try:
                await self._check_active_signal(sig)
            except Exception as e:
                logger.error("Error checking active signal %d: %s", sig.get('id'), e)

    async def _check_active_signal(self, sig: dict):
        signal_id = sig['id']
        symbol = sig['symbol']
        direction = sig['direction']
        entry = sig['entry_price']
        sl = sig['stop_loss']
        tp1 = sig['take_profit_1']
        tp2 = sig.get('take_profit_2')

        if not entry or not sl or not tp1:
            return

        act_raw = sig.get('activated_at') or sig['created_at']
        activated = datetime.fromisoformat(act_raw)
        if activated.tzinfo is None:
            activated = activated.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        seconds_active = (now - activated).total_seconds()
        hours_active = seconds_active / 3600

        # Защита от мгновенного срабатывания в ту же минуту активации
        if seconds_active < 60:
            return

        df = await self.fetcher.fetch_ohlcv(symbol, "M15", limit=12)
        if df is None or df.empty:
            return

        activated_naive = activated.replace(tzinfo=None)
        
        # Получаем экстремумы СТРОГО после момента активации
        recent_high, recent_low, current_close = self._get_post_event_prices(df, activated_naive)
        mult = self._get_pip_mult(symbol)

        # Проверка на таймаут удержания позиции (48 часов)
        if hours_active > self.ACTIVE_EXPIRE_HOURS:
            pnl = (current_close - entry) * mult if direction == "LONG" else (entry - current_close) * mult
            pnl = round(pnl, 1)
            await update_signal_status(
                signal_id, "EXPIRED",
                close_price=current_close,
                pnl_pips=pnl,
                result=f"Закрыт по времени ({self.ACTIVE_EXPIRE_HOURS}ч)"
            )
            msg = format_signal_result(sig, "EXPIRED", current_close, pnl)
            await self._send_to_all(msg)
            logger.info("Signal #%d closed by timeout.", signal_id)
            return

        # ── Фиксация TP / SL строго по свечам после момента активации ──
        if direction == "LONG":
            # 1. Проверяем Stop Loss
            if recent_low <= sl:
                pnl = -round(abs(entry - sl) * mult, 1)
                await update_signal_status(signal_id, "SL_HIT", close_price=sl, pnl_pips=pnl, result="Stop Loss")
                msg = format_signal_result(sig, "SL_HIT", sl, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d SL_HIT: %.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))
            # 2. Проверяем Take Profit 2
            elif tp2 and recent_high >= tp2:
                pnl = round(abs(tp2 - entry) * mult, 1)
                await update_signal_status(signal_id, "TP2_HIT", close_price=tp2, pnl_pips=pnl, result="Take Profit 2")
                msg = format_signal_result(sig, "TP2_HIT", tp2, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP2_HIT: +%.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))
            # 3. Проверяем Take Profit 1
            elif recent_high >= tp1:
                pnl = round(abs(tp1 - entry) * mult, 1)
                await update_signal_status(signal_id, "TP1_HIT", close_price=tp1, pnl_pips=pnl, result="Take Profit 1")
                msg = format_signal_result(sig, "TP1_HIT", tp1, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP1_HIT: +%.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))

        elif direction == "SHORT":
            # 1. Stop Loss
            if recent_high >= sl:
                pnl = -round(abs(sl - entry) * mult, 1)
                await update_signal_status(signal_id, "SL_HIT", close_price=sl, pnl_pips=pnl, result="Stop Loss")
                msg = format_signal_result(sig, "SL_HIT", sl, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d SL_HIT: %.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))
            # 2. Take Profit 2
            elif tp2 and recent_low <= tp2:
                pnl = round(abs(entry - tp2) * mult, 1)
                await update_signal_status(signal_id, "TP2_HIT", close_price=tp2, pnl_pips=pnl, result="Take Profit 2")
                msg = format_signal_result(sig, "TP2_HIT", tp2, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP2_HIT: +%.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))
            # 3. Take Profit 1
            elif recent_low <= tp1:
                pnl = round(abs(entry - tp1) * mult, 1)
                await update_signal_status(signal_id, "TP1_HIT", close_price=tp1, pnl_pips=pnl, result="Take Profit 1")
                msg = format_signal_result(sig, "TP1_HIT", tp1, pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP1_HIT: +%.1f pips (tag %s)", signal_id, pnl, sig.get('tag_emoji'))

    @staticmethod
    def _get_post_event_prices(df: pd.DataFrame, event_time_naive: datetime) -> tuple[float, float, float]:
        """
        Извлекает истинные high, low и close строго ПОСЛЕ времени события.
        Исключает пре-маркетные исторические тени из текущей незавершенной свечи.
        """
        current_close = float(df['close'].iloc[-1])
        
        if 'timestamp' not in df.columns:
            return current_close, current_close, current_close

        # Свечи, которые открылись СТРОГО после времени события
        df_future = df[df['timestamp'] > event_time_naive]
        
        if not df_future.empty:
            recent_high = max(float(df_future['high'].max()), current_close)
            recent_low = min(float(df_future['low'].min()), current_close)
        else:
            # Новых закрытых свечей пока нет, опираемся на текущую живую цену
            recent_high = current_close
            recent_low = current_close
            
        return recent_high, recent_low, current_close

    @staticmethod
    def _get_pip_mult(symbol: str) -> float:
        if 'JPY' in symbol:
            return 100.0
        elif 'XAU' in symbol:
            return 10.0
        return 10000.0
