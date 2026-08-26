"""
Smart Trader Bot — Еженедельный отчёт.
Отправляет в субботу сводку за неделю.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot
from pathlib import Path

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
                # Проверяем: суббота и нужный час
                if now.weekday() == self.report_day and now.hour == self.report_hour:
                    await self._generate_and_send()
                    # Ждём 2 часа чтобы не отправить дважды
                    await asyncio.sleep(7200)
                else:
                    # Проверяем каждые 30 минут
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

        # Отправляем всем одобренным пользователям
        user_ids = await get_approved_user_ids()
        # Также отправляем админу
        if config.ADMIN_ID and config.ADMIN_ID not in user_ids:
            user_ids.append(config.ADMIN_ID)

        for uid in user_ids:
            try:
                await self.bot.send_message(uid, report)
            except Exception as e:
                logger.error("Failed to send weekly report to %d: %s", uid, e)

        logger.info("Weekly report sent to %d users.", len(user_ids))

    async def _build_report(self) -> str:
        """Собирает статистику за 7 дней."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        try:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                # Всего сигналов за неделю
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ?",
                    (week_ago,),
                )
                total = (await cursor.fetchone())[0]

                if total == 0:
                    return (
                        "📊 ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ\n"
                        f"{'━' * 28}\n\n"
                        "На этой неделе сигналов не было.\n"
                        f"Бот продолжает мониторинг {len(config.ALL_PAIRS)} пар Forex и Золота.\n\n"
                        "💡 Качество > Количество!"
                    )

                # TP попадания
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT')",
                    (week_ago,),
                )
                tp_hits = (await cursor.fetchone())[0]

                # SL попадания
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status = 'SL_HIT'",
                    (week_ago,),
                )
                sl_hits = (await cursor.fetchone())[0]

                # Истёкшие
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status = 'EXPIRED'",
                    (week_ago,),
                )
                expired = (await cursor.fetchone())[0]

                # Ещё открытые
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM signals WHERE created_at >= ? AND status = 'OPEN'",
                    (week_ago,),
                )
                still_open = (await cursor.fetchone())[0]

                # Общие пипсы
                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status != 'OPEN'",
                    (week_ago,),
                )
                total_pips = (await cursor.fetchone())[0]

                # Пипсы TP
                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT')",
                    (week_ago,),
                )
                tp_pips = (await cursor.fetchone())[0]

                # Пипсы SL
                cursor = await db.execute(
                    "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE created_at >= ? AND status = 'SL_HIT'",
                    (week_ago,),
                )
                sl_pips = (await cursor.fetchone())[0]

                # Win rate
                closed = tp_hits + sl_hits + expired
                win_rate = (tp_hits / closed * 100) if closed > 0 else 0.0

                # Лучший сигнал
                cursor = await db.execute(
                    "SELECT symbol, direction, pnl_pips FROM signals "
                    "WHERE created_at >= ? AND status IN ('TP1_HIT', 'TP2_HIT') "
                    "ORDER BY pnl_pips DESC LIMIT 1",
                    (week_ago,),
                )
                best = await cursor.fetchone()

                # Худший сигнал
                cursor = await db.execute(
                    "SELECT symbol, direction, pnl_pips FROM signals "
                    "WHERE created_at >= ? AND status = 'SL_HIT' "
                    "ORDER BY pnl_pips ASC LIMIT 1",
                    (week_ago,),
                )
                worst = await cursor.fetchone()

                # По парам
                cursor = await db.execute(
                    "SELECT symbol, COUNT(*) as cnt, "
                    "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) as w, "
                    "SUM(CASE WHEN status = 'SL_HIT' THEN 1 ELSE 0 END) as l "
                    "FROM signals WHERE created_at >= ? "
                    "GROUP BY symbol ORDER BY cnt DESC LIMIT 5",
                    (week_ago,),
                )
                pair_rows = await cursor.fetchall()

                # По направлениям
                cursor = await db.execute(
                    "SELECT direction, COUNT(*), "
                    "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) "
                    "FROM signals WHERE created_at >= ? GROUP BY direction",
                    (week_ago,),
                )
                dir_rows = await cursor.fetchall()

                # Средний R:R
                cursor = await db.execute(
                    "SELECT AVG(risk_reward) FROM signals "
                    "WHERE created_at >= ? AND risk_reward > 0",
                    (week_ago,),
                )
                avg_rr = (await cursor.fetchone())[0] or 0.0

        except Exception as e:
            logger.error("Weekly report query error: %s", e)
            return ""

        # Форматируем отчёт
        now = datetime.now(self.tz)
        week_start = (now - timedelta(days=7)).strftime("%d.%m")
        week_end = now.strftime("%d.%m.%Y")

        pips_emoji = "📈" if total_pips >= 0 else "📉"
        pips_sign = "+" if total_pips >= 0 else ""
        wr_emoji = "🏆" if win_rate >= 60 else ("📊" if win_rate >= 40 else "⚠️")

        lines = [
            f"📊 ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ",
            f"📅 {week_start} — {week_end}",
            f"{'━' * 28}",
            "",
            f"📋 Всего сигналов: {total}",
            f"   ✅ Тейк-профит (TP): {tp_hits}",
            f"   ❌ Стоп-лосс (SL): {sl_hits}",
            f"   ⏰ Истекли: {expired}",
            f"   🔵 Ещё открыты: {still_open}",
            "",
            f"{wr_emoji} Win Rate: {win_rate:.1f}%",
            f"{'━' * 28}",
            "",
            f"{pips_emoji} Итого пипсов: {pips_sign}{total_pips:.1f}",
            f"   ✅ Прибыль (TP): +{tp_pips:.1f} пипсов",
            f"   ❌ Убыток (SL): {sl_pips:.1f} пипсов",
            f"   📐 Средний R:R: 1:{avg_rr:.1f}",
        ]

        if best:
            d_emoji = "🟢" if best[1] == "LONG" else "🔴"
            lines.extend(["", f"🥇 Лучший сигнал: {best[0]} {d_emoji} {best[1]} (+{best[2]:.1f} пипсов)"])

        if worst:
            d_emoji = "🟢" if worst[1] == "LONG" else "🔴"
            lines.extend([f"🥉 Худший сигнал: {worst[0]} {d_emoji} {worst[1]} ({worst[2]:.1f} пипсов)"])

        if pair_rows:
            lines.extend(["", f"{'━' * 28}", "🏅 Топ пары:"])
            for sym, cnt, w, l in pair_rows:
                wr = (w / (w + l) * 100) if (w + l) > 0 else 0
                lines.append(f"   {sym}: {cnt} сиг. ({w}✅ {l}❌) — {wr:.0f}%")

        if dir_rows:
            lines.extend(["", "📊 По направлениям:"])
            for d, cnt, w in dir_rows:
                d_emoji = "🟢" if d == "LONG" else "🔴"
                wr = (w / cnt * 100) if cnt > 0 else 0
                lines.append(f"   {d_emoji} {d}: {cnt} сигн. — {wr:.0f}% win")

        lines.extend([
            "",
            f"{'━' * 28}",
            "💡 Торгуй с умом. Качество > Количество.",
            "📊 Риск не более 1-2% депозита на сделку.",
        ])

        return "\n".join(lines)
