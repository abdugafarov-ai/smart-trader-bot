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
            ("admin_notes", "TEXT DEFAULT ''"),
        ]
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crm_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                amount_usd REAL NOT NULL,
                days_added INTEGER NOT NULL,
                tariff TEXT NOT NULL,
                payment_method TEXT DEFAULT 'CRYPTO_USDT',
                comment TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                created_by INTEGER DEFAULT 0
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crm_payments_user ON crm_payments (telegram_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crm_payments_date ON crm_payments (created_at);")
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
                            exp_dt = datetime.fromisoformat(exp)
                            if exp_dt.tzinfo is None:
                                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                            if exp_dt >= now:
                                result.append(d)
                        except Exception:
                            result.append(d)
                return result

            elif filter_type == "revoked":
                cursor = await db.execute(
                    "SELECT * FROM users WHERE status IN ('revoked', 'expired') ORDER BY last_seen DESC"
                )
                rows = list(await cursor.fetchall())
                seen_ids = {r["telegram_id"] for r in rows}
                # Also include approved but expired
                cur_app = await db.execute("SELECT * FROM users WHERE status = 'approved'")
                for r in await cur_app.fetchall():
                    d = dict(r)
                    exp = d.get("expires_at")
                    is_life = d.get("is_lifetime")
                    if not is_life and exp and d["telegram_id"] not in seen_ids:
                        try:
                            exp_dt = datetime.fromisoformat(exp)
                            if exp_dt.tzinfo is None:
                                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                            if exp_dt < now:
                                rows.append(r)
                                seen_ids.add(d["telegram_id"])
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


async def extend_subscription(telegram_id: int, days: int = 30, amount_usd: float = 0.0, tariff_name: str = "") -> tuple[bool, str]:
    """Продлевает подписку пользователя на N дней и фиксирует оплату в CRM."""
    try:
        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT expires_at, tariff FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
            cur_exp = row[0] if row else None
            cur_tariff = row[1] if row else "PRO"

            base_dt = now
            if cur_exp:
                try:
                    exp_dt = datetime.fromisoformat(cur_exp)
                    if exp_dt > now:
                        base_dt = exp_dt
                except Exception:
                    pass

            new_exp = (base_dt + timedelta(days=days)).isoformat()
            
            # Автоматическое сопоставление тарифа и суммы, если не переданы
            if not tariff_name:
                if days == 3:
                    tariff_name = "🎁 Бесплатный Тест-драйв (3 дня)"
                elif days == 30:
                    tariff_name = "💎 Тариф 1 Месяц ($50)"
                    if amount_usd == 0.0: amount_usd = 50.0
                elif days == 90:
                    tariff_name = "🚀 Тариф 3 Месяца ($140)"
                    if amount_usd == 0.0: amount_usd = 140.0
                elif days == 365:
                    tariff_name = "👑 Тариф 1 Год ($500)"
                    if amount_usd == 0.0: amount_usd = 500.0
                else:
                    tariff_name = f"Индивидуальный ({days} дн.)"

            await db.execute(
                """UPDATE users 
                   SET status = 'approved', expires_at = ?, tariff = ?, is_lifetime = 0,
                       reminder_3d_sent = 0, reminder_1d_sent = 0 
                   WHERE telegram_id = ?""",
                (new_exp, tariff_name, telegram_id),
            )
            await db.commit()

        # Фиксируем финансовую транзакцию в CRM payments при наличии суммы
        if amount_usd > 0.0:
            await record_payment(
                telegram_id=telegram_id,
                amount_usd=amount_usd,
                days_added=days,
                tariff=tariff_name,
                payment_method="CRYPTO_USDT",
                comment=f"Продление на {days} дн. через CRM",
                created_by=config.ADMIN_ID
            )

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


# ═══════════════════════════════════════════════════════════
# ADVANCED CRM SUITE FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def search_users(query: str) -> list[dict]:
    """Поиск клиентов по Telegram ID, Username (@username) или имени."""
    q = query.strip().lstrip("@")
    if not q:
        return []
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if q.isdigit():
                cursor = await db.execute(
                    """SELECT * FROM users 
                       WHERE telegram_id = ? 
                          OR CAST(telegram_id AS TEXT) LIKE ? 
                          OR username LIKE ? 
                          OR first_name LIKE ? 
                       ORDER BY approved_at DESC LIMIT 20""",
                    (int(q), f"%{q}%", f"%{q}%", f"%{q}%")
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM users 
                       WHERE username LIKE ? 
                          OR first_name LIKE ? 
                       ORDER BY approved_at DESC LIMIT 20""",
                    (f"%{q}%", f"%{q}%")
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows if r["telegram_id"] != config.ADMIN_ID]
    except Exception as e:
        logger.error("search_users error (%s): %s", query, e)
        return []


async def update_admin_notes(telegram_id: int, notes: str) -> bool:
    """Сохраняет заметку администратора о клиенте."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                "UPDATE users SET admin_notes = ? WHERE telegram_id = ?",
                (notes.strip(), telegram_id)
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error("update_admin_notes error (%d): %s", telegram_id, e)
        return False


async def record_payment(
    telegram_id: int,
    amount_usd: float,
    days_added: int,
    tariff: str,
    payment_method: str = "USDT",
    comment: str = "",
    created_by: int = 0
) -> int:
    """Записывает финансовую транзакцию оплаты подписки в CRM."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """INSERT INTO crm_payments 
                   (telegram_id, amount_usd, days_added, tariff, payment_method, comment, created_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (telegram_id, amount_usd, days_added, tariff, payment_method, comment, now_iso, created_by)
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error("record_payment error: %s", e)
        return 0


async def get_user_payments(telegram_id: int) -> list[dict]:
    """Возвращает историю платежей конкретного пользователя."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM crm_payments WHERE telegram_id = ? ORDER BY created_at DESC",
                (telegram_id,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_user_payments error: %s", e)
        return []


async def get_user_ltv(telegram_id: int) -> tuple[float, int]:
    """Возвращает (общая сумма оплат USD, количество оплат) для клиента."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0.0), COUNT(*) FROM crm_payments WHERE telegram_id = ?",
                (telegram_id,)
            )
            row = await cursor.fetchone()
            if row:
                return float(row[0]), int(row[1])
    except Exception:
        pass
    return 0.0, 0


async def get_crm_finance_summary() -> dict:
    """Возвращает сводную финансовую аналитику по кассе и доходам CRM."""
    try:
        now = datetime.now(timezone.utc)
        month_start_iso = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            c1 = await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0.0), COUNT(*), COUNT(DISTINCT telegram_id) FROM crm_payments"
            )
            r1 = await c1.fetchone()
            total_usd = float(r1[0]) if r1 else 0.0
            total_tx = int(r1[1]) if r1 else 0
            unique_clients = int(r1[2]) if r1 else 0

            c2 = await db.execute(
                "SELECT COALESCE(SUM(amount_usd), 0.0), COUNT(*) FROM crm_payments WHERE created_at >= ?",
                (month_start_iso,)
            )
            r2 = await c2.fetchone()
            month_usd = float(r2[0]) if r2 else 0.0
            month_tx = int(r2[1]) if r2 else 0

            avg_check = (total_usd / total_tx) if total_tx > 0 else 0.0

            return {
                "total_usd": total_usd,
                "total_tx": total_tx,
                "unique_clients": unique_clients,
                "month_usd": month_usd,
                "month_tx": month_tx,
                "avg_check": avg_check,
                "month_name": now.strftime("%B %Y")
            }
    except Exception as e:
        logger.error("get_crm_finance_summary error: %s", e)
        return {
            "total_usd": 0.0, "total_tx": 0, "unique_clients": 0,
            "month_usd": 0.0, "month_tx": 0, "avg_check": 0.0, "month_name": ""
        }


async def check_and_expire_subscriptions() -> list[dict]:
    """
    Находит всех пользователей с истёкшей датой подписки,
    автоматически переводит их статус в 'expired' и возвращает список для рассылки уведомлений.
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM users 
                   WHERE status = 'approved' 
                     AND (is_lifetime IS NULL OR is_lifetime = 0)
                     AND expires_at IS NOT NULL 
                     AND expires_at < ?""",
                (now_iso,)
            )
            rows = await cursor.fetchall()
            expired_users = [dict(r) for r in rows if r["telegram_id"] != config.ADMIN_ID]

            if expired_users:
                expired_ids = [u["telegram_id"] for u in expired_users]
                placeholders = ",".join("?" for _ in expired_ids)
                await db.execute(
                    f"UPDATE users SET status = 'expired' WHERE telegram_id IN ({placeholders})",
                    tuple(expired_ids)
                )
                await db.commit()
                logger.info("Auto-expired %d subscriptions in CRM.", len(expired_users))

            return expired_users
    except Exception as e:
        logger.error("check_and_expire_subscriptions error: %s", e)
        return []


async def export_users_to_csv() -> str:
    """Генерирует полный CSV-дамп базы пользователей для выгрузки в Excel/Google Sheets."""
    import csv
    import io
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users ORDER BY approved_at DESC, requested_at DESC"
            )
            rows = await cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow([
            "Telegram ID", "Username", "First Name", "Status", "Tariff",
            "Expires At", "Is Lifetime", "Total Paid USD (LTV)", "Payments Count",
            "Requested At", "Approved At", "Last Seen", "Activity Count", "Admin Notes"
        ])

        now = datetime.now(timezone.utc)
        for raw in rows:
            r = dict(raw)
            uid = r["telegram_id"]
            if uid == config.ADMIN_ID:
                continue
            ltv_usd, ltv_cnt = await get_user_ltv(uid)
            writer.writerow([
                uid,
                f"@{r['username']}" if r.get("username") else "",
                r.get("first_name") or "",
                r.get("status") or "",
                r.get("tariff") or "",
                (r.get("expires_at") or "")[:19].replace("T", " "),
                "YES" if r.get("is_lifetime") else "NO",
                f"{ltv_usd:.2f}",
                ltv_cnt,
                (r.get("requested_at") or "")[:19].replace("T", " "),
                (r.get("approved_at") or "")[:19].replace("T", " "),
                (r.get("last_seen") or "")[:19].replace("T", " "),
                r.get("activity_count") or 0,
                r.get("admin_notes") or ""
            ])
        return output.getvalue()
    except Exception as e:
        logger.error("export_users_to_csv error: %s", e)
        return ""
