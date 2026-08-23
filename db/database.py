"""
Smart Trader Bot — SQLite Database Manager.
Хранит сигналы, отслеживает win-rate, ведёт историю.
"""

import aiosqlite
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


async def init_db():
    """Создаёт базу данных и таблицы если не существуют."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                stars INTEGER NOT NULL DEFAULT 0,
                entry_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                risk_reward REAL,
                strategies_agreed TEXT DEFAULT '',
                timeframes_agreed TEXT DEFAULT '',
                status TEXT DEFAULT 'OPEN',
                created_at TEXT NOT NULL,
                closed_at TEXT,
                close_price REAL,
                pnl_pips REAL DEFAULT 0.0,
                result TEXT DEFAULT ''
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_signals INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                expired INTEGER DEFAULT 0,
                total_pips REAL DEFAULT 0.0
            )
        """)

        await db.commit()
    logger.info("Database initialized at %s", DB_PATH)


async def save_signal(
    symbol: str,
    direction: str,
    stars: int,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit_1: float | None,
    take_profit_2: float | None,
    risk_reward: float | None,
    strategies_agreed: str = "",
    timeframes_agreed: str = "",
) -> int | None:
    """Сохраняет новый сигнал в БД. Возвращает ID."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """INSERT INTO signals
                   (symbol, direction, stars, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)""",
                (
                    symbol, direction, stars, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error("save_signal error: %s", e)
        return None


async def update_signal_status(
    signal_id: int,
    status: str,
    close_price: float | None = None,
    pnl_pips: float = 0.0,
    result: str = "",
):
    """Обновляет статус сигнала (TP1_HIT, SL_HIT, EXPIRED)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE signals
                   SET status = ?, closed_at = ?, close_price = ?,
                       pnl_pips = ?, result = ?
                   WHERE id = ?""",
                (
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    close_price,
                    pnl_pips,
                    result,
                    signal_id,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.error("update_signal_status error: %s", e)


async def get_open_signals() -> list[dict]:
    """Возвращает все открытые сигналы."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_open_signals error: %s", e)
        return []


async def get_recent_signals(limit: int = 20) -> list[dict]:
    """Возвращает последние N сигналов."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_recent_signals error: %s", e)
        return []


async def get_stats() -> dict:
    """Возвращает общую статистику по сигналам."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            # Общее количество
            cursor = await db.execute("SELECT COUNT(*) FROM signals")
            total = (await cursor.fetchone())[0]

            # Открытые
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'OPEN'"
            )
            open_count = (await cursor.fetchone())[0]

            # Закрытые
            closed = total - open_count

            # Победы (TP1 или TP2)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT')"
            )
            wins = (await cursor.fetchone())[0]

            # Проигрыши (SL)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'SL_HIT'"
            )
            losses = (await cursor.fetchone())[0]

            # Истёкшие
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'EXPIRED'"
            )
            expired = (await cursor.fetchone())[0]

            # Общие пипсы
            cursor = await db.execute(
                "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE status != 'OPEN'"
            )
            total_pips = (await cursor.fetchone())[0]

            # Win rate
            win_rate = (wins / closed * 100) if closed > 0 else 0.0

            # Средний R:R
            cursor = await db.execute(
                "SELECT AVG(risk_reward) FROM signals WHERE risk_reward IS NOT NULL AND risk_reward > 0"
            )
            avg_rr = (await cursor.fetchone())[0] or 0.0

            # По направлениям
            cursor = await db.execute(
                "SELECT direction, COUNT(*) as cnt, "
                "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) as w "
                "FROM signals GROUP BY direction"
            )
            by_direction = {}
            for row in await cursor.fetchall():
                d, cnt, w = row
                by_direction[d] = {"total": cnt, "wins": w}

            # По парам (топ-5 по кол-ву)
            cursor = await db.execute(
                "SELECT symbol, COUNT(*) as cnt, "
                "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) as w "
                "FROM signals GROUP BY symbol ORDER BY cnt DESC LIMIT 5"
            )
            by_symbol = {}
            for row in await cursor.fetchall():
                s, cnt, w = row
                by_symbol[s] = {"total": cnt, "wins": w}

            return {
                "total": total,
                "open": open_count,
                "closed": closed,
                "wins": wins,
                "losses": losses,
                "expired": expired,
                "win_rate": win_rate,
                "total_pips": total_pips,
                "avg_rr": avg_rr,
                "by_direction": by_direction,
                "by_symbol": by_symbol,
            }
    except Exception as e:
        logger.error("get_stats error: %s", e)
        return {
            "total": 0, "open": 0, "closed": 0,
            "wins": 0, "losses": 0, "expired": 0,
            "win_rate": 0.0, "total_pips": 0.0, "avg_rr": 0.0,
            "by_direction": {}, "by_symbol": {},
        }


async def check_signal_exists(symbol: str, direction: str, hours: int = 4) -> bool:
    """Проверяет, есть ли уже похожий открытый сигнал за последние N часов."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) FROM signals
                   WHERE symbol = ? AND direction = ? AND status = 'OPEN'
                   AND datetime(created_at) > datetime('now', ?)""",
                (symbol, direction, f"-{hours} hours"),
            )
            count = (await cursor.fetchone())[0]
            return count > 0
    except Exception as e:
        logger.error("check_signal_exists error: %s", e)
        return False
