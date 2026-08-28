import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        self._cache_data = await response.json()
                        self._cache_time = now
                        return self._cache_data
                    else:
                        logger.warning("Failed to fetch economic calendar: HTTP %s. Using cache.", response.status)
                        # Защита от частых запросов при 429 — не долбить сервер чаще раза в 15 минут
                        self._cache_time = now - timedelta(minutes=45)
                        return self._cache_data
        except Exception as e:
            logger.warning("Error fetching economic calendar: %s. Using cached data.", e)
            self._cache_time = now - timedelta(minutes=45)
            return self._cache_data

    def _parse_datetime(self, date_str: str, time_str: str) -> datetime | None:
        if not time_str or time_str.lower() in ("all day", "tentative"):
            return None
        
        try:
            # date_str: "08-18-2026", time_str: "8:30am"
            dt_str = f"{date_str} {time_str}"
            dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
            dt = dt.replace(tzinfo=self.api_tz)
            return dt
        except Exception as e:
            logger.warning("Error parsing date/time: %s %s - %s", date_str, time_str, e)
            return None

    async def get_upcoming_high_impact(self, within_minutes: int = 120) -> list[EconomicEvent]:
        events = await self.fetch_events()
        now = datetime.now(timezone.utc)
        result = []

        for item in events:
            if item.get("impact", "") != "High":
                continue

            dt = self._parse_datetime(item.get("date", ""), item.get("time", ""))
            if not dt:
                continue
            
            minutes_until = int((dt - now).total_seconds() / 60)
            if 0 <= minutes_until <= within_minutes:
                country = item.get("country", "")
                affected_pairs = config.get_affected_pairs(country)
                
                # Format to user timezone
                user_dt = dt.astimezone(self.timezone)
                
                event = EconomicEvent(
                    title=item.get("title", ""),
                    country=country,
                    date_str=user_dt.strftime("%Y-%m-%d"),
                    time_str=user_dt.strftime("%H:%M"),
                    impact=item.get("impact", ""),
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
        events = await self.fetch_events()
        now = datetime.now(timezone.utc)
        result = []

        for item in events:
            if item.get("impact", "") != "High":
                continue

            dt = self._parse_datetime(item.get("date", ""), item.get("time", ""))
            if not dt:
                continue
            
            hours_until = (dt - now).total_seconds() / 3600
            
            # For today and tomorrow (within 48 hours)
            if -24 <= hours_until <= 48:
                minutes_until = int((dt - now).total_seconds() / 60)
                country = item.get("country", "")
                affected_pairs = config.get_affected_pairs(country)
                
                user_dt = dt.astimezone(self.timezone)
                
                event = EconomicEvent(
                    title=item.get("title", ""),
                    country=country,
                    date_str=user_dt.strftime("%Y-%m-%d"),
                    time_str=user_dt.strftime("%H:%M"),
                    impact=item.get("impact", ""),
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
        else:
            time_str = f"<b>через {event.minutes_until} мин</b>"
            
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
            f"│ • Не открывать новые позиции за 30 мин до релиза\n"
            f"│ • Защитить открытые ордера безубытком (BE)\n"
            f"│ • Ожидать повышенную волатильность и спайки\n"
            f"└──────────────────────────────────────\n"
            f"💼 <i>Wall Street Macro Risk Controller</i>"
        )
