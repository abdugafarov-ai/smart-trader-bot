import zoneinfo
from dataclasses import dataclass
from datetime import time, datetime, timedelta
from typing import Optional, List

@dataclass
class SessionInfo:
    name: str
    is_active: bool
    start_utc: time
    end_utc: time
    remaining: Optional[timedelta]
    volatility: str
    recommended_strategies: list[str]
    emoji: str
    description: str

class TradingSessions:
    """
    Институциональный менеджер торговых сессий с полной поддержкой перехода на летнее/зимнее время (DST).
    Динамически вычисляет границы сессий в UTC на основе локального времени Лондона и Нью-Йорка.
    """
    def __init__(self, timezone: str = 'Asia/Tashkent'):
        self.timezone = zoneinfo.ZoneInfo(timezone)
        self.utc_tz = zoneinfo.ZoneInfo("UTC")
        self.london_tz = zoneinfo.ZoneInfo("Europe/London")
        self.ny_tz = zoneinfo.ZoneInfo("America/New_York")
        self.tokyo_tz = zoneinfo.ZoneInfo("Asia/Tokyo")

    def _get_utc_now(self) -> datetime:
        return datetime.now(self.utc_tz)

    def _get_dynamic_sessions_data(self) -> List[dict]:
        now_utc = self._get_utc_now()
        cur_date = now_utc.date()

        # 1. Tokyo / Asia: 09:00 - 18:00 JST (No DST in Japan) -> convert to UTC
        tokyo_start = datetime.combine(cur_date, time(9, 0), tzinfo=self.tokyo_tz).astimezone(self.utc_tz).time()
        tokyo_end = datetime.combine(cur_date, time(18, 0), tzinfo=self.tokyo_tz).astimezone(self.utc_tz).time()

        # 2. London: 08:00 - 16:30 London local (DST-aware GMT/BST) -> convert to UTC
        london_start = datetime.combine(cur_date, time(8, 0), tzinfo=self.london_tz).astimezone(self.utc_tz).time()
        london_end = datetime.combine(cur_date, time(16, 30), tzinfo=self.london_tz).astimezone(self.utc_tz).time()

        # 3. New York: 08:00 - 17:00 NY local (DST-aware EST/EDT) -> convert to UTC
        ny_start = datetime.combine(cur_date, time(8, 0), tzinfo=self.ny_tz).astimezone(self.utc_tz).time()
        ny_end = datetime.combine(cur_date, time(17, 0), tzinfo=self.ny_tz).astimezone(self.utc_tz).time()

        # 4. London-NY Overlap: 13:00 - 16:30 London local -> convert to UTC
        overlap_start = datetime.combine(cur_date, time(13, 0), tzinfo=self.london_tz).astimezone(self.utc_tz).time()
        overlap_end = datetime.combine(cur_date, time(16, 30), tzinfo=self.london_tz).astimezone(self.utc_tz).time()

        return [
            {
                "name": "Sydney / Tokyo (Asia)",
                "start": tokyo_start,
                "end": tokyo_end,
                "emoji": "🌏",
                "volatility": "LOW",
                "strategies": ['S&D', 'Wyckoff'],
                "description": "Азиатский диапазон ликвидности."
            },
            {
                "name": "London Session",
                "start": london_start,
                "end": london_end,
                "emoji": "🇬🇧",
                "volatility": "HIGH",
                "strategies": ['ICT/SMC', 'Breakout', 'Judas Swing'],
                "description": "Лондонский импульс и снятие ликвидности (DST-aware)."
            },
            {
                "name": "New York Session",
                "start": ny_start,
                "end": ny_end,
                "emoji": "🇺🇸",
                "volatility": "HIGH",
                "strategies": ['ICT/SMC', 'Order Block', 'Macro Releases'],
                "description": "Нью-Йоркская волатильность (DST-aware)."
            },
            {
                "name": "London-NY Overlap",
                "start": overlap_start,
                "end": overlap_end,
                "emoji": "⭐",
                "volatility": "VERY HIGH",
                "strategies": ['ICT/SMC', 'OTE', 'High Probability'],
                "description": "Пересечение Лондона и Нью-Йорка. Пиковая ликвидность."
            }
        ]

    def _is_time_between(self, begin_time: time, end_time: time, check_time: time) -> bool:
        if begin_time < end_time:
            return begin_time <= check_time <= end_time
        else:
            return check_time >= begin_time or check_time <= end_time

    def _calc_remaining(self, now: datetime, end_time: time) -> timedelta:
        end_dt = datetime.combine(now.date(), end_time, tzinfo=self.utc_tz)
        if now.time() > end_time:
            end_dt += timedelta(days=1)
        return end_dt - now

    def _get_all_sessions(self) -> list[SessionInfo]:
        now_utc = self._get_utc_now()
        now_time = now_utc.time()

        sessions = []
        for s in self._get_dynamic_sessions_data():
            is_active = self._is_time_between(s["start"], s["end"], now_time)
            remaining = self._calc_remaining(now_utc, s["end"]) if is_active else None

            sessions.append(SessionInfo(
                name=s["name"],
                is_active=is_active,
                start_utc=s["start"],
                end_utc=s["end"],
                remaining=remaining,
                volatility=s["volatility"],
                recommended_strategies=s["strategies"],
                emoji=s["emoji"],
                description=s["description"]
            ))
        return sessions

    def get_current_sessions(self) -> list[SessionInfo]:
        return [s for s in self._get_all_sessions() if s.is_active]

    def get_best_session(self) -> Optional[SessionInfo]:
        current = self.get_current_sessions()
        if not current:
            return None
        overlap = next((s for s in current if s.name == "London-NY Overlap"), None)
        if overlap:
            return overlap
        return current[0]

    def format_sessions_text(self) -> str:
        all_sessions = self._get_all_sessions()
        lines = [
            "⏰ <b>РАСПИСАНИЕ ТОРГОВЫХ СЕССИЙ (UTC+5 / Ташкент)</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        now_utc = self._get_utc_now()

        for s in all_sessions:
            start_local = datetime.combine(now_utc.date(), s.start_utc, tzinfo=self.utc_tz).astimezone(self.timezone)
            end_local = datetime.combine(now_utc.date(), s.end_utc, tzinfo=self.utc_tz).astimezone(self.timezone)
            time_str = f"{start_local.strftime('%H:%M')} — {end_local.strftime('%H:%M')}"

            if s.is_active:
                status_icon = "🟢"
                status_text = "<b>АКТИВНА</b>"
                rem_hours, rem_remainder = divmod(s.remaining.total_seconds(), 3600)
                rem_mins, _ = divmod(rem_remainder, 60)
                rem_str = f" [осталось {int(rem_hours)}ч {int(rem_mins)}м]"
            else:
                status_icon = "⚪"
                status_text = "<i>Закрыта</i>"
                rem_str = ""

            lines.append(f"{status_icon} <b>{s.name}</b> {s.emoji}")
            lines.append(f"├ 🕒 <b>Часы работы:</b> <code>{time_str}</code>")
            lines.append(f"└ 📊 <b>Статус:</b> {status_text}{rem_str}\n")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <i>Пик объёмов и активности: Лондонская и Нью-Йоркская сессии (с динамическим учетом перехода на летнее время DST).</i>")
        return "\n".join(lines)
