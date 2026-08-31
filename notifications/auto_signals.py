"""
Smart Trader Bot — AutoSignalScanner v3.
Институциональный сканер:
- Session Filter: торгует ТОЛЬКО в активную сессию пары
- Kill Zone приоритет: сигналы в Kill Zone получают +1 звезду
- Correlation Filter: max 2 одновременных ордера в коррелированной группе
- Daily Limit: max 3 сигнала в день + cooldown 2 часа
- News Block: блокировка перед High Impact релизами
- Chart: прикрепление графика с разметкой Entry/SL/TP
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.types import BufferedInputFile
import config
from db.database import (
    save_signal, check_signal_exists, get_active_signals, get_pending_signals,
    get_today_signal_count, get_last_signal_time_for_pair
)

logger = logging.getLogger(__name__)


class AutoSignalScanner:
    def __init__(self, bot: Bot, interval_minutes: int, user_ids: list[int],
                 symbols: list[str], timeframe: str = 'H4'):
        self.bot = bot
        self.interval_minutes = interval_minutes
        self.user_ids = user_ids
        self.symbols = symbols
        self.timeframe = timeframe
        self.is_running = False
        self.last_signals: dict[str, tuple[str, int]] = {}
        self._news_warned: set[str] = set()
        self.signals_skipped_by_news: int = 0
        self.signals_skipped_by_session: int = 0
        self.signals_skipped_by_correlation: int = 0
        self.signals_skipped_by_daily_limit: int = 0
        self.chart_theme: str = "dark"
        self._daily_signal_count: int = 0
        self._daily_reset_date: str = ""
        self._last_signal_time: dict[str, datetime] = {}  # symbol -> last signal time

    async def start(self):
        self.is_running = True
        logger.info("AutoSignalScanner v3 started. Scanning %d pairs every %d min. "
                     "Session Filter: ON | Kill Zones: ON | Correlation Filter: ON | "
                     "Daily Limit: %d | Cooldown: %dh",
                     len(self.symbols), self.interval_minutes,
                     config.MAX_SIGNALS_PER_DAY, config.SIGNAL_COOLDOWN_HOURS)
        while self.is_running:
            try:
                await self.scan_and_notify()
                await self.check_news()
            except Exception as e:
                logger.error("Scan error: %s", e, exc_info=True)
            await asyncio.sleep(self.interval_minutes * 60)

    async def stop(self):
        self.is_running = False

    async def _get_notification_recipients(self) -> list[int]:
        try:
            from db.users import get_approved_user_ids
            approved = await get_approved_user_ids()
            if config.ADMIN_ID and config.ADMIN_ID not in approved:
                approved.append(config.ADMIN_ID)
            return approved
        except Exception:
            return self.user_ids

    async def _get_news_blocked_pairs(self) -> set[str]:
        blocked = set()
        try:
            from news.economic_calendar import EconomicCalendar
            calendar = EconomicCalendar(config.TIMEZONE)
            events = await calendar.get_upcoming_high_impact(within_minutes=30)
            for event in events:
                for pair in event.affected_pairs:
                    blocked.add(pair)
                    logger.info("Pair %s blocked: upcoming %s in %d min",
                               pair, event.title, event.minutes_until)
        except Exception as e:
            logger.error("News blocking check failed: %s", e)
        return blocked

    # ── Session Filter ──
    def _is_pair_active(self, symbol: str) -> bool:
        """Проверяет, активна ли торговая сессия для данной пары."""
        return config.is_pair_in_active_session(symbol)

    # ── Correlation Filter ──
    async def _check_correlation_limit(self, symbol: str, direction: str) -> bool:
        """
        Проверяет, не превышен ли лимит коррелированных сигналов.
        Например: если уже есть 2 LONG на EURUSD и GBPUSD (оба = SHORT USD),
        то третий LONG на AUDUSD блокируется.
        """
        try:
            open_signals = await get_active_signals()
            pending = await get_pending_signals()
            all_open = open_signals + pending

            for group_name, group_pairs in config.CORRELATION_GROUPS.items():
                if symbol not in group_pairs:
                    continue

                # Считаем открытые ордера в этой группе с тем же направлением
                same_dir_count = 0
                for sig in all_open:
                    sig_symbol = sig.get('symbol', '')
                    sig_dir = sig.get('direction', '')
                    if sig_symbol in group_pairs and sig_dir == direction:
                        same_dir_count += 1

                if same_dir_count >= config.MAX_CORRELATED_SIGNALS:
                    logger.info("Correlation limit: %s %s blocked (%d/%d in group %s)",
                               symbol, direction, same_dir_count, config.MAX_CORRELATED_SIGNALS, group_name)
                    return False  # Blocked

            return True  # Allowed
        except Exception as e:
            logger.error("Correlation check error: %s", e)
            return True  # Allow on error

    # ── Daily Limit (persistent via DB) ──
    async def _check_daily_limit(self) -> bool:
        """Проверяет, не превышен ли дневной лимит сигналов. Считает из БД (переживает рестарт)."""
        count = await get_today_signal_count()
        if count >= config.MAX_SIGNALS_PER_DAY:
            return False
        return True

    # ── Cooldown per pair (persistent via DB) ──
    async def _check_cooldown(self, symbol: str) -> bool:
        """Проверяет, прошло ли достаточно времени с последнего сигнала на пару. Считает из БД."""
        last_time = await get_last_signal_time_for_pair(symbol)
        if not last_time:
            return True
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600
        return elapsed >= config.SIGNAL_COOLDOWN_HOURS

    async def scan_and_notify(self):
        from bot.handlers import run_multi_tf_analysis
        from utils.formatters import format_notification
        from market.data_fetcher import DataFetcher

        if DataFetcher.is_weekend():
            logger.info("Weekend: markets closed. Scanner paused.")
            return

        # Kill Zone check
        kz = config.get_current_kill_zone()
        in_kill_zone = kz is not None
        if in_kill_zone:
            logger.info("Active Kill Zone: %s — high priority scanning", kz)

        # Daily limit check (persistent from DB)
        if not await self._check_daily_limit():
            daily_count = await get_today_signal_count()
            logger.info("Daily signal limit reached (%d/%d). Scanner paused until tomorrow.",
                        daily_count, config.MAX_SIGNALS_PER_DAY)
            return

        # Drawdown Protection: пауза при 3 стопах подряд
        from db.database import get_consecutive_sl_count
        consecutive_sl = await get_consecutive_sl_count()
        if consecutive_sl >= 3:
            logger.warning("Drawdown Protection: %d consecutive SL hits. Scanner paused to protect capital.", consecutive_sl)
            return

        news_blocked = await self._get_news_blocked_pairs()
        scan_list = self.symbols
        logger.info("Scanning %d pairs (Kill Zone: %s)...", len(scan_list), kz or "OFF")

        for symbol in scan_list:
            try:
                # ── ФИЛЬТР 1: Сессия ──
                if not self._is_pair_active(symbol):
                    self.signals_skipped_by_session += 1
                    continue

                # ── ФИЛЬТР 2: Новости ──
                if symbol in news_blocked:
                    self.signals_skipped_by_news += 1
                    continue

                # ── ФИЛЬТР 3: Уже есть открытый ордер ──
                if await check_signal_exists(symbol, "ANY"):
                    continue

                # ── ФИЛЬТР 4: Cooldown ──
                if not await self._check_cooldown(symbol):
                    continue

                # ── ФИЛЬТР 5: Daily limit ──
                if not await self._check_daily_limit():
                    break

                result = await run_multi_tf_analysis(symbol)
                if not result or result.overall_direction == 'NEUTRAL':
                    continue

                min_stars = config.MIN_SIGNAL_STARS
                # Kill Zone бонус: снижаем порог на 1 звезду (4→3)
                if in_kill_zone:
                    min_stars = max(3, min_stars - 1)

                if result.overall_stars >= min_stars and (result.risk_reward_1 or 0) >= 2.4:

                    # ── ФИЛЬТР 6: Корреляция ──
                    if not await self._check_correlation_limit(symbol, result.overall_direction):
                        self.signals_skipped_by_correlation += 1
                        continue

                    strategies_str = ", ".join(
                        [f"{e} {n}: {v}" for e, n, v in result.strategy_verdicts]
                    )
                    timeframes_str = ", ".join(
                        [f"{t.timeframe}: {t.direction}" for t in result.tf_analyses]
                    )

                    await save_signal(
                        symbol=symbol,
                        direction=result.overall_direction,
                        order_type=result.order_type,
                        tag_emoji=result.tag_emoji,
                        stars=result.overall_stars,
                        current_price=result.current_price,
                        entry_price=result.entry,
                        stop_loss=result.stop_loss,
                        take_profit_1=result.take_profit_1,
                        take_profit_2=result.take_profit_2,
                        risk_reward=result.risk_reward_1,
                        strategies_agreed=strategies_str,
                        timeframes_agreed=timeframes_str,
                    )

                    # Счётчик ведётся персистентно в БД через save_signal()
                    daily_count = await get_today_signal_count()
                    logger.info("Signal saved. Daily count: %d/%d", daily_count, config.MAX_SIGNALS_PER_DAY)

                    # ── Генерируем график ──
                    chart_bytes = await self._generate_chart(
                        symbol=symbol, direction=result.overall_direction,
                        entry=result.entry, stop_loss=result.stop_loss,
                        tp1=result.take_profit_1, tp2=result.take_profit_2,
                        current_price=result.current_price,
                        order_type=result.order_type, stars=result.overall_stars,
                    )

                    from bot.keyboards import signal_inline_keyboard
                    msg = format_notification(result)

                    # Добавляем Kill Zone метку
                    if in_kill_zone:
                        msg = f"⚡ <b>KILL ZONE: {kz}</b>\n\n" + msg

                    kb = signal_inline_keyboard(symbol)

                    if chart_bytes:
                        await self._send_chart_to_all(chart_bytes, msg, symbol, reply_markup=kb)
                    else:
                        await self._send_to_all(msg, reply_markup=kb)

                    self.last_signals[symbol] = (result.overall_direction, result.overall_stars)
                    logger.info("Signal #%d/%d sent: %s %s [%s] ⭐%d | KZ=%s | chart=%s",
                                daily_count, config.MAX_SIGNALS_PER_DAY,
                                symbol, result.order_type, result.overall_direction,
                                result.overall_stars, kz or "NO",
                                "YES" if chart_bytes else "NO")

            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e, exc_info=True)

        # Логируем статистику фильтров за этот цикл
        daily_used = await get_today_signal_count()
        logger.info("Scan cycle stats: session_skip=%d, news_skip=%d, corr_skip=%d, daily_used=%d/%d",
                     self.signals_skipped_by_session, self.signals_skipped_by_news,
                     self.signals_skipped_by_correlation,
                     daily_used, config.MAX_SIGNALS_PER_DAY)

    async def _generate_chart(self, symbol: str, direction: str, entry: float,
                               stop_loss: float, tp1: float, tp2: float = None,
                               current_price: float = None, order_type: str = "BUY_LIMIT",
                               stars: int = 4) -> bytes | None:
        try:
            from market.data_fetcher import DataFetcher
            from utils.chart_generator import generate_signal_chart

            fetcher = DataFetcher()
            df = await fetcher.fetch_ohlcv(symbol, "H1", limit=80)
            if df is None or df.empty or len(df) < 20:
                df = await fetcher.fetch_ohlcv(symbol, "M15", limit=80)
            if df is None or df.empty or len(df) < 15:
                return None

            return generate_signal_chart(
                df=df, symbol=symbol, direction=direction,
                entry=entry, stop_loss=stop_loss, tp1=tp1, tp2=tp2,
                current_price=current_price, order_type=order_type,
                stars=stars, theme=self.chart_theme, last_n_candles=60,
            )
        except Exception as e:
            logger.error("Chart generation failed for %s: %s", symbol, e, exc_info=True)
            return None

    async def check_news(self):
        try:
            from news.economic_calendar import EconomicCalendar
            calendar = EconomicCalendar(config.TIMEZONE)
            for warn_minutes in config.NEWS_WARN_BEFORE_MINUTES:
                events = await calendar.get_upcoming_high_impact(within_minutes=warn_minutes + 5)
                for event in events:
                    if event.minutes_until <= warn_minutes:
                        warn_key = f"{event.title}_{event.date_str}_{warn_minutes}"
                        if warn_key not in self._news_warned:
                            self._news_warned.add(warn_key)
                            msg = calendar.format_warning(event)
                            await self._send_to_all(msg)
                            logger.info("News warning: %s in %d min", event.title, event.minutes_until)
        except Exception as e:
            logger.error("News check error: %s", e, exc_info=True)

    async def _send_chart_to_all(self, chart_bytes: bytes, caption: str, symbol: str, reply_markup=None):
        recipients = await self._get_notification_recipients()
        photo = BufferedInputFile(chart_bytes, filename=f"signal_{symbol}.png")
        for uid in recipients:
            try:
                await self.bot.send_photo(uid, photo=photo, caption=caption,
                                          parse_mode="HTML", reply_markup=reply_markup)
            except Exception as e:
                logger.error("Failed to send chart to %d: %s", uid, e)
                try:
                    await self.bot.send_message(uid, caption, parse_mode="HTML", reply_markup=reply_markup)
                except Exception as e2:
                    logger.error("Fallback text also failed for %d: %s", uid, e2)

    async def _send_to_all(self, text: str, reply_markup=None):
        recipients = await self._get_notification_recipients()
        for uid in recipients:
            try:
                await self.bot.send_message(uid, text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as e:
                logger.error("Failed to send to %d: %s", uid, e)
