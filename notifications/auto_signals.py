"""
Smart Trader Bot — AutoSignalScanner.
Сканирует все торговые пары, фильтрует по институциональным критериям ICT/SMC (R:R >= 1:2.5),
блокирует сигналы перед важными новостями, отправляет сигнал с графиком и разметкой уровней.
"""

import asyncio
import logging
import io
from aiogram import Bot
from aiogram.types import BufferedInputFile
import config
from db.database import save_signal, check_signal_exists

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
        self.chart_theme: str = "dark"  # "dark" или "light" — настройка пользователя

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

        if is_weekend:
            logger.info("Weekend: Forex and Gold markets are closed. Scanner paused.")
            return

        scan_list = self.symbols
        logger.info("Scanning %d pairs for ICT/SMC institutional signals...", len(scan_list))

        news_blocked = await self._get_news_blocked_pairs()

        for symbol in scan_list:
            try:
                if symbol in news_blocked:
                    logger.info("Skipping %s — blocked by upcoming news.", symbol)
                    self.signals_skipped_by_news += 1
                    continue

                if await check_signal_exists(symbol, "ANY"):
                    continue

                result = await run_multi_tf_analysis(symbol)
                if not result or result.overall_direction == 'NEUTRAL':
                    continue

                # Только надежные сигналы (>= 4 звезд) и жесткий R:R >= 2.4
                if result.overall_stars >= config.MIN_SIGNAL_STARS and (result.risk_reward_1 or 0) >= 2.4:
                    strategies_str = ", ".join(
                        [f"{e} {n}: {v}" for e, n, v in result.strategy_verdicts]
                    )
                    timeframes_str = ", ".join(
                        [f"{t.timeframe}: {t.direction}" for t in result.tf_analyses]
                    )
                    
                    # Сохраняем в БД в статусе PENDING
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
                    
                    # ── Генерируем график с разметкой уровней ──
                    chart_bytes = await self._generate_chart(
                        symbol=symbol,
                        direction=result.overall_direction,
                        entry=result.entry,
                        stop_loss=result.stop_loss,
                        tp1=result.take_profit_1,
                        tp2=result.take_profit_2,
                        current_price=result.current_price,
                        order_type=result.order_type,
                        stars=result.overall_stars,
                    )
                    
                    # Отправляем ЭТАП 1 (Сигнал с графиком)
                    from bot.keyboards import signal_inline_keyboard
                    msg = format_notification(result)
                    kb = signal_inline_keyboard(symbol)
                    
                    if chart_bytes:
                        await self._send_chart_to_all(chart_bytes, msg, symbol, reply_markup=kb)
                    else:
                        await self._send_to_all(msg, reply_markup=kb)
                    
                    self.last_signals[symbol] = (
                        result.overall_direction, result.overall_stars
                    )
                    logger.info("Signal sent: %s %s [%s] ⭐%d (tag %s) with chart=%s",
                                symbol, result.order_type, result.overall_direction,
                                result.overall_stars, result.tag_emoji,
                                "YES" if chart_bytes else "NO")
            except Exception as e:
                logger.error("Error scanning %s: %s", symbol, e, exc_info=True)

    async def _generate_chart(self, symbol: str, direction: str, entry: float,
                               stop_loss: float, tp1: float, tp2: float = None,
                               current_price: float = None, order_type: str = "BUY_LIMIT",
                               stars: int = 4) -> bytes | None:
        """Генерирует свечной график с разметкой сигнала."""
        try:
            from market.data_fetcher import DataFetcher
            from utils.chart_generator import generate_signal_chart
            
            fetcher = DataFetcher()
            # Берём H1 данные для красивого графика (60 свечей = ~2.5 дня)
            df = await fetcher.fetch_ohlcv(symbol, "H1", limit=80)
            if df is None or df.empty or len(df) < 20:
                # Fallback на M15
                df = await fetcher.fetch_ohlcv(symbol, "M15", limit=80)
            
            if df is None or df.empty or len(df) < 15:
                return None
                
            chart_bytes = generate_signal_chart(
                df=df,
                symbol=symbol,
                direction=direction,
                entry=entry,
                stop_loss=stop_loss,
                tp1=tp1,
                tp2=tp2,
                current_price=current_price,
                order_type=order_type,
                stars=stars,
                theme=self.chart_theme,
                last_n_candles=60,
            )
            return chart_bytes
        except Exception as e:
            logger.error("Chart generation failed for %s: %s", symbol, e, exc_info=True)
            return None

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

    async def _send_chart_to_all(self, chart_bytes: bytes, caption: str, symbol: str, reply_markup=None):
        """Отправляет график + подпись всем одобренным пользователям."""
        recipients = await self._get_notification_recipients()
        photo = BufferedInputFile(chart_bytes, filename=f"signal_{symbol}.png")
        
        for uid in recipients:
            try:
                await self.bot.send_photo(
                    uid,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.error("Failed to send chart to %d: %s", uid, e)
                # Fallback: отправляем текст без графика
                try:
                    await self.bot.send_message(uid, caption, parse_mode="HTML", reply_markup=reply_markup)
                except Exception as e2:
                    logger.error("Failed to send text fallback to %d: %s", uid, e2)

    async def _send_to_all(self, text: str, reply_markup=None):
        """Отправляет текст всем одобренным пользователям."""
        recipients = await self._get_notification_recipients()
        for uid in recipients:
            try:
                await self.bot.send_message(uid, text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception as e:
                logger.error("Failed to send to %d: %s", uid, e)
