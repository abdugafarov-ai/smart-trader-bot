"""
Smart Trader Bot — Управление пользователями и подписками (CRM).
Система одобрения заявок, тарифов, триалов, сроков действия, авто-напоминаний и скрытой активности.
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
                activity_count INTEGER DEFAULT 0,
                is_lifetime INTEGER DEFAULT 0,
                reminder_3d_sent INTEGER DEFAULT 0,
                reminder_1d_sent INTEGER DEFAULT 0
            )
        """)
        await db.commit()

        # Миграция существующих таблиц (добавление новых колонок, если их ещё нет)
        new_cols = [
            ("tariff", "TEXT DEFAULT 'PRO'"),
            ("expires_at", "TEXT"),
            ("last_seen", "TEXT"),
            ("activity_count", "INTEGER DEFAULT 0"),
            ("is_lifetime", "INTEGER DEFAULT 0"),
            ("reminder_3d_sent", "INTEGER DEFAULT 0"),
            ("reminder_1d_sent", "INTEGER DEFAULT 0"),
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
                "SELECT status, expires_at, is_lifetime FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False

            status, expires_at, is_lifetime = row[0], row[1], row[2]
            if status != "approved":
                return False

            # Бессрочный доступ (VIP / Администратор / Друзья)
            if is_lifetime or expires_at is None:
                return True

            # Проверка срока действия подписки
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
                "SELECT status, expires_at, is_lifetime FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            status, expires_at, is_lifetime = row[0], row[1], row[2]
            if status == "approved":
                if is_lifetime or not expires_at:
                    return "approved"
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


async def get_all_users_filtered(filter_type: str = "all") -> list[dict]:
    """Возвращает пользователей с фильтрацией: all, active, revoked, pending."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if filter_type == "active":
                cursor = await db.execute(
                    "SELECT * FROM users WHERE status = 'approved' ORDER BY approved_at DESC"
                )
                rows = await cursor.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    if d.get("telegram_id") == config.ADMIN_ID:
                        continue
                    exp = d.get("expires_at")
                    is_life = d.get("is_lifetime")
                    if is_life or not exp:
                        result.append(d)
                    else:
                        try:
                            if datetime.fromisoformat(exp) >= now:
                                result.append(d)
                        except Exception:
                            result.append(d)
                return result

            elif filter_type == "revoked":
                cursor = await db.execute(
                    "SELECT * FROM users WHERE status IN ('revoked', 'expired') ORDER BY last_seen DESC"
                )
                rows = await cursor.fetchall()
                # Also include approved but expired
                cur_app = await db.execute("SELECT * FROM users WHERE status = 'approved'")
                for r in await cur_app.fetchall():
                    d = dict(r)
                    exp = d.get("expires_at")
                    is_life = d.get("is_lifetime")
                    if not is_life and exp:
                        try:
                            if datetime.fromisoformat(exp) < now and d not in rows:
                                rows.append(r)
                        except Exception:
                            pass
                return [dict(r) for r in rows if r["telegram_id"] != config.ADMIN_ID]

            elif filter_type == "pending":
                cursor = await db.execute(
                    "SELECT * FROM users WHERE status = 'pending' ORDER BY requested_at DESC"
                )
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

            else:
                cursor = await db.execute(
                    "SELECT * FROM users ORDER BY approved_at DESC, requested_at DESC"
                )
                rows = await cursor.fetchall()
                return [dict(r) for r in rows if r["telegram_id"] != config.ADMIN_ID]
    except Exception as e:
        logger.error("get_all_users_filtered error: %s", e)
        return []


async def request_access(telegram_id: int, username: str, first_name: str, tariff: str = "PRO") -> bool:
    """Подаёт заявку на доступ. Возвращает True если заявка новая."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT status FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()

            if row is not None:
                # Обновляем имя и username
                await db.execute(
                    "UPDATE users SET username = ?, first_name = ?, tariff = ? WHERE telegram_id = ?",
                    (username or "", first_name or "", tariff, telegram_id)
                )
                await db.commit()
                return False

            now_iso = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO users (telegram_id, username, first_name, status, tariff, requested_at, last_seen, activity_count)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, 1)""",
                (telegram_id, username or "", first_name or "", tariff, now_iso, now_iso),
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
                   SET status = 'approved', approved_at = ?, tariff = ?, expires_at = ?,
                       is_lifetime = 0, reminder_3d_sent = 0, reminder_1d_sent = 0
                   WHERE telegram_id = ?""",
                (now.isoformat(), tariff, expires_at, telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("approve_user error: %s", e)
        return False


async def activate_trial(telegram_id: int, days: int = 3) -> bool:
    """Активирует бесплатный тест-драйв на указанное количество дней (по умолчанию 3)."""
    try:
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=days)).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE users 
                   SET status = 'approved', approved_at = ?, tariff = 'TRIAL (3 дня)', expires_at = ?,
                       is_lifetime = 0, reminder_3d_sent = 0, reminder_1d_sent = 0
                   WHERE telegram_id = ?""",
                (now.isoformat(), expires_at, telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("activate_trial error: %s", e)
        return False


async def set_lifetime_subscription(telegram_id: int) -> bool:
    """Активирует бессрочный доступ (VIP / Для братьев и друзей администратора)."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                """UPDATE users 
                   SET status = 'approved', approved_at = ?, tariff = 'VIP BECCPOЧНЫЙ', expires_at = NULL,
                       is_lifetime = 1, reminder_3d_sent = 0, reminder_1d_sent = 0
                   WHERE telegram_id = ?""",
                (now.isoformat(), telegram_id),
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("set_lifetime_subscription error: %s", e)
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
                   SET status = 'approved', expires_at = ?, is_lifetime = 0,
                       reminder_3d_sent = 0, reminder_1d_sent = 0
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
                """UPDATE users 
                   SET status = 'approved', expires_at = ?, is_lifetime = 0,
                       reminder_3d_sent = 0, reminder_1d_sent = 0 
                   WHERE telegram_id = ?""",
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
                "SELECT telegram_id, expires_at, is_lifetime FROM users WHERE status = 'approved'"
            )
            rows = await cursor.fetchall()
            valid_ids = []
            for uid, exp, is_life in rows:
                if is_life or not exp:
                    valid_ids.append(uid)
                    continue
                try:
                    exp_dt = datetime.fromisoformat(exp)
                    if exp_dt >= now:
                        valid_ids.append(uid)
                except Exception:
                    pass
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
        pass


async def get_reminder_candidates() -> list[dict]:
    """Возвращает пользователей, которым пора отправить напоминание о завершении подписки (за 3 дня или 1 день)."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM users 
                   WHERE status = 'approved' AND (is_lifetime IS NULL OR is_lifetime = 0) 
                     AND expires_at IS NOT NULL"""
            )
            rows = await cursor.fetchall()
            candidates = []
            for r in rows:
                d = dict(r)
                if d.get("telegram_id") == config.ADMIN_ID:
                    continue
                try:
                    exp_dt = datetime.fromisoformat(d["expires_at"])
                    remaining_hours = (exp_dt - now).total_seconds() / 3600.0
                    days_left = remaining_hours / 24.0

                    if 24.0 < remaining_hours <= 72.0 and not d.get("reminder_3d_sent"):
                        d["reminder_type"] = "3d"
                        d["days_left"] = max(1, int(days_left))
                        candidates.append(d)
                    elif 0.0 < remaining_hours <= 24.0 and not d.get("reminder_1d_sent"):
                        d["reminder_type"] = "1d"
                        d["days_left"] = 1
                        candidates.append(d)
                except Exception:
                    pass
            return candidates
    except Exception as e:
        logger.error("get_reminder_candidates error: %s", e)
        return []


async def mark_reminder_sent(telegram_id: int, reminder_type: str):
    """Отмечает, что напоминание пользователю отправлено."""
    try:
        col = "reminder_3d_sent" if reminder_type == "3d" else "reminder_1d_sent"
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(f"UPDATE users SET {col} = 1 WHERE telegram_id = ?", (telegram_id,))
            await db.commit()
    except Exception as e:
        logger.error("mark_reminder_sent error: %s", e)


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
                    """INSERT INTO users (telegram_id, username, first_name, status, tariff, requested_at, approved_at, last_seen, is_lifetime)
                       VALUES (?, 'admin', 'Admin', 'approved', 'OWNER', ?, ?, ?, 1)""",
                    (admin_id, now_iso, now_iso, now_iso),
                )
            else:
                await db.execute(
                    """UPDATE users 
                       SET status = 'approved', tariff = 'OWNER', expires_at = NULL, is_lifetime = 1, last_seen = ? 
                       WHERE telegram_id = ?""",
                    (now_iso, admin_id),
                )
            await db.commit()
    except Exception as e:
        logger.error("auto_approve_admin error: %s", e)
