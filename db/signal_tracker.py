"""
Real-time Signal Tracker v4 & Institutional Lifecycle Manager.
Отслеживает полный жизненный цикл ордеров:
- ЭТАП 1: Выставлен отложенный ордер (PENDING)
- ЭТАП 2: Цена коснулась входа (PENDING -> ACTIVE)
- ЭТАП 3: Breakeven (SL -> Entry при достижении 1:1)
- ЭТАП 4: Partial Close (50% фиксация на TP1 + перевод остатка на TP2 с SL=Entry)
- ЭТАП 5: Фиксация Take Profit 2 / Stop Loss (TP2_HIT / SL_HIT / EXPIRED)
- ЭТАП 6: Drawdown Alert (Срочное оповещение при 3 SL подряд)

Строго фильтрует свечи по реальному времени, исключая ложные ретроспективные срабатывания.
"""

import asyncio
import logging
from datetime import datetime, timezone
import pandas as pd

import config
from market.data_fetcher import DataFetcher
from db.database import (
    get_pending_signals, get_active_signals, activate_signal,
    update_signal_status, update_signal_sl, get_consecutive_sl_count
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
        self.PENDING_EXPIRE_HOURS = 24.0
        self.ACTIVE_EXPIRE_HOURS = 48.0
        self._last_drawdown_warn_count: int = 0

    async def start(self):
        self.is_running = True
        logger.info("SignalTracker v4 started. Breakeven 1:1 + Partial Close TP1 enabled. Checking every %d min.", self.check_interval)
        while self.is_running:
            try:
                if not DataFetcher.is_weekend():
                    await self.process_pending_signals()
                    await self.process_active_signals()
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
                    await self.bot.send_message(uid, text, parse_mode="HTML")
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
                    f"🛑 <b>Зафиксировано {sl_count} Stop Loss подряд.</b>\n\n"
                    "🔒 <b>Действие:</b> Авто-сканер сигналов временно приостановлен на 24 часа для защиты депозита.\n"
                    "💼 <i>Рынок находится в аномальной фазе волатильности / смены тренда. Соблюдайте мани-менеджмент!</i>"
                )
                await self._send_to_all(alert_msg)
                logger.warning("Drawdown Alert broadcasted for %d consecutive SL hits.", sl_count)
            elif sl_count < 3:
                self._last_drawdown_warn_count = 0
        except Exception as e:
            logger.error("Drawdown alert check error: %s", e)

    # ── 1. PENDING -> ACTIVE ──
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

        created = datetime.fromisoformat(sig['created_at'])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        hours_pending = (now - created).total_seconds() / 3600

        if hours_pending > self.PENDING_EXPIRE_HOURS:
            await update_signal_status(
                signal_id, "EXPIRED", close_price=entry, pnl_pips=0.0,
                result="Истек срок ожидания входа (24ч)"
            )
            msg = format_signal_result(sig, "EXPIRED", entry, 0.0)
            await self._send_to_all(msg)
            logger.info("Signal #%d expired waiting for entry.", signal_id)
            return

        df = await self.fetcher.fetch_ohlcv(symbol, "M15", limit=10)
        if df is None or df.empty:
            return

        created_naive = created.replace(tzinfo=None)
        recent_high, recent_low, current_close = self._get_post_event_prices(df, created_naive)

        is_triggered = False
        if "LIMIT" in order_type:
            if direction == "LONG" and recent_low <= entry:
                is_triggered = True
            elif direction == "SHORT" and recent_high >= entry:
                is_triggered = True
        else:
            if direction == "LONG" and recent_high >= entry:
                is_triggered = True
            elif direction == "SHORT" and recent_low <= entry:
                is_triggered = True

        if is_triggered:
            await activate_signal(signal_id)
            sig['status'] = 'ACTIVE'
            sig['activated_at'] = now.isoformat()
            msg = format_order_activated(sig)
            await self._send_to_all(msg)
            logger.info("Signal #%d ACTIVATED at %.5f!", signal_id, entry)

    # ── 2. ACTIVE -> BREAKEVEN / PARTIAL / TP / SL ──
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
        status = sig.get('status', 'ACTIVE')
        entry = sig['entry_price']
        sl = sig['stop_loss']
        tp1 = sig['take_profit_1']
        tp2 = sig.get('take_profit_2')
        breakeven_applied = bool(sig.get('breakeven_applied', 0))

        if not entry or not sl or not tp1:
            return

        act_raw = sig.get('activated_at') or sig['created_at']
        activated = datetime.fromisoformat(act_raw)
        if activated.tzinfo is None:
            activated = activated.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        seconds_active = (now - activated).total_seconds()
        hours_active = seconds_active / 3600

        if seconds_active < 60:
            return

        df = await self.fetcher.fetch_ohlcv(symbol, "M15", limit=12)
        if df is None or df.empty:
            return

        activated_naive = activated.replace(tzinfo=None)
        recent_high, recent_low, current_close = self._get_post_event_prices(df, activated_naive)
        mult = self._get_pip_mult(symbol)

        # Таймаут удержания позиции
        if hours_active > self.ACTIVE_EXPIRE_HOURS:
            base_pnl = (current_close - entry) * mult if direction == "LONG" else (entry - current_close) * mult
            if status == "TP1_PARTIAL":
                # Первая половина уже зафиксирована на TP1
                tp1_pips = abs(tp1 - entry) * mult * 0.5
                rem_pips = base_pnl * 0.5
                total_pnl = round(tp1_pips + rem_pips, 1)
            else:
                total_pnl = round(base_pnl, 1)

            await update_signal_status(
                signal_id, "EXPIRED", close_price=current_close,
                pnl_pips=total_pnl, result=f"Закрыт по времени ({self.ACTIVE_EXPIRE_HOURS}ч)"
            )
            msg = format_signal_result(sig, "EXPIRED", current_close, total_pnl)
            await self._send_to_all(msg)
            logger.info("Signal #%d closed by timeout.", signal_id)
            return

        # ── 1. BREAKEVEN CHECK (1:1 risk = reward reached) ──
        risk = abs(entry - sl)
        if not breakeven_applied and risk > 0 and status != "TP1_PARTIAL":
            if direction == "LONG":
                breakeven_target = entry + risk
                if recent_high >= breakeven_target:
                    new_sl = entry
                    await update_signal_sl(signal_id, new_sl, breakeven=True)
                    sl = new_sl
                    breakeven_applied = True
                    pips_protected = round(risk * mult, 1)
                    msg = (
                        f"🛡 <b>БЕЗУБЫТОК АКТИВИРОВАН</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | LONG\n"
                        f"┌ 📍 Вход: <code>{entry:.5f}</code>\n"
                        f"├ 🛡 SL перенесён: <code>{new_sl:.5f}</code> (=Entry)\n"
                        f"├ 🎯 TP1: <code>{tp1:.5f}</code>\n"
                        f"└ ⚡ Защищено: <code>+{pips_protected}</code> pips риска\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Прибыль достигла 1:1 — риск снят.</i>"
                    )
                    await self._send_to_all(msg)
                    logger.info("Signal #%d BREAKEVEN applied at %.5f", signal_id, new_sl)

            elif direction == "SHORT":
                breakeven_target = entry - risk
                if recent_low <= breakeven_target:
                    new_sl = entry
                    await update_signal_sl(signal_id, new_sl, breakeven=True)
                    sl = new_sl
                    breakeven_applied = True
                    pips_protected = round(risk * mult, 1)
                    msg = (
                        f"🛡 <b>БЕЗУБЫТОК АКТИВИРОВАН</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | SHORT\n"
                        f"┌ 📍 Вход: <code>{entry:.5f}</code>\n"
                        f"├ 🛡 SL перенесён: <code>{new_sl:.5f}</code> (=Entry)\n"
                        f"├ 🎯 TP1: <code>{tp1:.5f}</code>\n"
                        f"└ ⚡ Защищено: <code>+{pips_protected}</code> pips риска\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Прибыль достигла 1:1 — риск снят.</i>"
                    )
                    await self._send_to_all(msg)
                    logger.info("Signal #%d BREAKEVEN applied at %.5f", signal_id, new_sl)

        # ── 2. LONG ПОЗИЦИИ ──
        if direction == "LONG":
            # Проверка SL
            if recent_low <= sl:
                if status == "TP1_PARTIAL":
                    # Выбит в безубыток по остатку (50% уже в кармане)
                    pnl_tp1_half = round((abs(tp1 - entry) * mult) * 0.5, 1)
                    await update_signal_status(signal_id, "TP1_HIT", close_price=sl, pnl_pips=pnl_tp1_half, result="TP1 50% + Breakeven")
                    msg = (
                        f"🛡 <b>ПОЗИЦИЯ ЗАКРЫТА ПО БЕЗУБЫТКУ</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | LONG\n"
                        f"┌ 🎯 TP1 (50%): <code>+{pnl_tp1_half}</code> pips (зафиксировано)\n"
                        f"├ 🛡 Остаток (50%): <code>0.0</code> pips (закрыт на Entry)\n"
                        f"└ 💰 <b>Итоговая прибыль:</b> <code>+{pnl_tp1_half}</code> pips\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Институциональный Partial Close защитил прибыль!</i>"
                    )
                    await self._send_to_all(msg)
                elif breakeven_applied:
                    await update_signal_status(signal_id, "BREAKEVEN", close_price=sl, pnl_pips=0.0, result="Безубыток (SL=Entry)")
                    msg = format_signal_result(sig, "BREAKEVEN", sl, 0.0)
                    await self._send_to_all(msg)
                else:
                    pnl = -round(abs(entry - sl) * mult, 1)
                    await update_signal_status(signal_id, "SL_HIT", close_price=sl, pnl_pips=pnl, result="Stop Loss")
                    msg = format_signal_result(sig, "SL_HIT", sl, pnl)
                    await self._send_to_all(msg)
                return

            # Проверка TP2
            if tp2 and recent_high >= tp2:
                if status == "TP1_PARTIAL":
                    tp1_half = (abs(tp1 - entry) * mult) * 0.5
                    tp2_half = (abs(tp2 - entry) * mult) * 0.5
                    total_pnl = round(tp1_half + tp2_half, 1)
                else:
                    total_pnl = round(abs(tp2 - entry) * mult, 1)

                await update_signal_status(signal_id, "TP2_HIT", close_price=tp2, pnl_pips=total_pnl, result="Take Profit 2 (FULL)")
                msg = format_signal_result(sig, "TP2_HIT", tp2, total_pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP2_HIT: +%.1f pips", signal_id, total_pnl)
                return

            # Проверка TP1 (Partial Close)
            if recent_high >= tp1 and status != "TP1_PARTIAL":
                pnl_tp1_full = round(abs(tp1 - entry) * mult, 1)
                pnl_tp1_half = round(pnl_tp1_full * 0.5, 1)
                
                if tp2:
                    # Частичное закрытие 50%, перенос SL на Entry
                    await update_signal_status(signal_id, "TP1_PARTIAL", close_price=tp1, pnl_pips=pnl_tp1_half, result="TP1 50% Partial Hit")
                    await update_signal_sl(signal_id, entry, breakeven=True)
                    msg = (
                        f"🎯 <b>TAKE PROFIT 1 HIT (50% PARTIAL)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | LONG\n"
                        f"┌ 🎯 Уровень TP1: <code>{tp1:.5f}</code> (+{pnl_tp1_full} pips)\n"
                        f"├ 💰 <b>Зафиксировано:</b> <code>+{pnl_tp1_half}</code> pips (50% позиции)\n"
                        f"├ 🛡 <b>SL перенесён:</b> <code>{entry:.5f}</code> (Безубыток)\n"
                        f"└ 🚀 <b>Остаток 50%:</b> удерживается к TP2 (<code>{tp2:.5f}</code>)\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Сделка без риска! 50% прибыли уже в кармане.</i>"
                    )
                    await self._send_to_all(msg)
                else:
                    await update_signal_status(signal_id, "TP1_HIT", close_price=tp1, pnl_pips=pnl_tp1_full, result="Take Profit 1")
                    msg = format_signal_result(sig, "TP1_HIT", tp1, pnl_tp1_full)
                    await self._send_to_all(msg)
                return

        # ── 3. SHORT ПОЗИЦИИ ──
        elif direction == "SHORT":
            # Проверка SL
            if recent_high >= sl:
                if status == "TP1_PARTIAL":
                    pnl_tp1_half = round((abs(entry - tp1) * mult) * 0.5, 1)
                    await update_signal_status(signal_id, "TP1_HIT", close_price=sl, pnl_pips=pnl_tp1_half, result="TP1 50% + Breakeven")
                    msg = (
                        f"🛡 <b>ПОЗИЦИЯ ЗАКРЫТА ПО БЕЗУБЫТКУ</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | SHORT\n"
                        f"┌ 🎯 TP1 (50%): <code>+{pnl_tp1_half}</code> pips (зафиксировано)\n"
                        f"├ 🛡 Остаток (50%): <code>0.0</code> pips (закрыт на Entry)\n"
                        f"└ 💰 <b>Итоговая прибыль:</b> <code>+{pnl_tp1_half}</code> pips\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Институциональный Partial Close защитил прибыль!</i>"
                    )
                    await self._send_to_all(msg)
                elif breakeven_applied:
                    await update_signal_status(signal_id, "BREAKEVEN", close_price=sl, pnl_pips=0.0, result="Безубыток (SL=Entry)")
                    msg = format_signal_result(sig, "BREAKEVEN", sl, 0.0)
                    await self._send_to_all(msg)
                else:
                    pnl = -round(abs(sl - entry) * mult, 1)
                    await update_signal_status(signal_id, "SL_HIT", close_price=sl, pnl_pips=pnl, result="Stop Loss")
                    msg = format_signal_result(sig, "SL_HIT", sl, pnl)
                    await self._send_to_all(msg)
                return

            # Проверка TP2
            if tp2 and recent_low <= tp2:
                if status == "TP1_PARTIAL":
                    tp1_half = (abs(entry - tp1) * mult) * 0.5
                    tp2_half = (abs(entry - tp2) * mult) * 0.5
                    total_pnl = round(tp1_half + tp2_half, 1)
                else:
                    total_pnl = round(abs(entry - tp2) * mult, 1)

                await update_signal_status(signal_id, "TP2_HIT", close_price=tp2, pnl_pips=total_pnl, result="Take Profit 2 (FULL)")
                msg = format_signal_result(sig, "TP2_HIT", tp2, total_pnl)
                await self._send_to_all(msg)
                logger.info("Signal #%d TP2_HIT: +%.1f pips", signal_id, total_pnl)
                return

            # Проверка TP1 (Partial Close)
            if recent_low <= tp1 and status != "TP1_PARTIAL":
                pnl_tp1_full = round(abs(entry - tp1) * mult, 1)
                pnl_tp1_half = round(pnl_tp1_full * 0.5, 1)
                
                if tp2:
                    await update_signal_status(signal_id, "TP1_PARTIAL", close_price=tp1, pnl_pips=pnl_tp1_half, result="TP1 50% Partial Hit")
                    await update_signal_sl(signal_id, entry, breakeven=True)
                    msg = (
                        f"🎯 <b>TAKE PROFIT 1 HIT (50% PARTIAL)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>{symbol}</b> | SHORT\n"
                        f"┌ 🎯 Уровень TP1: <code>{tp1:.5f}</code> (+{pnl_tp1_full} pips)\n"
                        f"├ 💰 <b>Зафиксировано:</b> <code>+{pnl_tp1_half}</code> pips (50% позиции)\n"
                        f"├ 🛡 <b>SL перенесён:</b> <code>{entry:.5f}</code> (Безубыток)\n"
                        f"└ 🚀 <b>Остаток 50%:</b> удерживается к TP2 (<code>{tp2:.5f}</code>)\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💼 <i>Сделка без риска! 50% прибыли уже в кармане.</i>"
                    )
                    await self._send_to_all(msg)
                else:
                    await update_signal_status(signal_id, "TP1_HIT", close_price=tp1, pnl_pips=pnl_tp1_full, result="Take Profit 1")
                    msg = format_signal_result(sig, "TP1_HIT", tp1, pnl_tp1_full)
                    await self._send_to_all(msg)
                return

    @staticmethod
    def _get_post_event_prices(df: pd.DataFrame, event_time_naive: datetime) -> tuple[float, float, float]:
        current_close = float(df['close'].iloc[-1])
        if 'timestamp' not in df.columns:
            return current_close, current_close, current_close
        df_future = df[df['timestamp'] > event_time_naive]
        if not df_future.empty:
            recent_high = max(float(df_future['high'].max()), current_close)
            recent_low = min(float(df_future['low'].min()), current_close)
        else:
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
