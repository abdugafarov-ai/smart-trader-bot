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
        """Собирает честную статистику брокера MT5 за 7 дней."""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        try:
            from db.database import get_stats
            stats = await get_stats()

            async with aiosqlite.connect(str(DB_PATH)) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM broker_deals WHERE created_at >= ?",
                    (week_ago,),
                )
                total = (await cursor.fetchone())[0]

                # Если за 7 дней не было новых сделок, показываем общую статистику робота
                all_time = False
                if total == 0:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals")
                    total = (await cursor.fetchone())[0]
                    all_time = True

                if total == 0:
                    return (
                        "📊 <b>METATRADER 5 | ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "<i>Сделок в журнале не зафиксировано.\n"
                        f"Советник подключен к MetaTrader 5 и ожидает исполнения сетапов.</i>\n\n"
                        "💼 <i>Дисциплина и институциональный риск-менеджмент.</i>"
                    )

                time_filter = "" if all_time else "WHERE created_at >= ?"
                params = () if all_time else (week_ago,)

                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM broker_deals {time_filter} AND profit_usd > 0" if not all_time else
                    "SELECT COUNT(*) FROM broker_deals WHERE profit_usd > 0",
                    params
                )
                tp_hits = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM broker_deals {time_filter} AND profit_usd < 0" if not all_time else
                    "SELECT COUNT(*) FROM broker_deals WHERE profit_usd < 0",
                    params
                )
                sl_hits = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    f"SELECT COUNT(*) FROM broker_deals {time_filter} AND profit_usd == 0" if not all_time else
                    "SELECT COUNT(*) FROM broker_deals WHERE profit_usd == 0",
                    params
                )
                breakevens = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {time_filter}",
                    params
                )
                total_profit_usd = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {time_filter} AND profit_usd > 0" if not all_time else
                    "SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd > 0",
                    params
                )
                gross_profit = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {time_filter} AND profit_usd < 0" if not all_time else
                    "SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd < 0",
                    params
                )
                gross_loss = abs((await cursor.fetchone())[0])

                profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)
                avg_win = round(gross_profit / tp_hits, 2) if tp_hits > 0 else 0.0
                avg_loss = round(gross_loss / sl_hits, 2) if sl_hits > 0 else 0.0
                win_pct = tp_hits / total if total > 0 else 0.0
                loss_pct = sl_hits / total if total > 0 else 0.0
                expectancy = round((win_pct * avg_win) - (loss_pct * avg_loss), 2)

                # Win Rate рассчитывается аналогично database.py (доля прибыльных и безубыточных сделок)
                win_rate = ((total - sl_hits) / total * 100) if total > 0 else 0.0

                # Лучшая сделка
                cursor = await db.execute(
                    f"SELECT symbol, deal_type, profit_usd, ticket FROM broker_deals {time_filter} ORDER BY profit_usd DESC LIMIT 1",
                    params
                )
                best = await cursor.fetchone()

                # Худшая сделка
                cursor = await db.execute(
                    f"SELECT symbol, deal_type, profit_usd, ticket FROM broker_deals {time_filter} ORDER BY profit_usd ASC LIMIT 1",
                    params
                )
                worst = await cursor.fetchone()

                # По инструментам
                cursor = await db.execute(
                    f"""SELECT symbol, COUNT(*) as cnt,
                               SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                               SUM(profit_usd) as pnl
                        FROM broker_deals {time_filter}
                        GROUP BY symbol ORDER BY pnl DESC LIMIT 5""",
                    params
                )
                pair_rows = await cursor.fetchall()

        except Exception as e:
            logger.error("Weekly report query error: %s", e)
            return ""

        now = datetime.now(self.tz)
        week_start = (now - timedelta(days=7)).strftime("%d.%m")
        week_end = now.strftime("%d.%m.%Y")
        period_title = f"{week_start} — {week_end}" if not all_time else "ВСЁ ВРЕМЯ (MT5)"

        profit_sign = "+" if total_profit_usd >= 0 else ""
        wr_bar_filled = int(win_rate // 10)
        wr_bar = "■" * wr_bar_filled + "□" * (10 - wr_bar_filled)

        lines = [
            "📊 <b>METATRADER 5 | БРОКЕРСКИЙ ОТЧЁТ</b>",
            f"📅 <code>{period_title}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "┌── <b>ПОРТФЕЛЬ РОБОТА</b> ───────────────────",
            f"│ 📋 <b>Всего сделок:</b>       <code>{total}</code>",
            f"│ ✅ <b>Тейк-профит (TP):</b>   <code>{tp_hits}</code>",
            f"│ ❌ <b>Стоп-лосс (SL):</b>     <code>{sl_hits}</code>",
            f"│ 🛡 <b>Безубыток (BE):</b>     <code>{breakevens}</code>",
            f"│ 🔵 <b>В рынке:</b>            <code>{stats.get('open', 0)}</code>",
            "└──────────────────────────────────────",
            "",
            f"🏆 <b>WIN RATE:</b> <code>{win_rate:.1f}%</code>",
            f"<code>[{wr_bar}]</code>",
            "",
            "┌── <b>ФИНАНСОВЫЙ РЕЗУЛЬТАТ (USD)</b> ────",
            f"│ 💵 <b>Чистый PnL:</b>       <b>{profit_sign}{total_profit_usd:.2f} USD</b>",
            f"│ 💼 <b>Баланс:</b>           <code>${stats.get('balance', 0.0):.2f}</code>",
            f"│ 📐 <b>Средний R:R:</b>       <code>1:{stats.get('avg_rr', 2.1):.1f}</code>",
            f"│ 📊 <b>Profit Factor:</b>     <code>{profit_factor:.2f}</code>",
            f"│ 🎯 <b>Expectancy:</b>        <b>{'+' if expectancy >= 0 else ''}{expectancy:.2f} USD</b>/сделка",
            "└──────────────────────────────────────",
        ]

        if best and best[2] > 0:
            d_emoji = "🟢" if "BUY" in best[1] else "🔴"
            lines.extend(["", f"🥇 <b>Лучший трейд:</b> #{best[3]} <code>{best[0]}</code> {d_emoji} (<b>+{best[2]:.2f} USD</b>)"])

        if worst and worst[2] < 0:
            d_emoji = "🟢" if "BUY" in worst[1] else "🔴"
            lines.extend([f"🛑 <b>Макс. просадка трейда:</b> #{worst[3]} <code>{worst[0]}</code> {d_emoji} (<b>{worst[2]:.2f} USD</b>)"])

        if pair_rows:
            lines.extend(["", "🏅 <b>ПРИБЫЛЬ ПО ИНСТРУМЕНТАМ:</b>"])
            for sym, cnt, w, pnl in pair_rows:
                p_sign = "+" if pnl >= 0 else ""
                lines.append(f"│ <b>{sym:6}</b> ── <code>{cnt:2} сделок</code> | <b>{p_sign}{pnl:.2f} USD</b>")

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💼 <i>Данные поступают напрямую из брокерского терминала MT5.</i>"
        ])

        return "\n".join(lines)
