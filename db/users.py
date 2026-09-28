"""
Smart Trader Bot — Управление пользователями и подписками (CRM).
Система одобрения заявок, тарифов, сроков действия и скрытой активности.
"""

import aiosqlite
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import config

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


async def init_users_table():
    """Создаёт таблицу пользователей и выполняет миграцию новых колонок."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                tariff TEXT DEFAULT 'PRO',
                expires_at TEXT,
                requested_at TEXT,
                approved_at TEXT,
                last_seen TEXT,
                activity_count INTEGER DEFAULT 0
            )
        """)
        await db.commit()

        # Миграция существующих таблиц (добавление новых колонок, если их ещё нет)
        new_cols = [
            ("tariff", "TEXT DEFAULT 'PRO'"),
            ("expires_at", "TEXT"),
            ("last_seen", "TEXT"),
            ("activity_count", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_def in new_cols:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                await db.commit()
            except Exception:
                # Колонка уже существует в таблице
                pass

    logger.info("Users table & schema verified.")


async def is_user_approved(telegram_id: int) -> bool:
    """Проверяет, одобрен ли пользователь и активна ли его подписка."""
    if config.ADMIN_ID and telegram_id == config.ADMIN_ID:
        return True

    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status, expires_at FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False

            status, expires_at = row[0], row[1]
            if status != "approved":
                return False

            # Проверка срока действия подписки
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if datetime.now(timezone.utc) > exp_dt:
                        return False
                except Exception:
                    pass

            return True
    except Exception as e:
        logger.error("is_user_approved error: %s", e)
        return False


async def get_user_status(telegram_id: int) -> str | None:
    """
    Возвращает актуальный статус пользователя:
    'approved', 'expired', 'revoked', 'pending', 'rejected', или None.
    """
    if config.ADMIN_ID and telegram_id == config.ADMIN_ID:
        return "approved"

    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status, expires_at FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            status, expires_at = row[0], row[1]
            if status == "approved" and expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if datetime.now(timezone.utc) > exp_dt:
                        return "expired"
                except Exception:
                    pass

            return status
    except Exception:
        return None


async def get_user(telegram_id: int) -> dict | None:
    """Возвращает полные данные пользователя в виде словаря."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("get_user error: %s", e)
        return None


async def get_all_users() -> list[dict]:
    """Возвращает всех пользователей для CRM панели."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users ORDER BY approved_at DESC, requested_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_all_users error: %s", e)
        return []


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
                return False  # Уже есть запись

            now_iso = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO users (telegram_id, username, first_name, status, requested_at, last_seen, activity_count)
                   VALUES (?, ?, ?, 'pending', ?, ?, 1)""",
                (telegram_id, username or "", first_name or "", now_iso, now_iso),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("request_access error: %s", e)
        return False


async def approve_user(telegram_id: int, days: int = 30, tariff: str = "PRO") -> bool:
    """Одобряет пользователя и активирует тариф на указанное количество дней (по умолчанию 30)."""
    try:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=days)).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE users 
                   SET status = 'approved', approved_at = ?, tariff = ?, expires_at = ?
                   WHERE telegram_id = ?""",
                (now.isoformat(), tariff, expires_at, telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("approve_user error: %s", e)
        return False


async def revoke_user(telegram_id: int) -> bool:
    """Мгновенно отключает пользователя (KICK / REVOKE)."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                "UPDATE users SET status = 'revoked' WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("revoke_user error: %s", e)
        return False


async def restore_user(telegram_id: int, days: int = 30) -> bool:
    """Восстанавливает доступ пользователю на указанный срок."""
    try:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=days)).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE users 
                   SET status = 'approved', expires_at = ? 
                   WHERE telegram_id = ?""",
                (expires_at, telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("restore_user error: %s", e)
        return False


async def extend_subscription(telegram_id: int, days: int = 30) -> tuple[bool, str]:
    """Продлевает подписку пользователя на N дней."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT expires_at FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            cur_exp = row[0] if row else None

            base_dt = now
            if cur_exp:
                try:
                    exp_dt = datetime.fromisoformat(cur_exp)
                    if exp_dt > now:
                        base_dt = exp_dt
                except Exception:
                    pass

            new_exp = (base_dt + timedelta(days=days)).isoformat()
            await db.execute(
                "UPDATE users SET status = 'approved', expires_at = ? WHERE telegram_id = ?",
                (new_exp, telegram_id),
            )
            await db.commit()
            return True, new_exp
    except Exception as e:
        logger.error("extend_subscription error: %s", e)
        return False, ""


async def delete_user(telegram_id: int) -> bool:
    """Удаляет пользователя из базы."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
            await db.commit()
            return True
    except Exception as e:
        logger.error("delete_user error: %s", e)
        return False


async def reject_user(telegram_id: int) -> bool:
    """Отклоняет заявку пользователя."""
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
    """Возвращает активные непросроченные telegram_id для рассылки сигналов."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT telegram_id, expires_at FROM users WHERE status = 'approved'"
            )
            rows = await cursor.fetchall()
            valid_ids = []
            for uid, exp in rows:
                if exp:
                    try:
                        exp_dt = datetime.fromisoformat(exp)
                        if exp_dt < now:
                            continue  # Просрочен
                    except Exception:
                        pass
                valid_ids.append(uid)
            return valid_ids
    except Exception as e:
        logger.error("get_approved_user_ids error: %s", e)
        return []


async def log_user_activity(telegram_id: int):
    """Скрытно фиксирует время последнего действия и счётчик активности."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE users 
                   SET last_seen = ?, activity_count = COALESCE(activity_count, 0) + 1 
                   WHERE telegram_id = ?""",
                (now_iso, telegram_id),
            )
            await db.commit()
    except Exception:
        # Не ломаем выполнение, если что-то пошло не так при записи аналитики
        pass


async def auto_approve_admin(admin_id: int):
    """Автоматически одобряет администратора с пожизненным доступом."""
    if admin_id <= 0:
        return
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT telegram_id FROM users WHERE telegram_id = ?",
                (admin_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.execute(
                    """INSERT INTO users (telegram_id, username, first_name, status, tariff, requested_at, approved_at, last_seen)
                       VALUES (?, 'admin', 'Admin', 'approved', 'OWNER', ?, ?, ?)""",
                    (admin_id, now_iso, now_iso, now_iso),
                )
            else:
                await db.execute(
                    """UPDATE users 
                       SET status = 'approved', tariff = 'OWNER', expires_at = NULL, last_seen = ? 
                       WHERE telegram_id = ?""",
                    (now_iso, admin_id),
                )
            await db.commit()
    except Exception as e:
        logger.error("auto_approve_admin error: %s", e)
