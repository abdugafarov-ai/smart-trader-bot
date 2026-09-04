"""
Smart Trader Bot — SQLite Database Manager.
Хранит сигналы с уникальными эмодзи-маркерами, отслеживает активацию, TP/SL и ведет историю.
"""

import aiosqlite
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


async def init_db():
    """Создаёт базу данных, таблицы и выполняет миграции если нужно."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                order_type TEXT DEFAULT 'BUY_LIMIT',
                tag_emoji TEXT DEFAULT '🔥',
                stars INTEGER NOT NULL DEFAULT 0,
                current_price REAL,
                entry_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                risk_reward REAL,
                strategies_agreed TEXT DEFAULT '',
                timeframes_agreed TEXT DEFAULT '',
                status TEXT DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                activated_at TEXT,
                closed_at TEXT,
                close_price REAL,
                pnl_pips REAL DEFAULT 0.0,
                result TEXT DEFAULT ''
            )
        """)

        # Миграция колонок на случай старой структуры БД
        cursor = await db.execute("PRAGMA table_info(signals)")
        columns = [row[1] for row in await cursor.fetchall()]
        
        if "order_type" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN order_type TEXT DEFAULT 'BUY_LIMIT'")
        if "tag_emoji" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN tag_emoji TEXT DEFAULT '🔥'")
        if "current_price" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN current_price REAL")
        if "activated_at" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN activated_at TEXT")
        if "breakeven_applied" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN breakeven_applied INTEGER DEFAULT 0")

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

        # Автоматическая очистка старых зависших PENDING ордеров старше 24ч при старте
        from datetime import timedelta
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        await db.execute("""
            UPDATE signals
            SET status = 'EXPIRED', closed_at = ?, result = 'Истек срок ожидания (авто-очистка)'
            WHERE status = 'PENDING' AND created_at < ?
        """, (datetime.now(timezone.utc).isoformat(), cutoff_iso))

        await db.commit()
    logger.info("Database initialized with full schema and stale pending orders cleaned up at %s", DB_PATH)


async def save_signal(
    symbol: str,
    direction: str,
    order_type: str,
    tag_emoji: str,
    stars: int,
    current_price: float | None,
    entry_price: float | None,
    stop_loss: float | None,
    take_profit_1: float | None,
    take_profit_2: float | None,
    risk_reward: float | None,
    strategies_agreed: str = "",
    timeframes_agreed: str = "",
) -> int | None:
    """Сохраняет новый сигнал в БД (ACTIVE для MARKET ордеров, PENDING для LIMIT). Возвращает ID."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        initial_status = "ACTIVE" if "MARKET" in (order_type or "") else "PENDING"
        activated_at = now_iso if initial_status == "ACTIVE" else None

        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """INSERT INTO signals
                   (symbol, direction, order_type, tag_emoji, stars,
                    current_price, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed, status, created_at, activated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, direction, order_type, tag_emoji, stars,
                    current_price, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed,
                    initial_status, now_iso, activated_at
                ),
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error("save_signal error: %s", e)
        return None


async def activate_signal(signal_id: int):
    """Переводит сигнал из статуса PENDING в ACTIVE (цена коснулась входа)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE signals
                   SET status = 'ACTIVE', activated_at = ?
                   WHERE id = ? AND status = 'PENDING'""",
                (datetime.now(timezone.utc).isoformat(), signal_id),
            )
            await db.commit()
    except Exception as e:
        logger.error("activate_signal error: %s", e)


async def update_signal_status(
    signal_id: int,
    status: str,
    close_price: float | None = None,
    pnl_pips: float = 0.0,
    result: str = "",
):
    """Обновляет статус сигнала (TP1_HIT, TP2_HIT, SL_HIT, EXPIRED, CANCELLED)."""
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


async def update_signal_sl(signal_id: int, new_sl: float, breakeven: bool = False):
    """Переносит стоп-лосс на новый уровень (breakeven / trailing)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            if breakeven:
                await db.execute(
                    """UPDATE signals SET stop_loss = ?, breakeven_applied = 1 WHERE id = ?""",
                    (new_sl, signal_id),
                )
            else:
                await db.execute(
                    """UPDATE signals SET stop_loss = ? WHERE id = ?""",
                    (new_sl, signal_id),
                )
            await db.commit()
    except Exception as e:
        logger.error("update_signal_sl error: %s", e)


async def has_open_signal_for_pair(symbol: str) -> bool:
    """Проверяет, есть ли уже активный или ожидающий сигнал по этой паре (анти-спам)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) FROM signals
                   WHERE symbol = ? AND status IN ('PENDING', 'ACTIVE', 'OPEN', 'TP1_PARTIAL')""",
                (symbol,),
            )
            count = (await cursor.fetchone())[0]
            return count > 0
    except Exception as e:
        logger.error("has_open_signal_for_pair error: %s", e)
        return False


async def get_pending_signals() -> list[dict]:
    """Возвращает все отложенные сигналы (ожидающие касания цены входа)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM signals WHERE status = 'PENDING' ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_pending_signals error: %s", e)
        return []


async def get_active_signals() -> list[dict]:
    """Возвращает все сигналы в рынке (активированные, ожидающие TP/SL)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM signals WHERE status IN ('ACTIVE', 'OPEN', 'TP1_PARTIAL') ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_active_signals error: %s", e)
        return []


async def get_recent_signals(limit: int = 20) -> list[dict]:
    """Возвращает последние N сигналов для истории."""
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
            cursor = await db.execute("SELECT COUNT(*) FROM signals")
            total = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status IN ('PENDING', 'ACTIVE', 'OPEN', 'TP1_PARTIAL')"
            )
            open_count = (await cursor.fetchone())[0]

            closed = total - open_count

            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT', 'TP1_PARTIAL')"
            )
            wins = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'SL_HIT'"
            )
            losses = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'EXPIRED'"
            )
            expired = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COALESCE(SUM(pnl_pips), 0) FROM signals WHERE status NOT IN ('PENDING', 'ACTIVE', 'OPEN')"
            )
            total_pips = (await cursor.fetchone())[0]

            win_rate = (wins / closed * 100) if closed > 0 else 0.0

            cursor = await db.execute(
                "SELECT AVG(risk_reward) FROM signals WHERE risk_reward IS NOT NULL AND risk_reward > 0"
            )
            avg_rr = (await cursor.fetchone())[0] or 0.0

            cursor = await db.execute(
                "SELECT direction, COUNT(*) as cnt, "
                "SUM(CASE WHEN status IN ('TP1_HIT','TP2_HIT') THEN 1 ELSE 0 END) as w "
                "FROM signals GROUP BY direction"
            )
            by_direction = {}
            for row in await cursor.fetchall():
                d, cnt, w = row
                by_direction[d] = {"total": cnt, "wins": w}

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


async def check_signal_exists(symbol: str, direction: str, hours: int = 6) -> bool:
    """Проверяет наличие активного сигнала по паре."""
    return await has_open_signal_for_pair(symbol)


async def get_consecutive_sl_count() -> int:
    """Считает количество последовательных Stop Loss среди последних закрытых сигналов."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """SELECT status FROM signals 
                   WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT') 
                   ORDER BY closed_at DESC LIMIT 10"""
            )
            rows = await cursor.fetchall()
            sl_count = 0
            for (status,) in rows:
                if status == 'SL_HIT':
                    sl_count += 1
                else:
                    break
            return sl_count
    except Exception as e:
        logger.error("get_consecutive_sl_count error: %s", e)
        return 0


async def get_today_signal_count() -> int:
    """Считает количество сигналов, созданных сегодня (по UTC). Персистентный счётчик, переживает перезагрузки."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE created_at LIKE ?",
                (f"{today}%",),
            )
            count = (await cursor.fetchone())[0]
            return count
    except Exception as e:
        logger.error("get_today_signal_count error: %s", e)
        return 0


async def get_last_signal_time_for_pair(symbol: str) -> Optional[datetime]:
    """Возвращает время последнего сигнала по данной паре (для cooldown). Персистентный."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT created_at FROM signals WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol,),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                dt = datetime.fromisoformat(row[0])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            return None
    except Exception as e:
        logger.error("get_last_signal_time_for_pair error: %s", e)
        return None

