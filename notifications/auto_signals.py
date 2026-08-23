"""
Smart Trader Bot — AutoSignalScanner.
Сканирует все пары, фильтрует по звёздам, блокирует при новостях,
отправляет уведомления всем одобренным пользователям.
"""

import asyncio
import logging
from aiogram import Bot
import config
from db.database import save_signal, check_signal_exists

logger = logging.getLogger(__name__)


class AutoSignalScanner:
    def __init__(self, bot: Bot, interval_minutes: int, user_ids: list[int],
                 symbols: list[str], timeframe: str = 'H4'):
        self.bot = bot
        self.interval_minutes = interval_minutes
        self.user_ids = user_ids  # Fallback list
        self.symbols = symbols
        self.timeframe = timeframe
        self.is_running = False
        self.last_signals: dict[str, tuple[str, int]] = {}
        self._news_warned: set[str] = set()
        self.signals_skipped_by_news: int = 0

    async def start(self):
        self.is_running = True
        logger.info("AutoSignalScanner started. Scanning %d pairs every %d min.",
                     len(self.symbols), self.interval_minutes)
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
        """Получает всех одобренных пользователей + админа."""
        try:
            from db.users import get_approved_user_ids
            approved = await get_approved_user_ids()
            if config.ADMIN_ID and config.ADMIN_ID not in approved:
                approved.append(config.ADMIN_ID)
            return approved
        except Exception:
            # Fallback на статичный список
            return self.user_ids

    async def _get_news_blocked_pairs(self) -> set[str]:
        """Возвращает пары, заблокированные из-за предстоящих новостей."""
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

    async def scan_and_notify(self):
        from bot.handlers import run_multi_tf_analysis
        from utils.formatters import format_notification
        from market.data_fetcher import DataFetcher

        is_weekend = DataFetcher.is_weekend()

        # На выходных — только крипто
        if is_weekend:
            scan_list = [s for s in self.symbols if config.is_crypto(s)]
            if not scan_list:
                logger.info("Weekend: Forex closed. No crypto pairs to scan.")
                return
            logger.info("Weekend: scanning %d crypto pairs only.", len(scan_list))
        else:
            scan_list = self.symbols
            logger.info("Scanning %d pairs for signals...", len(scan_list))

        # Получаем пары, заблокированные новостями
        news_blocked = await self._get_news_blocked_pairs()

        for symbol in scan_list:
            try:
                # Пропускаем пары с предстоящими новостями
                if symbol in news_blocked:
                    logger.info("Skipping %s — blocked by upcoming news.", symbol)
                    self.signals_skipped_by_news += 1
                    continue

                result = await run_multi_tf_analysis(symbol)
                if not result or result.overall_direction == 'NEUTRAL':
                    continue

                # Только >= MIN_SIGNAL_STARS
                if result.overall_stars >= config.MIN_SIGNAL_STARS:
                    # Проверяем дубликаты
                    if not await check_signal_exists(symbol, result.overall_direction, hours=4):
                        # Сохраняем в БД
                        strategies_str = ", ".join(
                            [f"{e} {n}: {v}" for e, n, v in result.strategy_verdicts]
                        )
                        timeframes_str = ", ".join(
                            [f"{t.timeframe}: {t.direction}" for t in result.tf_analyses]
                        )
                        await save_signal(
                            symbol=symbol,
                            direction=result.overall_direction,
                            stars=result.overall_stars,
                            entry_price=result.entry,
                            stop_loss=result.stop_loss,
                            take_profit_1=result.take_profit_1,
                            take_profit_2=result.take_profit_2,
                            risk_reward=result.risk_reward_1,
                            strategies_agreed=strategies_str,
                            timeframes_agreed=timeframes_str,
                        )
                        # Уведомление
                        msg = format_notification(result)
                        await self._send_to_all(msg)
                        self.last_signals[symbol] = (
                            result.overall_direction, result.overall_stars
                        )
                        logger.info("Signal sent: %s %s ⭐%d",
                                    symbol, result.overall_direction, result.overall_stars)
            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e, exc_info=True)

    async def check_news(self):
        """Проверяет предстоящие важные новости и предупреждает."""
        try:
            from news.economic_calendar import EconomicCalendar
            calendar = EconomicCalendar(config.TIMEZONE)

            for warn_minutes in config.NEWS_WARN_BEFORE_MINUTES:
                events = await calendar.get_upcoming_high_impact(
                    within_minutes=warn_minutes + 5
                )
                for event in events:
                    if event.minutes_until <= warn_minutes:
                        warn_key = f"{event.title}_{event.date_str}_{warn_minutes}"
                        if warn_key not in self._news_warned:
                            self._news_warned.add(warn_key)
                            msg = calendar.format_warning(event)
                            await self._send_to_all(msg)
                            logger.info("News warning sent: %s in %d min",
                                       event.title, event.minutes_until)
        except Exception as e:
            logger.error("News check error: %s", e, exc_info=True)

    async def _send_to_all(self, text: str):
        """Отправляет всем одобренным пользователям."""
        recipients = await self._get_notification_recipients()
        for uid in recipients:
            try:
                await self.bot.send_message(uid, text)
            except Exception as e:
                logger.error("Failed to send to %d: %s", uid, e)
