"""
Smart Trader Bot — Еженедельный отчёт.
Стиль: 🏛 «Wall Street / Bloomberg Terminal».
Отправляет в субботу сводку за неделю в HTML формате.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import aiosqlite
from aiogram import Bot

import config
from db.users import get_approved_user_ids

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


class WeeklyReporter:
    """Генерирует и отправляет еженедельный отчёт в субботу."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.is_running = False
        self.tz = ZoneInfo(config.TIMEZONE)
        self.report_day = int(getattr(config, 'WEEKLY_REPORT_DAY', 5))  # 5 = суббота
        self.report_hour = int(getattr(config, 'WEEKLY_REPORT_HOUR', 10))

    async def start(self):
        """Запускает фоновый цикл проверки."""
        self.is_running = True
        logger.info("WeeklyReporter started. Report day=%d, hour=%d",
                     self.report_day, self.report_hour)

        while self.is_running:
            try:
                now = datetime.now(self.tz)
                if now.weekday() == self.report_day and now.hour == self.report_hour:
                    await self._generate_and_send()
                    await asyncio.sleep(7200)
                else:
                    await asyncio.sleep(1800)
            except Exception as e:
                logger.error("WeeklyReporter error: %s", e, exc_info=True)
                await asyncio.sleep(1800)

    async def stop(self):
        self.is_running = False

    async def _generate_and_send(self):
        """Генерирует отчёт за последние 7 дней и отправляет."""
        report = await self._build_report()
        if not report:
            return

        user_ids = await get_approved_user_ids()
        if config.ADMIN_ID and config.ADMIN_ID not in user_ids:
            user_ids.append(config.ADMIN_ID)

        for uid in user_ids:
            try:
                await self.bot.send_message(uid, report, parse_mode="HTML")
            except Exception as e:
                logger.error("Failed to send weekly report to %d: %s", uid, e)

        logger.info("Weekly report sent to %d users.", len(user_ids))

    async def _build_report(self) -> str:
        """Собирает статистику за 7 дней."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        try:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ?",
                    (week_ago,),
                )
                total = (await cursor.fetchone())[0]

                if total == 0:
                    return (
                        "📊 <b>WALL STREET TERMINAL | WEEKLY REPORT</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "<i>На этой неделе сетапов не зафиксировано.\n"
                        f"Терминал продолжает мониторинг {len(config.ALL_PAIRS)} активов.</i>\n\n"
                        "💡 <i>Качество &gt; Количество!</i>"
                    )

                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT')",
                    (week_ago,),
                )
                tp_hits = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status = 'SL_HIT'",
                    (week_ago,),
                )
                sl_hits = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status = 'EXPIRED'",
                    (week_ago,),
                )
                expired = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status IN ('PENDING', 'ACTIVE', 'OPEN')",
                    (week_ago,),
                )
                still_open = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status NOT IN ('PENDING', 'ACTIVE', 'OPEN')",
                    (week_ago,),
                )
                total_pips = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT')",
                    (week_ago,),
                )
                tp_pips = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status = 'SL_HIT'",
                    (week_ago,),
                )
                sl_pips = (await cursor.fetchone())[0]

                closed = tp_hits + sl_hits + expired
                win_rate = (tp_hits / closed * 100) if closed > 0 else 0.0

                cursor = await db.execute(
                    "SELECT symbol, direction, pnl_pips FROM signals "
                    "WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT') "
                    "ORDER BY pnl_pips DESC LIMIT 1",
                    (week_ago,),
                )
                best = await cursor.fetchone()

                cursor = await db.execute(
                    "SELECT symbol, direction, pnl_pips FROM signals "
                    "WHERE created_at >= ? AND status = 'SL_HIT' "
                    "ORDER BY pnl_pips ASC LIMIT 1",
                    (week_ago,),
                )
                worst = await cursor.fetchone()

                cursor = await db.execute(
                    "SELECT symbol, COUNT(*) as cnt, "
                    "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) as w, "
                    "SUM(CASE WHEN status = 'SL_HIT' THEN 1 ELSE 0 END) as l "
                    "FROM signals WHERE created_at >= ? "
                    "GROUP BY symbol ORDER BY cnt DESC LIMIT 5",
                    (week_ago,),
                )
                pair_rows = await cursor.fetchall()

                cursor = await db.execute(
                    "SELECT direction, COUNT(*), "
                    "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) "
                    "FROM signals WHERE created_at >= ? GROUP BY direction",
                    (week_ago,),
                )
                dir_rows = await cursor.fetchall()

                cursor = await db.execute(
                    "SELECT AVG(risk_reward) FROM signals "
                    "WHERE created_at >= ? AND risk_reward > 0",
                    (week_ago,),
                )
                avg_rr = (await cursor.fetchone())[0] or 0.0

        except Exception as e:
            logger.error("Weekly report query error: %s", e)
            return ""

        now = datetime.now(self.tz)
        week_start = (now - timedelta(days=7)).strftime("%d.%m")
        week_end = now.strftime("%d.%m.%Y")

        pips_sign = "+" if total_pips >= 0 else ""
        wr_bar_filled = int(win_rate // 10)
        wr_bar = "■" * wr_bar_filled + "□" * (10 - wr_bar_filled)

        lines = [
            "📊 <b>WALL STREET TERMINAL | WEEKLY REPORT</b>",
            f"📅 <code>{week_start} — {week_end}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "┌── <b>ПОРТФЕЛЬ ЗА 7 ДНЕЙ</b> ───────────",
            f"│ 📋 <b>Всего сетапов:</b>  <code>{total}</code>",
            f"│ ✅ <b>Тейк-профит (TP):</b> <code>{tp_hits}</code>",
            f"│ ❌ <b>Стоп-лосс (SL):</b>   <code>{sl_hits}</code>",
            f"│ ⏰ <b>Истекло (24h):</b>    <code>{expired}</code>",
            f"│ 🔵 <b>В рынке:</b>          <code>{still_open}</code>",
            "└──────────────────────────────────────",
            "",
            f"🏆 <b>WIN RATE:</b> <code>{win_rate:.1f}%</code>",
            f"<code>[{wr_bar}]</code>",
            "",
            "┌── <b>ФИНАНСОВЫЙ РЕЗУЛЬТАТ</b> ─────────",
            f"│ 💰 <b>Общий PnL:</b>       <code>{pips_sign}{total_pips:.1f} pips</code>",
            f"│ 📈 <b>Прибыль (TP):</b>    <code>+{tp_pips:.1f} pips</code>",
            f"│ 📉 <b>Убыток (SL):</b>     <code>{sl_pips:.1f} pips</code>",
            f"│ 📐 <b>Средний R:R:</b>     <code>1:{avg_rr:.1f}</code>",
            "└──────────────────────────────────────",
        ]

        if best:
            d_emoji = "🟢" if best[1] == "LONG" else "🔴"
            lines.extend(["", f"🥇 <b>Лучший трейд:</b> <code>{best[0]}</code> {d_emoji} (<code>+{best[2]:.1f} pips</code>)"])

        if worst:
            d_emoji = "🟢" if worst[1] == "LONG" else "🔴"
            lines.extend([f"🥉 <b>Худший трейд:</b> <code>{worst[0]}</code> {d_emoji} (<code>{worst[2]:.1f} pips</code>)"])

        if pair_rows:
            lines.extend(["", "🏅 <b>ТОП ИНСТРУМЕНТОВ:</b>"])
            for sym, cnt, w, l in pair_rows:
                wr = (w / (w + l) * 100) if (w + l) > 0 else 0
                lines.append(f"│ <b>{sym:6}</b> ── <code>{cnt:2} сделок</code> ({w}✅ {l}❌) [<code>{wr:.0f}%</code>]")

        if dir_rows:
            lines.extend(["", "📊 <b>ПО НАПРАВЛЕНИЯМ:</b>"])
            for d, cnt, w in dir_rows:
                d_emoji = "🟢" if d == "LONG" else "🔴"
                wr = (w / cnt * 100) if cnt > 0 else 0
                lines.append(f"│ {d_emoji} <b>{d:5}</b> ── <code>{cnt:2} сделок</code> [<code>{wr:.0f}% win</code>]")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💼 <i>Wall Street Institutional Risk Engine</i>"
        ])

        return "\n".join(lines)
