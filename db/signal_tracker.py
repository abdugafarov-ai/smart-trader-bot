"""
Institutional Signal Tracker & Risk Monitor.
Отвечает за:
- Мониторинг таймаута неисполненных лимитных ордеров (24ч)
- Расчет просадки и срочный Drawdown Alert при 3 SL подряд
- Контроль синхронизации с реальными сделками MetaTrader 5

Жизненный цикл позиций (открытие, безубыток, TP, SL) управляется
100% реальными событиями терминала MetaTrader 5 (MT5 Bridge API).
"""

import asyncio
import logging
from datetime import datetime, timezone

import config
from db.database import (
    get_pending_signals, get_active_signals,
    update_signal_status, get_consecutive_sl_count
)
from db.users import get_approved_user_ids
from utils.formatters import format_signal_result

logger = logging.getLogger(__name__)


class SignalTracker:
    """Институциональный монитор сигналов и рисков."""

    def __init__(self, bot=None, check_interval_minutes: int = 5):
        self.bot = bot
        self.check_interval = check_interval_minutes
        self.is_running = False
        self.PENDING_EXPIRE_HOURS = 24.0
        self._last_drawdown_warn_count: int = 0

    async def start(self):
        self.is_running = True
        logger.info("SignalTracker started (MT5 broker-grounded). Checking every %d min.", self.check_interval)
        while self.is_running:
            try:
                await self.process_pending_timeouts()
                await self.check_drawdown_alert()
            except Exception as e:
                logger.error("SignalTracker loop error: %s", e, exc_info=True)
            await asyncio.sleep(self.check_interval * 60)

    async def stop(self):
        self.is_running = False

    async def _send_to_all(self, text: str):
        if not self.bot:
            return
        try:
            recipients = await get_approved_user_ids()
            if config.ADMIN_ID and config.ADMIN_ID not in recipients:
                recipients.append(config.ADMIN_ID)
            for uid in recipients:
                try:
                    protect = (uid != config.ADMIN_ID)
                    await self.bot.send_message(uid, text, parse_mode="HTML", protect_content=protect)
                except Exception as err:
                    logger.error("Failed to send tracker alert to %d: %s", uid, err)
        except Exception as e:
            logger.error("Error broadcasting tracker alert: %s", e)

    async def check_drawdown_alert(self):
        """Проверяет просадку и шлет срочный Drawdown Alert при 3 SL подряд."""
        try:
            sl_count = await get_consecutive_sl_count()
            if sl_count >= 3 and sl_count != self._last_drawdown_warn_count:
                self._last_drawdown_warn_count = sl_count
                alert_msg = (
                    "⚠️ <b>DRAWDOWN ALERT | СИСТЕМА ЗАЩИТЫ КАПИТАЛА</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🛑 <b>Зафиксировано {sl_count} Stop Loss подряд в реальных сделках.</b>\n\n"
                    "🔒 <b>Действие:</b> Авто-сканер сигналов временно приостановлен для защиты депозита.\n"
                    "💼 <i>Соблюдайте мани-менеджмент! Дождитесь нормализации рынка.</i>"
                )
                await self._send_to_all(alert_msg)
                logger.warning("Drawdown Alert broadcasted for %d consecutive SL hits.", sl_count)
            elif sl_count < 3:
                self._last_drawdown_warn_count = 0
        except Exception as e:
            logger.error("Drawdown alert check error: %s", e)

    async def process_pending_timeouts(self):
        """Отменяет отложенные ордера, которые не были исполнены брокером."""
        pending = await get_pending_signals()
        if not pending:
            return

        now = datetime.now(timezone.utc)
        for sig in pending:
            try:
                signal_id = sig['id']
                symbol = sig['symbol']
                status = sig.get('status')
                if status in ('CANCELLED_BY_MT5', 'REJECTED_BY_MT5', 'CANCELLED_UNCONFIRMED', 'EXPIRED'):
                    continue

                created_raw = sig.get('created_at')
                if not created_raw:
                    continue
                created = datetime.fromisoformat(created_raw)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)

                age_minutes = (now - created).total_seconds() / 60.0
                broker_confirmed = sig.get('broker_confirmed', 0)

                # 1. Если ордер не был подтвержден MT5 за 5 минут — авто-отмена фантома
                if not broker_confirmed and age_minutes > 5.0:
                    await update_signal_status(
                        signal_id, "CANCELLED_UNCONFIRMED",
                        result="Не подтвержден терминалом MT5 в течение 5 минут (авто-отмена)"
                    )
                    logger.info("Signal #%d (%s) unconfirmed after %.1f min -> CANCELLED_UNCONFIRMED", signal_id, symbol, age_minutes)
                    continue

                # 2. Для подтвержденных лимитных ордеров — истечение по 24ч таймауту
                hours_pending = age_minutes / 60.0
                if hours_pending > self.PENDING_EXPIRE_HOURS:
                    entry = sig.get('entry_price', 0.0)
                    await update_signal_status(
                        signal_id, "EXPIRED", close_price=entry, pnl_pips=0.0,
                        result=f"Истёк срок ожидания входа ({int(self.PENDING_EXPIRE_HOURS)}ч)"
                    )
                    # Проверяем, есть ли реальная позиция в MT5 по этой паре
                    active_signals = await get_active_signals()
                    has_active = any(s.get('symbol') == symbol for s in active_signals)
                    if not has_active:
                        msg = format_signal_result(sig, "EXPIRED", entry, 0.0)
                        await self._send_to_all(msg)
                    logger.info("Pending signal #%d (%s) expired after %.1f hours.", signal_id, symbol, hours_pending)
            except Exception as e:
                logger.error("Error processing pending timeout for signal %d: %s", sig.get('id', 0), e)
