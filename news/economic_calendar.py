"""
Экономический календарь — парсер ForexFactory (faireconomy.media API).
Получает макроэкономические события, предупреждает о важных новостях (High Impact).
Поддерживает НОВЫЙ ISO формат дат API (2026+) и старый формат (date + time раздельно).
"""

import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil import parser as dateutil_parser

import config
from strategies.base import EconomicEvent

logger = logging.getLogger(__name__)


class EconomicCalendar:
    _shared_cache_data: list[dict] = []
    _shared_cache_time: datetime | None = None
    _last_successful_fetch: datetime | None = None
    _last_failed_time: datetime | None = None
    _last_fetch_error: str = ""
    _fetch_lock = asyncio.Lock()

    def __init__(self, timezone_str: str = 'Asia/Tashkent'):
        self.timezone_str = timezone_str
        self.timezone = ZoneInfo(timezone_str)
        self.api_tz = ZoneInfo('America/New_York')
        self.url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    async def fetch_events(self) -> list[dict]:
        async with self._fetch_lock:
            now = datetime.now(timezone.utc)
            # 1. Если есть общий кэш свежее 60 минут — возвращаем без сетевого запроса
            if EconomicCalendar._shared_cache_time and (now - EconomicCalendar._shared_cache_time) < timedelta(hours=1) and EconomicCalendar._shared_cache_data:
                return EconomicCalendar._shared_cache_data

            # 2. Если недавняя попытка завершилась ошибкой — не спамим сервер чаще чем раз в 10 минут
            if EconomicCalendar._last_failed_time and (now - EconomicCalendar._last_failed_time) < timedelta(minutes=10):
                return EconomicCalendar._shared_cache_data
        
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.forexfactory.com/",
            }
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(self.url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json(content_type=None)
                            if isinstance(data, list) and len(data) > 0:
                                EconomicCalendar._shared_cache_data = data
                                EconomicCalendar._shared_cache_time = now
                                EconomicCalendar._last_successful_fetch = now
                                EconomicCalendar._last_failed_time = None
                                EconomicCalendar._last_fetch_error = ""
                                logger.info("Fetched %d economic events from ForexFactory.", len(data))
                            else:
                                logger.warning("ForexFactory returned empty or invalid data.")
                                EconomicCalendar._last_failed_time = now
                                EconomicCalendar._last_fetch_error = "Empty or invalid data payload returned"
                            return EconomicCalendar._shared_cache_data
                        else:
                            logger.warning("Failed to fetch economic calendar: HTTP %s (using cached data)", response.status)
                            EconomicCalendar._last_failed_time = now
                            EconomicCalendar._last_fetch_error = f"HTTP {response.status}"
                            return EconomicCalendar._shared_cache_data
            except Exception as e:
                logger.warning("Error fetching economic calendar: %s", e)
                EconomicCalendar._last_failed_time = now
                EconomicCalendar._last_fetch_error = str(e)
                return EconomicCalendar._shared_cache_data

    @classmethod
    def get_calendar_health(cls, now_dt: datetime | None = None) -> tuple[bool, str]:
        """
        Проверяет достоверность и актуальность данных календаря новостей.
        Возвращает (is_healthy, status_message).
        При отсутствии данных, ошибке API или устаревании кэша (>4 часов без обновления)
        возвращает (False, reason).
        """
        curr = now_dt if now_dt is not None else datetime.now(timezone.utc)
        if curr.tzinfo is None:
            curr = curr.replace(tzinfo=timezone.utc)
        if not cls._shared_cache_data:
            err = cls._last_fetch_error or "Данные календаря отсутствуют или не загружены"
            return False, f"Календарь новостей пуст ({err})"
        if cls._last_successful_fetch is None:
            return False, "Календарь новостей не имеет отметки успешной загрузки"
        age_hours = (curr - cls._last_successful_fetch).total_seconds() / 3600.0
        if age_hours > 24.0:
            return False, f"Данные календаря устарели (возраст {age_hours:.1f}ч > 24ч)"
        if cls._last_failed_time is not None and age_hours > 4.0:
            return False, f"Ошибка обновления календаря при устаревшем кэше ({age_hours:.1f}ч > 4ч): {cls._last_fetch_error}"
        return True, "OK"

    def check_news_blocked_sync(
        self,
        symbol: str,
        now_dt: datetime | None = None,
        pre_minutes: int = 30,
        post_minutes: int = 30,
    ) -> tuple[bool, str]:
        """
        Синхронная проверка блокировки новостей для символа (по кэшу событий).
        - Если календарь нездоров, возвращает блокировку: (True, "NO_TRADE_NEWS_CALENDAR_UNAVAILABLE: ...")
        - Если в окне [-post_minutes, +pre_minutes] есть High Impact новость по валютам пары: (True, reason)
        - Иначе (False, "")
        """
        is_healthy, h_reason = self.get_calendar_health(now_dt)
        if not is_healthy:
            return True, f"NO_TRADE_NEWS_CALENDAR_UNAVAILABLE: {h_reason}. Новые входы запрещены до восстановления достоверных данных."

        now = now_dt if now_dt is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        sym_u = symbol.upper().replace("/", "").replace("=X", "")
        currencies = config.PAIR_CURRENCIES.get(sym_u)
        if not currencies:
            if "XAU" in sym_u or "GOLD" in sym_u:
                currencies = ["USD"]
            elif len(sym_u) == 6:
                currencies = [sym_u[:3], sym_u[3:6]]
            else:
                currencies = ["USD"]

        for item in self._shared_cache_data:
            impact = str(item.get("impact", "")).strip().capitalize()
            if impact != "High":
                continue
            item_country = str(item.get("country", "")).strip().upper()
            if item_country not in currencies:
                continue

            dt = self._parse_datetime(item)
            if not dt:
                # Fail-closed: блокируем входы при наличии High Impact события с неопределенным временем (All Day, Tentative) на сегодня
                d_str = str(item.get("date", "")).strip()
                t_str = str(item.get("time", "")).strip().lower()
                title = item.get("title", "High Impact Event")
                is_today_event = False
                if d_str:
                    try:
                        from dateutil import parser as dt_parser
                        parsed_d = dt_parser.parse(d_str).date()
                        if parsed_d == now.date():
                            is_today_event = True
                    except Exception:
                        pass
                if is_today_event or t_str in ("all day", "tentative"):
                    return True, f"NO_TRADE_NEWS_UNPARSEABLE_EVENT: Событие '{title}' ({item_country}) с неопределенным временем ({t_str or 'не указано'}). Новые входы заблокированы."
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)

            minutes_until = int((dt - now).total_seconds() / 60)
            if -post_minutes <= minutes_until <= pre_minutes:
                title = item.get("title", "High Impact Event")
                timing_str = f"через {minutes_until} мин" if minutes_until >= 0 else f"{abs(minutes_until)} мин назад"
                return True, f"Новостной фильтр: {title} ({item_country}) {timing_str} (окно ±{pre_minutes}м)"

        return False, ""

    async def is_news_blocked_for_pair(
        self,
        symbol: str,
        now_dt: datetime | None = None,
        pre_minutes: int = 30,
        post_minutes: int = 30,
    ) -> tuple[bool, str]:
        """
        Асинхронная проверка блокировки новостей с предварительной загрузкой при необходимости.
        """
        await self.fetch_events()
        return self.check_news_blocked_sync(symbol, now_dt=now_dt, pre_minutes=pre_minutes, post_minutes=post_minutes)

    def _parse_datetime(self, item: dict) -> datetime | None:
        """
        Парсит дату/время события. Поддерживает оба формата API:
        - Новый ISO: {"date": "2026-09-01T10:00:00-04:00"}
        - Старый:    {"date": "09-01-2026", "time": "10:00am"}
        """
        date_str = item.get("date", "")
        time_str = item.get("time", "")

        if not date_str:
            return None

        # ── Попытка 1: Новый ISO формат (2026+) ──
        # Пример: "2026-09-01T10:00:00-04:00"
        if "T" in date_str:
            try:
                dt = dateutil_parser.isoparse(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=self.api_tz)
                return dt
            except (ValueError, TypeError) as e:
                logger.debug("ISO parse failed for '%s': %s", date_str, e)

        # ── Попытка 2: Старый формат (date + time раздельно) ──
        if not time_str or time_str.lower() in ("all day", "tentative", ""):
            return None

        try:
            dt_str = f"{date_str} {time_str}"
            dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
            dt = dt.replace(tzinfo=self.api_tz)
            return dt
        except (ValueError, TypeError) as e:
            logger.debug("Legacy parse failed for '%s %s': %s", date_str, time_str, e)

        return None

    async def get_upcoming_high_impact(self, within_minutes: int = 120, post_minutes: int = 20) -> list[EconomicEvent]:
        """
        Возвращает события High Impact за период:
        от post_minutes ПОСЛЕ новости (кулдаун волатильности)
        до within_minutes ДО новости.
        """
        events = await self.fetch_events()
        now = datetime.now(timezone.utc)
        result = []

        for item in events:
            impact = item.get("impact", "")
            if impact not in ("High",):
                continue

            dt = self._parse_datetime(item)
            if not dt:
                continue
            
            minutes_until = int((dt - now).total_seconds() / 60)
            if -post_minutes <= minutes_until <= within_minutes:
                country = item.get("country", "")
                affected_pairs = config.get_affected_pairs(country)
                
                user_dt = dt.astimezone(self.timezone)
                
                event = EconomicEvent(
                    title=item.get("title", ""),
                    country=country,
                    date_str=user_dt.strftime("%Y-%m-%d"),
                    time_str=user_dt.strftime("%H:%M"),
                    impact=impact,
                    forecast=item.get("forecast", ""),
                    previous=item.get("previous", ""),
                    actual=item.get("actual", ""),
                    affected_pairs=affected_pairs,
                    minutes_until=minutes_until
                )
                result.append(event)
        
        result.sort(key=lambda x: x.minutes_until)
        return result

    async def get_events_for_display(self) -> list[EconomicEvent]:
        """Возвращает все High Impact события за ±48 часов для отображения в /news."""
        events = await self.fetch_events()
        now = datetime.now(timezone.utc)
        result = []

        for item in events:
            impact = item.get("impact", "")
            if impact != "High":
                continue

            dt = self._parse_datetime(item)
            if not dt:
                continue
            
            hours_until = (dt - now).total_seconds() / 3600
            
            if -24 <= hours_until <= 72:
                minutes_until = int((dt - now).total_seconds() / 60)
                country = item.get("country", "")
                affected_pairs = config.get_affected_pairs(country)
                
                user_dt = dt.astimezone(self.timezone)
                
                event = EconomicEvent(
                    title=item.get("title", ""),
                    country=country,
                    date_str=user_dt.strftime("%Y-%m-%d"),
                    time_str=user_dt.strftime("%H:%M"),
                    impact=impact,
                    forecast=item.get("forecast", ""),
                    previous=item.get("previous", ""),
                    actual=item.get("actual", ""),
                    affected_pairs=affected_pairs,
                    minutes_until=minutes_until
                )
                result.append(event)
        
        result.sort(key=lambda x: x.minutes_until)
        return result

    def format_event(self, event: EconomicEvent) -> str:
        country_name_map = {
            "USD": "США", "EUR": "Еврозона", "GBP": "Великобритания", 
            "JPY": "Япония", "CHF": "Швейцария", "AUD": "Австралия", 
            "NZD": "Новая Зеландия", "CAD": "Канада"
        }
        country_name = country_name_map.get(event.country, event.country)
        flag = config.CURRENCY_COUNTRY.get(event.country, "")
        country_display = f"{flag} {country_name}".strip()

        if event.minutes_until < 0:
            time_str = "<i>уже состоялось</i>"
        elif event.minutes_until < 60:
            time_str = f"<b>через {event.minutes_until} мин</b>"
        else:
            hours = event.minutes_until // 60
            mins = event.minutes_until % 60
            time_str = f"<b>через {hours}ч {mins}м</b>"
            
        now_dt = datetime.now(self.timezone)
        utc_offset_sec = now_dt.utcoffset().total_seconds() if now_dt.utcoffset() else 0
        utc_offset = int(utc_offset_sec / 3600)
        utc_str = f"UTC+{utc_offset}" if utc_offset >= 0 else f"UTC{utc_offset}"
            
        pairs_str = f"<code>{', '.join(event.affected_pairs)}</code>" if event.affected_pairs else "Все мажоры"
        impact_icon = "🔴" if event.impact.lower() == "high" else "🟠"

        return (
            f"📰 <b>{event.title}</b>\n"
            f"┌ 🕒 <b>Время:</b> <code>{event.time_str} ({utc_str})</code> | {time_str}\n"
            f"├ 🏳️ <b>Регион:</b> <code>{country_display}</code> | {impact_icon} <b>{event.impact.upper()}</b>\n"
            f"├ 📊 <b>Прогноз:</b> <code>{event.forecast or '—'}</code> | <b>Пред.:</b> <code>{event.previous or '—'}</code>\n"
            f"└ 🔗 <b>Инструменты:</b> {pairs_str}\n"
        )

    def format_warning(self, event: EconomicEvent) -> str:
        country_name_map = {
            "USD": "США", "EUR": "Еврозона", "GBP": "Великобритания", 
            "JPY": "Япония", "CHF": "Швейцария", "AUD": "Австралия", 
            "NZD": "Новая Зеландия", "CAD": "Канада"
        }
        country_name = country_name_map.get(event.country, event.country)
        flag = config.CURRENCY_COUNTRY.get(event.country, "")
        country_display = f"{flag} {country_name}".strip()

        now_dt = datetime.now(self.timezone)
        utc_offset_sec = now_dt.utcoffset().total_seconds() if now_dt.utcoffset() else 0
        utc_offset = int(utc_offset_sec / 3600)
        utc_str = f"UTC+{utc_offset}" if utc_offset >= 0 else f"UTC{utc_offset}"
        
        pairs_str = f"<code>{', '.join(event.affected_pairs)}</code>" if event.affected_pairs else "Все валютные пары"

        return (
            f"⚠️ <b>MACRO ALERT: ВАЖНАЯ НОВОСТЬ ЧЕРЕЗ {event.minutes_until} МИН</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 <b>СОБЫТИЕ:</b> <b>{event.title}</b>\n"
            f"🕐 <b>ВРЕМЯ:</b>    <code>{event.time_str} ({utc_str})</code>\n"
            f"🏳️ <b>РЕГИОН:</b>   <code>{country_display}</code>\n"
            f"💥 <b>ИМПАКТ:</b>   🔴 <b>HIGH IMPACT RELEASE</b>\n\n"
            f"<b>ЗАТРОНУТЫЕ АКТИВЫ:</b>\n{pairs_str}\n\n"
            f"┌── <b>ПРАВИЛА ИНСТИТУЦИОНАЛЬНОГО РИСКА</b> ─\n"
            f"│ ⚫ Не открывать новые позиции за 30 мин до релиза\n"
            f"│ ⚫ Защитить открытые ордера безубытком (BE)\n"
            f"│ ⚫ Ожидать повышенную волатильность и спайки\n"
            f"└──────────────────────────────────────\n"
            f"💼 <i>Wall Street Macro Risk Controller</i>"
        )
