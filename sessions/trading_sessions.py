from dataclasses import dataclass
from datetime import time, datetime, timedelta
from typing import Optional
import zoneinfo

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
    def __init__(self, timezone: str = 'Asia/Tashkent'):
        self.timezone = zoneinfo.ZoneInfo(timezone)
        self.utc_tz = zoneinfo.ZoneInfo("UTC")

    def _get_utc_now(self) -> datetime:
        return datetime.now(self.utc_tz)

    def _get_sessions_data(self):
        return [
            {
                "name": "Sydney / Tokyo (Asia)",
                "start": time(0, 0),
                "end": time(8, 0),
                "emoji": "🌏",
                "volatility": "LOW",
                "strategies": ['S&D', 'Wyckoff'],
                "description": "Азиатский диапазон ликвидности."
            },
            {
                "name": "London Session",
                "start": time(7, 0),
                "end": time(15, 0),
                "emoji": "🇬🇧",
                "volatility": "HIGH",
                "strategies": ['ICT/SMC', 'Breakout', 'Judas Swing'],
                "description": "Лондонский импульс и снятие ликвидности."
            },
            {
                "name": "New York Session",
                "start": time(12, 0),
                "end": time(20, 0),
                "emoji": "🇺🇸",
                "volatility": "HIGH",
                "strategies": ['ICT/SMC', 'Order Block', 'Macro Releases'],
                "description": "Нью-Йоркская волатильность."
            },
            {
                "name": "London-NY Overlap",
                "start": time(12, 0),
                "end": time(15, 0),
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
        for s in self._get_sessions_data():
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
            "🏛 <b>WALL STREET TERMINAL | GLOBAL SESSIONS</b>",
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
                
            strats = ", ".join(s.recommended_strategies)
            
            lines.append(f"{status_icon} <b>{s.name}</b> {s.emoji}")
            lines.append(f"┌ 🕒 <b>Часы:</b> <code>{time_str}</code>")
            lines.append(f"├ 📊 <b>Статус:</b> {status_text}{rem_str}")
            lines.append(f"├ ⚡ <b>Волатильность:</b> <code>{s.volatility}</code>")
            lines.append(f"└ 🎯 <b>Модели:</b> <code>{strats}</code>\n")
            
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💼 <i>Торгуйте в периоды высокой ликвидности (London & NY).</i>")
        return "\n".join(lines)
