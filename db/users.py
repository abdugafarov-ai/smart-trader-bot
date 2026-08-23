"""
Smart Trader Bot — Управление пользователями.
Система одобрения заявок: только админ может дать доступ.
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


async def init_users_table():
    """Создаёт таблицу пользователей."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                requested_at TEXT,
                approved_at TEXT
            )
        """)
        await db.commit()
    logger.info("Users table initialized.")


async def is_user_approved(telegram_id: int) -> bool:
    """Проверяет, одобрен ли пользователь."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            return row[0] == "approved"
    except Exception as e:
        logger.error("is_user_approved error: %s", e)
        return False


async def get_user_status(telegram_id: int) -> str | None:
    """Возвращает статус пользователя: 'pending', 'approved', 'rejected', или None."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None
    except Exception:
        return None


async def request_access(telegram_id: int, username: str, first_name: str) -> bool:
    """Подаёт заявку на доступ. Возвращает True если заявка новая."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()

            if row is not None:
                return False  # Уже есть заявка

            await db.execute(
                """INSERT INTO users (telegram_id, username, first_name, status, requested_at)
                   VALUES (?, ?, ?, 'pending', ?)""",
                (telegram_id, username or "", first_name or "",
                 datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("request_access error: %s", e)
        return False


async def approve_user(telegram_id: int) -> bool:
    """Одобряет пользователя."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                "UPDATE users SET status = 'approved', approved_at = ? WHERE telegram_id = ?",
                (datetime.now(timezone.utc).isoformat(), telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("approve_user error: %s", e)
        return False


async def reject_user(telegram_id: int) -> bool:
    """Отклоняет пользователя."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                "UPDATE users SET status = 'rejected' WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("reject_user error: %s", e)
        return False


async def get_pending_users() -> list[dict]:
    """Возвращает список пользователей с ожидающими заявками."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE status = 'pending' ORDER BY requested_at"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


async def get_approved_user_ids() -> list[int]:
    """Возвращает все одобренные telegram_id для рассылки."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM users WHERE status = 'approved'"
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


async def auto_approve_admin(admin_id: int):
    """Автоматически одобряет админа."""
    if admin_id <= 0:
        return
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM users WHERE telegram_id = ?",
                (admin_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.execute(
                    """INSERT INTO users (telegram_id, username, first_name, status, requested_at, approved_at)
                       VALUES (?, 'admin', 'Admin', 'approved', ?, ?)""",
                    (admin_id,
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat()),
                )
            else:
                await db.execute(
                    "UPDATE users SET status = 'approved' WHERE telegram_id = ?",
                    (admin_id,),
                )
            await db.commit()
    except Exception as e:
        logger.error("auto_approve_admin error: %s", e)
