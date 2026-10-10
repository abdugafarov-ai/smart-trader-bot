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
        self.signals_skipped_by_spread: int = 0
        self.signals_skipped_by_daily_limit: int = 0
        self.chart_theme: str = getattr(config, "CHART_THEME", "ict")
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

    def _get_currency_exposures(self, symbol: str, direction: str) -> list[str]:
        """
        Возвращает список валютных экспозиций для сделки.
        Например: EURUSD LONG -> ['+EUR', '-USD']
                  USDJPY LONG -> ['+USD', '-JPY']
                  USDJPY SHORT -> ['-USD', '+JPY']
        """
        sym = symbol.upper().replace("/", "").replace("=X", "")
        if len(sym) >= 6 and not sym.endswith("USDT"):
            base, quote = sym[:3], sym[3:6]
            if direction == "LONG":
                return [f"+{base}", f"-{quote}"]
            elif direction == "SHORT":
                return [f"-{base}", f"+{quote}"]
        return []

    # ── Correlation Filter (Институциональный контроль валютной концентрации) ──
    async def _check_correlation_limit(self, symbol: str, direction: str) -> bool:
        """
        Проверяет, не превышен ли лимит концентрации риска по одной валюте (макс. 2 позиции в одну сторону).
        Например: не более 2 сделок, шортящих USD одновременно (+EUR/-USD, +GBP/-USD).
        """
        try:
            new_exposures = self._get_currency_exposures(symbol, direction)
            if not new_exposures:
                return True

            open_signals = await get_active_signals()
            pending = await get_pending_signals()
            all_open = open_signals + pending

            # Считаем текущие валютные экспозиции по всем открытым ордерам
            exposure_counts: dict[str, int] = {}
            for sig in all_open:
                s_sym = sig.get('symbol', '')
                s_dir = sig.get('direction', '')
                for exp in self._get_currency_exposures(s_sym, s_dir):
                    exposure_counts[exp] = exposure_counts.get(exp, 0) + 1

            # Проверяем, не превысит ли новый ордер лимит по любой валюте
            max_allowed = getattr(config, 'MAX_CORRELATED_SIGNALS', 2)
            for exp in new_exposures:
                current_cnt = exposure_counts.get(exp, 0)
                if current_cnt >= max_allowed:
                    logger.info("Currency correlation limit: %s %s blocked (%d/%d on %s)",
                                symbol, direction, current_cnt, max_allowed, exp)
                    return False

            return True
        except Exception as e:
            logger.error("Correlation check error: %s", e)
            return True

    # ── Concurrent Active Orders Limit ──
    async def _check_concurrent_limit(self) -> bool:
        """
        Проверяет, не превышен ли лимит одновременно активных/отложенных ордеров.
        В режиме 'micro' разрешен максимум 1 ордер.
        В режиме 'prop' разрешено до MAX_CONCURRENT_POSITIONS (5) ордеров.
        """
        try:
            from db.database import get_bot_setting
            from trading.execution_bridge import bridge_manager
            trading_mode = await get_bot_setting("trading_mode", "micro")
            max_orders = config.MICRO_MAX_CONCURRENT_ORDERS if trading_mode == "micro" else getattr(config, 'MAX_CONCURRENT_POSITIONS', 5)

            # Проверяем живую телеметрию MT5, если терминал в сети
            is_mt5_online, _ = bridge_manager.is_mt5_online()
            if is_mt5_online:
                positions = bridge_manager.mt5_telemetry.get("positions") or []
                orders = bridge_manager.mt5_telemetry.get("orders") or []
                total_mt5 = len(positions) + len(orders)
                if total_mt5 >= max_orders:
                    logger.info("MT5 active slots limit reached (%d/%d, mode: %s). New signals paused.",
                                total_mt5, max_orders, trading_mode.upper())
                    return False

            open_signals = await get_active_signals()
            pending = await get_pending_signals()
            total_active = len(open_signals) + len(pending)
            if total_active >= max_orders:
                logger.info("Concurrent order limit reached (%d/%d active/pending, mode: %s). New signals paused until orders close.",
                            total_active, max_orders, trading_mode.upper())
                return False
            return True
        except Exception as e:
            logger.error("Concurrent limit check error: %s", e)
            return True

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
        from db.database import get_bot_setting

        if DataFetcher.is_weekend():
            logger.info("Weekend: markets closed. Scanner paused.")
            return

        # ── Проверка институционального недельного окна (Smart Weekly Window) ──
        window_open, window_reason = config.is_weekly_trading_window_open()
        if not window_open:
            logger.info("Smart Weekly Window active: %s. New signals paused.", window_reason)
            return

        # ── Проверка замка дневного риска (Daily Drawdown Lock) ──
        from trading.execution_bridge import bridge_manager
        if bridge_manager.daily_loss_locked:
            logger.info("Daily loss lock active: %s. Scanner paused.", bridge_manager.daily_lock_reason)
            return

        # Текущий профиль торговли (micro vs prop)
        trading_mode = await get_bot_setting("trading_mode", "micro")

        # Kill Zone check
        kz = config.get_current_kill_zone()
        in_kill_zone = kz is not None
        if in_kill_zone:
            logger.info("Active Kill Zone: %s — high priority scanning", kz)

        # ── Проверка лимита одновременно открытых ордеров ──
        if not await self._check_concurrent_limit():
            return

        # Daily limit check (persistent from DB)
        if not await self._check_daily_limit():
            daily_count = await get_today_signal_count()
            logger.info("Daily signal limit reached (%d/%d). Scanner paused until tomorrow.",
                        daily_count, config.MAX_SIGNALS_PER_DAY)
            return

        # Drawdown Protection: полностью автоматический режим (без остановки сканера!)
        from db.database import get_consecutive_sl_count
        consecutive_sl = await get_consecutive_sl_count(max_lookback_hours=12.0)
        drawdown_mode = consecutive_sl >= 3
        if drawdown_mode:
            logger.info("Auto Drawdown Filter Active (%d SL). Strict 5-star filter engaged automatically.", consecutive_sl)

        news_blocked = await self._get_news_blocked_pairs()
        scan_list = self.symbols
        logger.info("Scanning %d pairs (Mode: %s | Kill Zone: %s | Strict Mode: %s)...",
                    len(scan_list), trading_mode.upper(), kz or "OFF", "ON" if drawdown_mode else "OFF")

        for symbol in scan_list:
            try:
                # ── ФИЛЬТР: Полный запрет золота и запрещенных пар ──
                if symbol in getattr(config, "BANNED_PAIRS", ["XAUUSD", "GOLD"]) or "XAU" in symbol:
                    continue

                # ── ФИЛЬТР 0: Исключение пар для режима Микро ($12) ──
                if trading_mode == "micro" and symbol in getattr(config, "MICRO_EXCLUDED_PAIRS", ["XAUUSD"]):
                    continue

                # ── ФИЛЬТР 0.1: Предварительная проверка слотов и риска MT5 Risk Guard ──
                is_mt5_online, _ = bridge_manager.is_mt5_online()
                if is_mt5_online and bridge_manager.enabled:
                    allowed, block_reason = bridge_manager.can_open_new_position(symbol, trading_mode)
                    if not allowed:
                        if "Лимит слотов" in block_reason or "Защита от дневного убытка" in block_reason:
                            logger.info("Scanner paused by Risk Guard: %s", block_reason)
                            break
                        if any(k in block_reason for k in ["уже открыта", "уже выставлен", "лимит корреляции"]):
                            continue

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

                # ── ФИЛЬТР 6: Concurrent orders limit ──
                if not await self._check_concurrent_limit():
                    break

                result = await run_multi_tf_analysis(symbol)
                if not result or result.overall_direction == 'NEUTRAL':
                    continue

                min_stars = config.MIN_SIGNAL_STARS  # = 4
                # В режиме серии стопов автоматически требуем наивысшее качество (5 звёзд)
                if drawdown_mode:
                    min_stars = 5
                # Kill Zone НЕ снижает планку ниже 4★ (строгий режим)

                if result.overall_stars >= min_stars and (result.risk_reward_1 or 0) >= 2.0:

                    # ── ФИЛЬТР МИКРО-СЧЁТА: Ограничение размера стоп-лосса (Max SL <= 18 pips) ──
                    if result.entry is not None and result.stop_loss is not None:
                        sl_dist = abs(result.entry - result.stop_loss)
                        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
                        if "JPY" in sym_clean:
                            sl_pips = sl_dist / 0.01
                        elif sym_clean.startswith("XAU"):
                            sl_pips = sl_dist / 0.1
                        else:
                            sl_pips = sl_dist / 0.0001

                        if trading_mode == "micro" and sl_pips > config.MICRO_MAX_SL_PIPS:
                            logger.info(
                                "Micro Mode ($12): %s SL is %.1f pips (exceeds max allowed %.1f pips / $%.2f risk). Setup skipped.",
                                symbol, sl_pips, config.MICRO_MAX_SL_PIPS, sl_pips * 0.1
                            )
                            continue

                    # ── ФИЛЬТР 7: Корреляция ──
                    if not await self._check_correlation_limit(symbol, result.overall_direction):
                        self.signals_skipped_by_correlation += 1
                        continue

                    # ── ФИЛЬТР 8: Защита от расширения спреда (Spread Spike Guard) ──
                    from trading.execution_bridge import bridge_manager
                    spread_ok, cur_spread, max_allowed = bridge_manager.is_spread_acceptable(symbol)
                    if not spread_ok:
                        logger.warning("Signal for %s BLOCKED: Spread spike detected (%.1f > %.1f pips). Waiting for market to calm.",
                                       symbol, cur_spread, max_allowed)
                        self.signals_skipped_by_spread = getattr(self, "signals_skipped_by_spread", 0) + 1
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
                    msg_admin = format_notification(result, is_admin=True)
                    msg_client = format_notification(result, is_admin=False)

                    # Добавляем Kill Zone метку
                    if in_kill_zone:
                        msg_admin = f"⚡ <b>KILL ZONE: {kz}</b>\n\n" + msg_admin
                        msg_client = f"⚡ <b>KILL ZONE: {kz}</b>\n\n" + msg_client

                    kb = signal_inline_keyboard(symbol)

                    if chart_bytes:
                        await self._send_chart_to_all(chart_bytes, caption=msg_client, symbol=symbol,
                                                     reply_markup=kb, caption_admin=msg_admin)
                    else:
                        await self._send_to_all(msg_client, reply_markup=kb, text_admin=msg_admin)

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
                    if 0 <= event.minutes_until <= warn_minutes:
                        warn_key = f"{event.title}_{event.date_str}_{warn_minutes}"
                        if warn_key not in self._news_warned:
                            self._news_warned.add(warn_key)
                            msg = calendar.format_warning(event)
                            await self._send_to_all(msg)
                            logger.info("News warning: %s in %d min", event.title, event.minutes_until)
        except Exception as e:
            logger.error("News check error: %s", e, exc_info=True)

    async def _send_chart_to_all(self, chart_bytes: bytes, caption: str, symbol: str, reply_markup=None, caption_admin: str = None):
        recipients = await self._get_notification_recipients()
        photo = BufferedInputFile(chart_bytes, filename=f"signal_{symbol}.png")
        for uid in recipients:
            cap = caption_admin if (uid == config.ADMIN_ID and caption_admin) else caption
            protect = (uid != config.ADMIN_ID)
            try:
                await self.bot.send_photo(uid, photo=photo, caption=cap,
                                          parse_mode="HTML", reply_markup=reply_markup,
                                          protect_content=protect)
            except Exception as e:
                logger.error("Failed to send chart to %d: %s", uid, e)
                try:
                    await self.bot.send_message(uid, cap, parse_mode="HTML", reply_markup=reply_markup,
                                                protect_content=protect)
                except Exception as e2:
                    logger.error("Fallback text also failed for %d: %s", uid, e2)

    async def _send_to_all(self, text: str, reply_markup=None, text_admin: str = None):
        recipients = await self._get_notification_recipients()
        for uid in recipients:
            t = text_admin if (uid == config.ADMIN_ID and text_admin) else text
            protect = (uid != config.ADMIN_ID)
            try:
                await self.bot.send_message(uid, t, parse_mode="HTML", reply_markup=reply_markup,
                                            protect_content=protect)
            except Exception as e:
                logger.error("Failed to send to %d: %s", uid, e)
