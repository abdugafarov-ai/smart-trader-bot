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
    def __init__(self, timezone_str: str = 'Asia/Tashkent'):
        self.timezone_str = timezone_str
        self.timezone = ZoneInfo(timezone_str)
        self.api_tz = ZoneInfo('America/New_York')
        self._cache_data: list[dict] = []
        self._cache_time: datetime | None = None
        self.url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    async def fetch_events(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        if self._cache_time and (now - self._cache_time) < timedelta(hours=1) and self._cache_data:
            return self._cache_data
        
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
                            self._cache_data = data
                            self._cache_time = now
                            logger.info("Fetched %d economic events from ForexFactory.", len(data))
                        else:
                            logger.warning("ForexFactory returned empty or invalid data.")
                        return self._cache_data
                    else:
                        logger.warning("Failed to fetch economic calendar: HTTP %s", response.status)
                        self._cache_time = now - timedelta(minutes=45)
                        return self._cache_data
        except Exception as e:
            logger.warning("Error fetching economic calendar: %s", e)
            self._cache_time = now - timedelta(minutes=45)
            return self._cache_data

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

    async def get_upcoming_high_impact(self, within_minutes: int = 120) -> list[EconomicEvent]:
        """Возвращает предстоящие High Impact события в пределах N минут."""
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
            if 0 <= minutes_until <= within_minutes:
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
            if impact not in ("High", "Medium"):
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
