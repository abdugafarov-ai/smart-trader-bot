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
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA synchronous = NORMAL;")
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
        if "broker_confirmed" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN broker_confirmed INTEGER DEFAULT 0")
        if "broker_ticket" not in columns:
            await db.execute("ALTER TABLE signals ADD COLUMN broker_ticket INTEGER DEFAULT 0")

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS broker_deals (
                ticket INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                deal_type TEXT NOT NULL,
                lot REAL NOT NULL,
                price REAL NOT NULL,
                profit_usd REAL NOT NULL,
                close_time INTEGER NOT NULL,
                magic INTEGER DEFAULT 0,
                comment TEXT DEFAULT '',
                created_at TEXT NOT NULL
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

        await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals (status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON signals (created_at);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broker_deals_close_time ON broker_deals (close_time);")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # system_settings — хранит stats_reset_time и другие системные значения
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # По умолчанию устанавливаем режим micro для депозита $12 (если ещё не задан)
        await db.execute("""
            INSERT OR IGNORE INTO bot_settings (key, value, updated_at)
            VALUES ('trading_mode', 'micro', ?)
        """, (datetime.now(timezone.utc).isoformat(),))

        await db.commit()
    logger.info("Database initialized with full schema, broker_deals, and bot_settings table at %s", DB_PATH)


async def get_bot_setting(key: str, default: str = "") -> str:
    """Получить значение настройки из БД."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
        return default
    except Exception as e:
        logger.error("Error reading bot setting %s: %s", key, e)
        return default


async def set_bot_setting(key: str, value: str):
    """Сохранить значение настройки в БД."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("""
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, str(value), now_iso))
            await db.commit()
    except Exception as e:
        logger.error("Error setting bot setting %s: %s", key, e)


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


async def confirm_signal_by_broker(symbol: str, action: str, price: float, ticket: int = 0, signal_id: int = 0) -> dict | None:
    """Подтверждает прием ордера терминалом MT5 (для лимитов оставляет PENDING, для маркет ордеров OPEN)."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        is_limit = "LIMIT" in action.upper()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if signal_id > 0:
                cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            else:
                cursor = await db.execute(
                    """SELECT * FROM signals
                       WHERE symbol = ? AND status IN ('PENDING', 'ACTIVE', 'OPEN')
                       ORDER BY id DESC LIMIT 1""",
                    (symbol,)
                )
            row = await cursor.fetchone()
            if row:
                sig = dict(row)
                if is_limit:
                    # Лимитный ордер выставлен в MT5: статус PENDING, но подтвержден брокером
                    await db.execute(
                        """UPDATE signals
                           SET status = 'PENDING', broker_confirmed = 1,
                               broker_ticket = CASE WHEN ? > 0 THEN ? ELSE broker_ticket END,
                               entry_price = CASE WHEN entry_price IS NULL OR entry_price = 0 THEN ? ELSE entry_price END
                           WHERE id = ?""",
                        (ticket, ticket, price, sig['id'])
                    )
                else:
                    # Рыночный ордер сразу открыт
                    await db.execute(
                        """UPDATE signals
                           SET status = 'OPEN', broker_confirmed = 1,
                               broker_ticket = CASE WHEN ? > 0 THEN ? ELSE broker_ticket END,
                               activated_at = COALESCE(activated_at, ?),
                               entry_price = CASE WHEN entry_price IS NULL OR entry_price = 0 THEN ? ELSE entry_price END
                           WHERE id = ?""",
                        (ticket, ticket, now_iso, price, sig['id'])
                    )
                await db.commit()
                return sig
            return None
    except Exception as e:
        logger.error("confirm_signal_by_broker error: %s", e)
        return None


async def activate_filled_signal(symbol: str, price: float, ticket: int = 0, signal_id: int = 0) -> dict | None:
    """Активирует лимитный ордер (переводит PENDING -> OPEN), когда цена коснулась лимита и брокер открыл позицию."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if signal_id > 0:
                cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            else:
                cursor = await db.execute(
                    """SELECT * FROM signals
                       WHERE symbol = ? AND status = 'PENDING'
                       ORDER BY id DESC LIMIT 1""",
                    (symbol,)
                )
            row = await cursor.fetchone()
            if row:
                sig = dict(row)
                await db.execute(
                    """UPDATE signals
                       SET status = 'OPEN', broker_confirmed = 1,
                           broker_ticket = CASE WHEN ? > 0 THEN ? ELSE broker_ticket END,
                           activated_at = ?,
                           entry_price = CASE WHEN ? > 0 THEN ? ELSE entry_price END
                       WHERE id = ?""",
                    (ticket, ticket, now_iso, price, price, sig['id'])
                )
                await db.commit()
                sig['status'] = 'OPEN'
                sig['activated_at'] = now_iso
                if price > 0:
                    sig['entry_price'] = price
                return sig
            return None
    except Exception as e:
        logger.error("activate_filled_signal error: %s", e)
        return None


async def expire_signal_by_broker(symbol: str, signal_id: int = 0, reason: str = "") -> dict | None:
    """Отмечает ордер как EXPIRED, если лимит был снят или истек срок действия в MT5."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if signal_id > 0:
                cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            else:
                cursor = await db.execute(
                    """SELECT * FROM signals
                       WHERE symbol = ? AND status = 'PENDING'
                       ORDER BY id DESC LIMIT 1""",
                    (symbol,)
                )
            row = await cursor.fetchone()
            if row:
                sig = dict(row)
                res_text = reason or "Истёк срок ожидания входа (снят брокером)"
                await db.execute(
                    """UPDATE signals
                       SET status = 'EXPIRED', closed_at = ?, pnl_pips = 0.0, result = ?
                       WHERE id = ?""",
                    (now_iso, res_text, sig['id'])
                )
                await db.commit()
                sig['status'] = 'EXPIRED'
                sig['result'] = res_text
                return sig
            return None
    except Exception as e:
        logger.error("expire_signal_by_broker error: %s", e)
        return None


async def reject_signal_by_broker(symbol: str, reason: str, signal_id: int = 0) -> dict | None:
    """Отменяет сигнал, если MT5 заблокировал вход (R:R < 1.8, ошибка терминала или лимит слотов)."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            if signal_id > 0:
                cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
            else:
                cursor = await db.execute(
                    """SELECT * FROM signals
                       WHERE symbol = ? AND status IN ('PENDING', 'ACTIVE')
                       ORDER BY id DESC LIMIT 1""",
                    (symbol,)
                )
            row = await cursor.fetchone()
            if row:
                sig = dict(row)
                await db.execute(
                    """UPDATE signals
                       SET status = 'CANCELLED_BY_MT5', closed_at = ?, result = ?
                       WHERE id = ?""",
                    (now_iso, reason, sig['id'])
                )
                await db.commit()
                return sig
            return None
    except Exception as e:
        logger.error("reject_signal_by_broker error: %s", e)
        return None


async def close_signal_by_broker(symbol: str, close_price: float, profit_usd: float, reason: str = "") -> dict | None:
    """Закрывает сделку по факту закрытия позиции в MT5."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM signals
                   WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE', 'TP1_PARTIAL')
                   ORDER BY id DESC LIMIT 1""",
                (symbol,)
            )
            row = await cursor.fetchone()
            if row:
                sig = dict(row)
                status = "TP1_HIT" if profit_usd > 0 else ("SL_HIT" if profit_usd < 0 else "BREAKEVEN")
                res_text = f"MT5: {profit_usd:+.2f}$ ({reason})" if reason else f"MT5: {profit_usd:+.2f}$"
                await db.execute(
                    """UPDATE signals
                       SET status = ?, close_price = ?, closed_at = ?, result = ?
                       WHERE id = ?""",
                    (status, close_price, now_iso, res_text, sig['id'])
                )
                await db.commit()
                return sig
            return None
    except Exception as e:
        logger.error("close_signal_by_broker error: %s", e)
        return None


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


async def get_stats_reset_time() -> Optional[int]:
    """Получает unix timestamp последнего сброса статистики."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute("SELECT value FROM system_settings WHERE key = 'stats_reset_time'")
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] else None
    except Exception:
        return None


async def set_stats_reset_time(ts: Optional[int] = None) -> int:
    """Устанавливает unix timestamp сброса статистики."""
    if ts is None:
        import time
        ts = int(time.time())
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            """INSERT INTO system_settings (key, value) VALUES ('stats_reset_time', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(ts),)
        )
        await db.commit()
    return ts


async def reset_all_stats() -> dict:
    """Полная очистка всей истории сигналов и сделок для вин-рейта."""
    import time
    ts = int(time.time())
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM broker_deals")
        await db.execute("DELETE FROM signals")
        await db.execute("DELETE FROM daily_stats")
        await db.execute(
            """INSERT INTO system_settings (key, value) VALUES ('stats_reset_time', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(ts),)
        )
        await db.commit()
        await db.execute("VACUUM")
    logger.info("All trade statistics and signal history reset at timestamp %d", ts)
    return {"reset_time": ts, "status": "ok"}


async def sync_broker_deals(deals: list[dict]) -> list[dict]:
    """Синхронизирует реальные закрытые сделки терминала MetaTrader 5 и возвращает список НОВЫХ закрытых сделок."""
    if not deals:
        return []
    try:
        new_deals = []
        reset_ts = await get_stats_reset_time()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            now_iso = datetime.now(timezone.utc).isoformat()
            for d in deals:
                ticket = int(d.get("ticket") or 0)
                if ticket <= 0:
                    continue
                close_time = int(d.get("time") or 0)
                # Игнорируем сделки, закрытые до момента сброса статистики
                if reset_ts and close_time < reset_ts:
                    continue

                # Проверяем, была ли эта сделка уже зафиксирована в базе
                cursor = await db.execute("SELECT ticket FROM broker_deals WHERE ticket = ?", (ticket,))
                already_exists = await cursor.fetchone()

                sym = str(d.get("symbol", "")).upper()
                deal_type = str(d.get("type", "BUY")).upper()
                lot = float(d.get("lot") or d.get("volume") or 0.01)
                price = float(d.get("price") or 0.0)
                profit = float(d.get("profit") or 0.0)
                magic = int(d.get("magic") or 0)
                comment = str(d.get("comment") or "")

                await db.execute(
                    """INSERT INTO broker_deals (ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(ticket) DO UPDATE SET
                           profit_usd = excluded.profit_usd,
                           price = excluded.price,
                           comment = excluded.comment""",
                    (ticket, sym, deal_type, lot, price, profit, close_time, magic, comment, now_iso),
                )

                if not already_exists:
                    new_deals.append({
                        "ticket": ticket,
                        "symbol": sym,
                        "type": deal_type,
                        "lot": lot,
                        "price": price,
                        "profit": profit,
                        "comment": comment
                    })

            await db.commit()
        return new_deals
    except Exception as e:
        logger.error("sync_broker_deals error: %s", e)
        return []


async def get_recent_signals(limit: int = 20) -> list[dict]:
    """Возвращает реальные сделки и ордера брокера MT5 для истории."""
    try:
        from trading.execution_bridge import bridge_manager
        telemetry = bridge_manager.mt5_telemetry

        results = []

        # 1. Открытые позиции в рынке
        for p in (telemetry.get("positions") or []):
            results.append({
                "ticket": p.get("ticket", 0),
                "symbol": p.get("symbol", ""),
                "direction": p.get("type", "BUY"),
                "order_type": "MARKET",
                "tag_emoji": "🚀",
                "status": "ACTIVE",
                "entry_price": p.get("price", 0.0),
                "profit_usd": p.get("profit", 0.0),
                "lot": p.get("lot", 0.01),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        # 2. Отложенные ордера в стакане
        for o in (telemetry.get("orders") or []):
            results.append({
                "ticket": o.get("ticket", 0),
                "symbol": o.get("symbol", ""),
                "direction": "BUY" if "BUY" in str(o.get("type", "")) else "SELL",
                "order_type": o.get("type", "LIMIT"),
                "tag_emoji": "⏳",
                "status": "PENDING",
                "entry_price": o.get("price", 0.0),
                "profit_usd": 0.0,
                "lot": o.get("lot", 0.01),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        # 3. Закрытые сделки из broker_deals
        reset_ts = await get_stats_reset_time()
        time_cond = f"WHERE close_time >= {reset_ts}" if reset_ts else ""
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM broker_deals {time_cond} ORDER BY close_time DESC, ticket DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            for r in rows:
                d = dict(r)
                pnl = d.get("profit_usd", 0.0)
                if pnl > 0:
                    status = "TP1_HIT"
                elif pnl < 0:
                    status = "SL_HIT"
                else:
                    status = "BREAKEVEN"

                results.append({
                    "ticket": d.get("ticket", 0),
                    "symbol": d.get("symbol", ""),
                    "direction": d.get("deal_type", "BUY"),
                    "order_type": "MARKET",
                    "tag_emoji": "✅" if pnl > 0 else ("🛡" if pnl == 0 else "🛑"),
                    "status": status,
                    "entry_price": d.get("price", 0.0),
                    "close_price": d.get("price", 0.0),
                    "profit_usd": pnl,
                    "lot": d.get("lot", 0.01),
                    "created_at": d.get("created_at"),
                    "result": d.get("comment") or "",
                })

        return results[:limit]
    except Exception as e:
        logger.error("get_recent_signals error: %s", e)
        return []


async def get_stats() -> dict:
    """Возвращает 100% честную статистику торговли по реальным сделкам брокера MT5."""
    try:
        from trading.execution_bridge import bridge_manager
        is_online, ping = bridge_manager.is_mt5_online()
        telemetry = bridge_manager.mt5_telemetry

        open_positions = telemetry.get("positions") or []
        pending_orders = telemetry.get("orders") or []

        reset_ts = await get_stats_reset_time()

        async with aiosqlite.connect(str(DB_PATH)) as db:
            if reset_ts:
                cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE close_time >= ?", (reset_ts,))
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM broker_deals")
            deals_count = (await cursor.fetchone())[0]

            if deals_count > 0:
                if reset_ts:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd > 0 AND close_time >= ?", (reset_ts,))
                else:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd > 0")
                wins = (await cursor.fetchone())[0]

                if reset_ts:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd < 0 AND close_time >= ?", (reset_ts,))
                else:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd < 0")
                losses = (await cursor.fetchone())[0]

                if reset_ts:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd == 0 AND close_time >= ?", (reset_ts,))
                else:
                    cursor = await db.execute("SELECT COUNT(*) FROM broker_deals WHERE profit_usd == 0")
                breakevens = (await cursor.fetchone())[0]

                if reset_ts:
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE close_time >= ?", (reset_ts,))
                else:
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals")
                total_profit_usd = (await cursor.fetchone())[0]

                # Win Rate рассчитывается как доля безубыточных и прибыльных сделок (non-losing trades)
                win_rate = ((deals_count - losses) / deals_count * 100) if deals_count > 0 else 0.0

                if reset_ts:
                    cursor = await db.execute(
                        """SELECT symbol, COUNT(*) as cnt,
                                  SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                                  SUM(profit_usd) as pnl
                           FROM broker_deals WHERE close_time >= ? GROUP BY symbol ORDER BY pnl DESC""",
                        (reset_ts,)
                    )
                else:
                    cursor = await db.execute(
                        """SELECT symbol, COUNT(*) as cnt,
                                  SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                                  SUM(profit_usd) as pnl
                           FROM broker_deals GROUP BY symbol ORDER BY pnl DESC"""
                    )
                by_symbol = {}
                for row in await cursor.fetchall():
                    s, cnt, w, pnl = row
                    by_symbol[s] = {"total": cnt, "wins": w, "profit_usd": round(pnl, 2)}

                if reset_ts:
                    cursor = await db.execute(
                        """SELECT deal_type, COUNT(*) as cnt,
                                  SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                                  SUM(profit_usd) as pnl
                           FROM broker_deals WHERE close_time >= ? GROUP BY deal_type""",
                        (reset_ts,)
                    )
                else:
                    cursor = await db.execute(
                        """SELECT deal_type, COUNT(*) as cnt,
                                  SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                                  SUM(profit_usd) as pnl
                           FROM broker_deals GROUP BY deal_type"""
                    )
                by_direction = {}
                for row in await cursor.fetchall():
                    d, cnt, w, pnl = row
                    by_direction[d] = {"total": cnt, "wins": w, "profit_usd": round(pnl, 2)}

                # Расчет реального среднего R:R по сигналам (включая BREAKEVEN)
                cursor = await db.execute(
                    """SELECT AVG(CASE WHEN risk_reward > 0 THEN risk_reward ELSE 2.0 END) 
                       FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'CLOSED', 'BREAKEVEN', 'CLOSED_BE')"""
                )
                avg_rr_row = await cursor.fetchone()
                avg_rr = round(float(avg_rr_row[0]), 2) if avg_rr_row and avg_rr_row[0] else 2.0

                # Расчет реальных пунктов (pips) из закрытых сигналов
                cursor = await db.execute(
                    """SELECT COALESCE(SUM(pnl_pips), 0.0) 
                       FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'CLOSED', 'BREAKEVEN', 'CLOSED_BE')"""
                )
                pips_row = await cursor.fetchone()
                total_pips = round(float(pips_row[0]), 1) if pips_row and pips_row[0] else 0.0

                # Расчет валовой прибыли и валового убытка
                if reset_ts:
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd > 0 AND close_time >= ?", (reset_ts,))
                    gross_profit = (await cursor.fetchone())[0]
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd < 0 AND close_time >= ?", (reset_ts,))
                    gross_loss = abs((await cursor.fetchone())[0])
                else:
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd > 0")
                    gross_profit = (await cursor.fetchone())[0]
                    cursor = await db.execute("SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals WHERE profit_usd < 0")
                    gross_loss = abs((await cursor.fetchone())[0])

                profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)
                avg_win = round(gross_profit / wins, 2) if wins > 0 else 0.0
                avg_loss = round(gross_loss / losses, 2) if losses > 0 else 0.0
                win_pct = wins / deals_count if deals_count > 0 else 0.0
                loss_pct = losses / deals_count if deals_count > 0 else 0.0
                expectancy = round((win_pct * avg_win) - (loss_pct * avg_loss), 2)

                return {
                    "total": deals_count,
                    "open": len(open_positions),
                    "closed": deals_count,
                    "wins": wins,
                    "losses": losses,
                    "breakevens": breakevens,
                    "expired": 0,
                    "win_rate": round(win_rate, 1),
                    "total_profit_usd": round(total_profit_usd, 2),
                    "total_pips": total_pips,
                    "avg_rr": avg_rr,
                    "profit_factor": profit_factor,
                    "expectancy": expectancy,
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "by_direction": by_direction,
                    "by_symbol": by_symbol,
                    "balance": telemetry.get("balance", 0.0),
                    "equity": telemetry.get("equity", 0.0),
                    "broker": telemetry.get("broker", "—"),
                    "account": telemetry.get("account", "—"),
                    "mt5_online": is_online,
                    "open_positions": open_positions,
                    "pending_orders": pending_orders,
                }
            else:
                return {
                    "total": 0, "open": len(open_positions), "closed": 0,
                    "wins": 0, "losses": 0, "expired": 0, "breakevens": 0,
                    "win_rate": 0.0, "total_profit_usd": 0.0, "total_pips": 0.0, "avg_rr": 0.0,
                    "by_direction": {}, "by_symbol": {},
                    "balance": telemetry.get("balance", 0.0), "equity": telemetry.get("equity", 0.0),
                    "broker": telemetry.get("broker", "—"), "account": telemetry.get("account", "—"),
                    "mt5_online": is_online,
                    "open_positions": open_positions, "pending_orders": pending_orders,
                }
    except Exception as e:
        logger.error("get_stats error: %s", e)
        return {
            "total": 0, "open": 0, "closed": 0,
            "wins": 0, "losses": 0, "expired": 0, "breakevens": 0,
            "win_rate": 0.0, "total_profit_usd": 0.0, "total_pips": 0.0, "avg_rr": 0.0,
            "by_direction": {}, "by_symbol": {},
        }


async def check_signal_exists(symbol: str, direction: str, hours: int = 6) -> bool:
    """Проверяет наличие активного сигнала по паре."""
    return await has_open_signal_for_pair(symbol)


async def get_drawdown_reset_time() -> Optional[str]:
    """Получает время последнего ручного сброса просадки."""
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute("SELECT value FROM system_settings WHERE key = 'drawdown_reset_time'")
            row = await cursor.fetchone()
            return row[0] if row else None
    except Exception:
        return None


async def set_drawdown_reset_now() -> bool:
    """Сбрасывает таймер просадки на текущий момент."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.execute(
                "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('drawdown_reset_time', ?)",
                (now_iso,)
            )
            await db.commit()
            logger.info("Drawdown reset timestamp saved: %s", now_iso)
            return True
    except Exception as e:
        logger.error("set_drawdown_reset_now error: %s", e)
        return False


async def get_consecutive_sl_count(max_lookback_hours: float = 12.0) -> int:
    """
    Считает количество последовательных Stop Loss среди последних закрытых сигналов.
    Автоматически сбрасывается, если последний SL был закрыт более max_lookback_hours назад,
    или если администратор выполнил сброс просадки.
    """
    try:
        reset_time_str = await get_drawdown_reset_time()
        reset_dt = datetime.fromisoformat(reset_time_str) if reset_time_str else None
        if reset_dt and reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)

        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """SELECT status, closed_at FROM signals 
                   WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BREAKEVEN', 'CLOSED_BE') 
                   ORDER BY closed_at DESC LIMIT 10"""
            )
            rows = await cursor.fetchall()
            sl_count = 0
            now = datetime.now(timezone.utc)
            for status, closed_at_str in rows:
                if not closed_at_str:
                    continue
                try:
                    closed_at = datetime.fromisoformat(closed_at_str)
                    if closed_at.tzinfo is None:
                        closed_at = closed_at.replace(tzinfo=timezone.utc)

                    # 1. Если сделка закрыта до последнего ручного сброса просадки — стоп
                    if reset_dt and closed_at < reset_dt:
                        break

                    # 2. Если сделка закрыта более max_lookback_hours назад — авто-сброс
                    age_hours = (now - closed_at).total_seconds() / 3600.0
                    if age_hours > max_lookback_hours:
                        break
                except Exception:
                    pass

                if status == 'SL_HIT':
                    sl_count += 1
                else:
                    break
            return sl_count
    except Exception as e:
        logger.error("get_consecutive_sl_count error: %s", e)
        return 0


async def get_today_signal_count() -> int:
    """Считает количество реально исполненных/активных сигналов за сегодня (исключая отменённые MT5)."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiosqlite.connect(str(DB_PATH)) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) FROM signals 
                   WHERE created_at LIKE ? 
                   AND status NOT IN ('CANCELLED_BY_MT5', 'CANCELLED_UNCONFIRMED', 'EXPIRED')""",
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

