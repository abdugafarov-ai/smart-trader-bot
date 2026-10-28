"""
Smart Trader Bot — SQLite Database Manager.
Хранит сигналы с уникальными эмодзи-маркерами, отслеживает активацию, TP/SL и ведет историю.
"""

import aiosqlite
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import json

from utils.trade_metrics import (
    calculate_planned_rr,
    calculate_actual_r,
    calculate_trade_costs,
    calculate_duration,
    calculate_mae_mfe,
    detect_trading_session,
)
from utils.trade_export import (
    export_trades_to_csv,
    export_trades_to_json,
    sanitize_trade_record,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "signals.db"


async def init_db():
    """Создаёт базу данных, таблицы и выполняет миграции если нужно."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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

        # Миграция колонок на случай старой структуры signals
        cursor = await db.execute("PRAGMA table_info(signals)")
        columns = [row[1] for row in await cursor.fetchall()]

        signal_cols = [
            ("order_type", "TEXT DEFAULT 'BUY_LIMIT'"),
            ("tag_emoji", "TEXT DEFAULT '🔥'"),
            ("current_price", "REAL"),
            ("activated_at", "TEXT"),
            ("breakeven_applied", "INTEGER DEFAULT 0"),
            ("broker_confirmed", "INTEGER DEFAULT 0"),
            ("broker_ticket", "INTEGER DEFAULT 0"),
            ("lot", "REAL"),
            ("position_id", "INTEGER"),
            ("order_id", "INTEGER"),
            ("close_order_id", "INTEGER"),
            ("deal_ticket", "INTEGER"),
            ("record_type", "TEXT DEFAULT 'LIVE'"),
            ("initial_sl", "REAL"),
            ("final_sl", "REAL"),
            ("initial_tp", "REAL"),
            ("final_tp", "REAL"),
            ("balance_before", "REAL"),
            ("equity_before", "REAL"),
            ("risk_usd", "REAL"),
            ("risk_pct", "REAL"),
            ("planned_rr", "REAL"),
            ("actual_r", "REAL"),
            ("gross_profit", "REAL"),
            ("commission", "REAL"),
            ("swap", "REAL"),
            ("slippage", "REAL"),
            ("mae", "REAL"),
            ("mfe", "REAL"),
            ("mae_pips", "REAL"),
            ("mfe_pips", "REAL"),
            ("mae_mfe_status", "TEXT"),
            ("trading_session", "TEXT"),
            ("strategy", "TEXT"),
            ("setup_version", "TEXT"),
            ("duration_seconds", "INTEGER"),
        ]
        for col_name, col_type in signal_cols:
            if col_name not in columns:
                await db.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")

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

        # Миграция колонок на случай старой структуры broker_deals
        cursor = await db.execute("PRAGMA table_info(broker_deals)")
        b_cols = [row[1] for row in await cursor.fetchall()]
        broker_deals_cols = [
            ("entry_price", "REAL"),
            ("close_price", "REAL"),
            ("position_id", "INTEGER"),
            ("order_id", "INTEGER"),
            ("close_order_id", "INTEGER"),
            ("gross_profit", "REAL"),
            ("commission", "REAL"),
            ("swap", "REAL"),
            ("slippage", "REAL"),
            ("record_type", "TEXT DEFAULT 'LIVE'"),
            ("signal_id", "INTEGER"),
            ("open_time", "INTEGER"),
        ]
        for col_name, col_type in broker_deals_cols:
            if col_name not in b_cols:
                await db.execute(f"ALTER TABLE broker_deals ADD COLUMN {col_name} {col_type}")

        # ── Таблица подробного журнала каждой сделки (Trade History Dossier) ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT NOT NULL DEFAULT 'LIVE',
                position_id INTEGER,
                order_id INTEGER,
                close_order_id INTEGER,
                deal_in_ticket INTEGER,
                deal_out_ticket INTEGER,
                broker_ticket INTEGER,
                signal_id INTEGER,
                broker_symbol TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                lot REAL NOT NULL,
                entry_price REAL,
                close_price REAL NOT NULL,
                initial_sl REAL,
                final_sl REAL,
                initial_tp REAL,
                final_tp REAL,
                signal_time TEXT,
                open_time TEXT,
                close_time TEXT,
                duration_seconds INTEGER,
                duration_str TEXT,
                entry_type TEXT DEFAULT 'AUTO',
                strategy TEXT,
                setup_version TEXT,
                trading_session TEXT,
                balance_before REAL,
                equity_before REAL,
                risk_usd REAL,
                risk_pct REAL,
                planned_rr REAL,
                actual_r REAL,
                gross_profit REAL,
                commission REAL,
                swap REAL,
                slippage REAL,
                slippage_pts REAL,
                net_pnl REAL,
                pnl_pips REAL,
                result TEXT,
                mae REAL,
                mfe REAL,
                mae_pips REAL,
                mfe_pips REAL,
                mae_mfe_status TEXT,
                comment TEXT DEFAULT '',
                account TEXT,
                raw_telemetry TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Migration: ensure account column exists on trade_history
        cursor = await db.execute("PRAGMA table_info(trade_history);")
        th_cols = [row[1] for row in await cursor.fetchall()]
        if th_cols and "account" not in th_cols:
            await db.execute("ALTER TABLE trade_history ADD COLUMN account TEXT;")

        # Миграция существующих исторических сделок из broker_deals в trade_history если trade_history пуста
        cursor = await db.execute("SELECT COUNT(*) FROM trade_history")
        th_count = (await cursor.fetchone())[0]
        if th_count == 0:
            cursor = await db.execute("SELECT * FROM broker_deals ORDER BY close_time ASC")
            b_rows = await cursor.fetchall()
            for b_row in b_rows:
                d = dict(b_row)
                pos_id = d.get("position_id") or d.get("ticket") or 0
                close_p = float(d.get("close_price") or d.get("price") or 0.0)
                entry_p = d.get("entry_price")
                if (entry_p is None or entry_p == 0.0) and pos_id > 0:
                    c_s = await db.execute(
                        "SELECT entry_price, created_at, activated_at, order_type FROM signals WHERE broker_ticket = ? OR position_id = ? ORDER BY id DESC LIMIT 1",
                        (pos_id, pos_id)
                    )
                    s_r = await c_s.fetchone()
                    if s_r and s_r[0]:
                        entry_p = float(s_r[0])
                c_time_int = d.get("close_time", 0)
                c_time_iso = (
                    datetime.fromtimestamp(c_time_int, tz=timezone.utc).isoformat()
                    if c_time_int else datetime.now(timezone.utc).isoformat()
                )
                dur_sec, dur_str = calculate_duration(c_time_iso, c_time_iso)
                t_sess = detect_trading_session(c_time_iso)
                sym = d.get("symbol", "")
                deal_type = d.get("deal_type", "BUY")
                dir_clean = "BUY" if "BUY" in deal_type else "SELL"
                pnl = float(d.get("profit_usd") or 0.0)
                res_val = "TP1_HIT" if pnl > 0 else ("SL_HIT" if pnl < 0 else "BREAKEVEN")
                await db.execute("""
                    INSERT INTO trade_history (
                        record_type, position_id, order_id, close_order_id,
                        deal_in_ticket, deal_out_ticket, broker_ticket, signal_id,
                        broker_symbol, symbol, direction, lot,
                        entry_price, close_price,
                        signal_time, open_time, close_time, duration_seconds, duration_str,
                        entry_type, strategy, setup_version, trading_session,
                        gross_profit, commission, swap, slippage,
                        net_pnl, pnl_pips, result,
                        comment, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    d.get("record_type") or "LIVE", pos_id, d.get("order_id") or 0, 0,
                    0, d.get("ticket", 0), pos_id, d.get("signal_id"),
                    sym, sym, dir_clean, float(d.get("lot") or 0.01),
                    entry_p, close_p,
                    c_time_iso, c_time_iso, c_time_iso, dur_sec, dur_str,
                    "AUTO", "ICT_SMC", "v1.0", t_sess,
                    d.get("gross_profit"), d.get("commission"), d.get("swap"), d.get("slippage"),
                    pnl, 0.0, res_val,
                    d.get("comment", ""), d.get("created_at") or c_time_iso
                ))
            if b_rows:
                await db.commit()
                logger.info("Migrated %d existing broker deals into trade_history", len(b_rows))

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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_record_type ON trade_history (record_type);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_pos ON trade_history (position_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_close_time ON trade_history (close_time);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_history_symbol ON trade_history (symbol);")
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

        # По умолчанию устанавливаем режим prop для FundingPips $5,000 XAUUSD Intraday (если ещё не задан)
        await db.execute("""
            INSERT OR IGNORE INTO bot_settings (key, value, updated_at)
            VALUES ('trading_mode', 'prop', ?)
        """, (datetime.now(timezone.utc).isoformat(),))

        await db.commit()
    logger.info("Database initialized with full schema, broker_deals, trade_history, and bot_settings at %s", DB_PATH)


async def get_bot_setting(key: str, default: str = "") -> str:
    """Получить значение настройки из БД."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
    order_type: str = "MARKET",
    tag_emoji: str = "💎",
    stars: int = 5,
    current_price: float | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit_1: float | None = None,
    take_profit_2: float | None = None,
    risk_reward: float | None = None,
    strategies_agreed: str = "",
    timeframes_agreed: str = "",
    lot: float | None = None,
    record_type: str = "LIVE",
    strategy: str = "",
    setup_version: str = "",
    trading_session: str = "",
    balance_before: float | None = None,
    equity_before: float | None = None,
    risk_usd: float | None = None,
    risk_pct: float | None = None,
    planned_rr: float | None = None,
    initial_sl: float | None = None,
    initial_tp: float | None = None,
    position_id: int | None = None,
    entry: float | None = None,
    sl: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    status: str | None = None,
) -> int | None:
    """Сохраняет новый сигнал в БД (ACTIVE для MARKET ордеров, PENDING для LIMIT). Возвращает ID."""
    try:
        if entry_price is None and entry is not None:
            entry_price = entry
        if stop_loss is None and sl is not None:
            stop_loss = sl
        if take_profit_1 is None and tp1 is not None:
            take_profit_1 = tp1
        if take_profit_2 is None and tp2 is not None:
            take_profit_2 = tp2
        if current_price is None:
            current_price = entry_price

        now_iso = datetime.now(timezone.utc).isoformat()
        initial_status = status or ("ACTIVE" if "MARKET" in (order_type or "") else "PENDING")
        activated_at = now_iso if initial_status == "ACTIVE" else None

        init_sl = initial_sl if initial_sl is not None else stop_loss
        init_tp = initial_tp if initial_tp is not None else take_profit_1
        p_rr = planned_rr if planned_rr is not None else (
            calculate_planned_rr(entry_price, init_sl, init_tp) or risk_reward
        )
        t_session = trading_session or detect_trading_session(now_iso)
        strat = strategy or strategies_agreed or "ICT_SMC"
        s_version = setup_version or (f"{stars} stars" if stars else "v1.0")

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(
                """INSERT INTO signals
                   (symbol, direction, order_type, tag_emoji, stars,
                    current_price, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed, status, created_at, activated_at, lot,
                    record_type, initial_sl, final_sl, initial_tp, final_tp,
                    balance_before, equity_before, risk_usd, risk_pct, planned_rr,
                    trading_session, strategy, setup_version, position_id, broker_ticket)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, direction, order_type, tag_emoji, stars,
                    current_price, entry_price, stop_loss,
                    take_profit_1, take_profit_2, risk_reward,
                    strategies_agreed, timeframes_agreed,
                    initial_status, now_iso, activated_at, lot,
                    record_type, init_sl, stop_loss, init_tp, take_profit_1,
                    balance_before, equity_before, risk_usd, risk_pct, p_rr,
                    t_session, strat, s_version, position_id, position_id
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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


async def close_signal_by_broker(
    symbol: str = "",
    close_price: float = 0.0,
    profit_usd: float = 0.0,
    reason: str = "",
    ticket: int = 0,
    position_id: int = 0,
    signal_id: int = 0,
    entry_price: float | None = None,
    lot: float | None = None,
    commission: float | None = None,
    swap: float | None = None,
    gross_profit: float | None = None,
    slippage: float | None = None,
    record_type: str = "LIVE",
    net_pnl: float | None = None,
    status: str | None = None,
) -> dict | None:
    """Закрывает сделку по факту закрытия позиции в MT5 и сохраняет подробное досье в trade_history."""
    try:
        if net_pnl is not None and profit_usd == 0.0:
            profit_usd = net_pnl
        if status and not reason:
            reason = status

        now_iso = datetime.now(timezone.utc).isoformat()
        pos_ticket = position_id or ticket
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row

            # 1. Надёжный поиск сигнала по точному ID / тикету позиции
            row = None
            if signal_id > 0:
                cursor = await db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
                row = await cursor.fetchone()
            elif pos_ticket > 0:
                cursor = await db.execute(
                    """SELECT * FROM signals 
                       WHERE (position_id = ? OR broker_ticket = ?)
                       ORDER BY id DESC LIMIT 1""",
                    (pos_ticket, pos_ticket)
                )
                row = await cursor.fetchone()

            if not row:
                cursor = await db.execute(
                    """SELECT * FROM signals
                       WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE', 'TP1_PARTIAL')
                       ORDER BY id DESC LIMIT 1""",
                    (symbol,)
                )
                row = await cursor.fetchone()

            sig = dict(row) if row else {}
            sig_id = sig.get("id") or signal_id or None

            # 2. Фактические цены входа и выхода
            entry_p = entry_price if entry_price is not None else float(sig.get("entry_price") or 0.0)
            entry_val = float(entry_p) if (entry_p is not None and float(entry_p) > 0) else None
            close_p = float(close_price or 0.0)
            direction = str(sig.get("direction") or ("BUY" if "BUY" in reason.upper() else "LONG")).upper()
            dir_clean = "BUY" if "BUY" in direction or "LONG" in direction else "SELL"
            sym_u = str(sig.get("symbol") or symbol).upper()
            pip_mult = 100.0 if "JPY" in sym_u else (10.0 if "XAU" in sym_u else 10000.0)

            if entry_val is not None and close_p > 0:
                raw_pips = (close_p - entry_val) * pip_mult if dir_clean == "BUY" else (entry_val - close_p) * pip_mult
                pnl_pips = round(raw_pips, 1)
            else:
                pnl_pips = 0.0

            # 3. Торговые издержки и чистый PnL
            costs = calculate_trade_costs(
                gross_profit=gross_profit,
                commission=commission,
                swap=swap,
                slippage=slippage,
                reported_profit=profit_usd,
            )
            net_profit = costs["net_pnl"]

            reason_u = str(reason or "").upper()
            if "PARTIAL" in reason_u:
                status = reason_u if ("TP" in reason_u or "SL" in reason_u) else "PARTIAL_CLOSE"
            elif "BREAKEVEN" in reason_u or (net_profit >= -0.50 and abs(pnl_pips) <= 3.5 and "TP" not in reason_u):
                status = "BREAKEVEN"
            elif net_profit > 0:
                status = "TP1_HIT"
            elif net_profit < 0:
                status = "SL_HIT"
            else:
                status = "BREAKEVEN"

            res_text = f"MT5: {net_profit:+.2f}$ ({reason})" if reason else f"MT5: {net_profit:+.2f}$"

            # 4. Расчет R-multiple и параметров риск-менеджмента
            init_sl = float(sig.get("initial_sl") or sig.get("stop_loss") or 0.0) or None
            final_sl = float(sig.get("stop_loss") or 0.0) or None
            init_tp = float(sig.get("initial_tp") or sig.get("take_profit_1") or 0.0) or None
            final_tp = float(sig.get("take_profit_1") or 0.0) or None
            planned_rr = float(sig.get("planned_rr") or sig.get("risk_reward") or 0.0) or None
            if planned_rr is None and entry_p and init_sl and init_tp:
                planned_rr = calculate_planned_rr(entry_p, init_sl, init_tp)

            trade_lot = float(lot or sig.get("lot") or 0.01)
            risk_usd = float(sig.get("risk_usd") or 0.0) or None
            risk_pct = float(sig.get("risk_pct") or 0.0) or None
            actual_r = calculate_actual_r(
                entry=entry_p if entry_p > 0 else None,
                close=close_p if close_p > 0 else None,
                initial_sl=init_sl,
                direction=dir_clean,
                net_pnl=net_profit,
                risk_usd=risk_usd,
            )

            # 5. Время, длительность и торговая сессия
            open_time_str = sig.get("activated_at") or sig.get("created_at") or now_iso
            close_time_str = now_iso
            dur_sec, dur_str = calculate_duration(open_time_str, close_time_str)
            t_session = sig.get("trading_session") or detect_trading_session(open_time_str)
            strat = sig.get("strategy") or sig.get("strategies_agreed") or "ICT_SMC"
            s_ver = sig.get("setup_version") or (f"{sig.get('stars')} stars" if sig.get("stars") else "v1.0")

            # 6. MAE / MFE (помечаем как UNAVAILABLE, если баров нет)
            mae_res = calculate_mae_mfe(
                symbol=sym_u,
                direction=dir_clean,
                entry_price=entry_p if entry_p > 0 else close_p,
                open_time=open_time_str,
                close_time=close_time_str,
                lot=trade_lot,
            )

            rec_type = str(sig.get("record_type") or record_type or "LIVE").upper()

            # Обновляем сигнал в таблице signals
            if sig.get("id"):
                await db.execute(
                    """UPDATE signals
                       SET status = ?, close_price = ?, pnl_pips = ?, closed_at = ?, result = ?,
                           actual_r = ?, planned_rr = COALESCE(planned_rr, ?),
                           gross_profit = ?, commission = ?, swap = ?, slippage = ?,
                           mae = ?, mfe = ?, mae_pips = ?, mfe_pips = ?, mae_mfe_status = ?,
                           duration_seconds = ?
                       WHERE id = ?""",
                    (
                        status, close_p, pnl_pips, now_iso, res_text,
                        actual_r, planned_rr,
                        costs["gross_profit"], costs["commission"], costs["swap"], costs["slippage"],
                        mae_res["mae"], mae_res["mfe"], mae_res["mae_pips"], mae_res["mfe_pips"], mae_res["status"],
                        dur_sec, sig["id"]
                    )
                )

            # 7. Сохраняем в trade_history
            await db.execute(
                """INSERT INTO trade_history (
                    record_type, position_id, order_id, close_order_id,
                    deal_in_ticket, deal_out_ticket, broker_ticket, signal_id,
                    broker_symbol, symbol, direction, lot,
                    entry_price, close_price, initial_sl, final_sl, initial_tp, final_tp,
                    signal_time, open_time, close_time, duration_seconds, duration_str,
                    entry_type, strategy, setup_version, trading_session,
                    balance_before, equity_before, risk_usd, risk_pct, planned_rr, actual_r,
                    gross_profit, commission, swap, slippage, slippage_pts,
                    net_pnl, pnl_pips, result,
                    mae, mfe, mae_pips, mfe_pips, mae_mfe_status,
                    comment, raw_telemetry, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec_type, pos_ticket, sig.get("order_id") or 0, 0,
                    0, ticket, pos_ticket, sig_id,
                    sym_u, sym_u, dir_clean, trade_lot,
                    entry_val, close_p, init_sl, final_sl, init_tp, final_tp,
                    sig.get("created_at"), open_time_str, close_time_str, dur_sec, dur_str,
                    sig.get("order_type") or "AUTO", strat, s_ver, t_session,
                    sig.get("balance_before"), sig.get("equity_before"), risk_usd, risk_pct, planned_rr, actual_r,
                    costs["gross_profit"], costs["commission"], costs["swap"], costs["slippage"], None,
                    net_profit, pnl_pips, status,
                    mae_res["mae"], mae_res["mfe"], mae_res["mae_pips"], mae_res["mfe_pips"], mae_res["status"],
                    res_text, json.dumps({"reason": reason, "profit_usd": profit_usd}), now_iso
                )
            )

            # 8. Синхронизируем broker_deals для полной совместимости
            if ticket > 0 or pos_ticket > 0:
                tk = ticket if ticket > 0 else pos_ticket
                await db.execute(
                    """INSERT INTO broker_deals (
                        ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at,
                        entry_price, close_price, position_id, order_id, gross_profit, commission, swap, slippage, record_type, signal_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticket) DO UPDATE SET
                        entry_price = excluded.entry_price,
                        close_price = excluded.close_price,
                        position_id = excluded.position_id,
                        profit_usd = excluded.profit_usd,
                        gross_profit = excluded.gross_profit,
                        commission = excluded.commission,
                        swap = excluded.swap,
                        record_type = excluded.record_type,
                        comment = excluded.comment""",
                    (
                        tk, sym_u, dir_clean, trade_lot, close_p, net_profit, int(datetime.now(timezone.utc).timestamp()),
                        sig.get("magic") or 888001, res_text, now_iso,
                        entry_val, close_p, pos_ticket, sig.get("order_id") or 0,
                        costs["gross_profit"], costs["commission"], costs["swap"], costs["slippage"],
                        rec_type, sig_id
                    )
                )

            await db.commit()

            sig["status"] = status
            sig["close_price"] = close_p
            sig["pnl_pips"] = pnl_pips
            sig["result"] = res_text
            sig["actual_r"] = actual_r
            sig["planned_rr"] = planned_rr
            sig["net_pnl"] = net_profit
            return sig
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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


async def has_open_signal_for_pair(symbol: str, record_type: str = "LIVE") -> bool:
    """Проверяет, есть ли уже активный или ожидающий сигнал по этой паре (анти-спам)."""
    try:
        cond = ""
        params = [symbol]
        if record_type and record_type.upper() != "ALL":
            if record_type.upper() == "LIVE":
                cond = "AND (record_type = 'LIVE' OR record_type IS NULL)"
            else:
                cond = "AND record_type = ?"
                params.append(record_type.upper())
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(
                f"""SELECT COUNT(*) FROM signals
                   WHERE symbol = ? AND status IN ('PENDING', 'ACTIVE', 'OPEN', 'TP1_PARTIAL') {cond}""",
                params,
            )
            count = (await cursor.fetchone())[0]
            return count > 0
    except Exception as e:
        logger.error("has_open_signal_for_pair error: %s", e)
        return False


async def get_pending_signals() -> list[dict]:
    """Возвращает все отложенные сигналы (ожидающие касания цены входа)."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
    async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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
    async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
        await db.execute("DELETE FROM broker_deals")
        await db.execute("DELETE FROM signals")
        await db.execute("DELETE FROM daily_stats")
        await db.execute("DELETE FROM trade_history")
        await db.execute(
            """INSERT INTO system_settings (key, value) VALUES ('stats_reset_time', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(ts),)
        )
        await db.commit()
        await db.execute("VACUUM")
    logger.info("All trade statistics, signals, and trade_history reset at timestamp %d", ts)
    return {"reset_time": ts, "status": "ok"}


async def save_trade_record(t: dict) -> int:
    """
    Сохраняет или обновляет детальную запись закрытой сделки в таблице trade_history
    и синхронизирует её в broker_deals для обратной совместимости.
    """
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        pos_id = int(t.get("position_id") or t.get("ticket") or 0)
        close_p = float(t.get("close_price") or t.get("price") or 0.0)
        entry_p = t.get("entry_price")
        entry_val = float(entry_p) if (entry_p is not None and float(entry_p) > 0) else None
        trade_lot = float(t.get("lot") or t.get("volume") or 0.01)
        direction = str(t.get("direction") or t.get("type") or "BUY").upper()
        dir_clean = "BUY" if "BUY" in direction or "LONG" in direction else "SELL"
        symbol = str(t.get("symbol") or t.get("broker_symbol") or "XAUUSD").upper()
        broker_symbol = str(t.get("broker_symbol") or symbol).upper()
        rec_type = str(t.get("record_type") or "LIVE").upper()
        net_profit = float(t.get("net_pnl") if t.get("net_pnl") is not None else (t.get("profit_usd") or t.get("profit") or 0.0))

        # Нормализация времени, длительности и торговой сессии
        open_time = t.get("open_time")
        if isinstance(open_time, (int, float)) and open_time > 0:
            open_time_iso = datetime.fromtimestamp(open_time, tz=timezone.utc).isoformat()
        elif isinstance(open_time, datetime):
            open_time_iso = open_time.isoformat()
        elif isinstance(open_time, str):
            open_time_iso = open_time
        else:
            open_time_iso = None

        close_time = t.get("close_time") or now_iso
        if isinstance(close_time, (int, float)) and close_time > 0:
            close_time_iso = datetime.fromtimestamp(close_time, tz=timezone.utc).isoformat()
        elif isinstance(close_time, datetime):
            close_time_iso = close_time.isoformat()
        elif isinstance(close_time, str):
            close_time_iso = close_time
        else:
            close_time_iso = now_iso

        dur_sec, dur_str = calculate_duration(open_time_iso, close_time_iso)
        t_session = t.get("trading_session") or detect_trading_session(open_time_iso or close_time_iso)

        # MAE / MFE
        mae = t.get("mae")
        mfe = t.get("mfe")
        mae_pips = t.get("mae_pips")
        mfe_pips = t.get("mfe_pips")
        mae_mfe_status = t.get("mae_mfe_status") or ("AVAILABLE" if mae is not None else "UNAVAILABLE: requires historical tick/bar data")

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(
                """INSERT INTO trade_history (
                    record_type, position_id, order_id, close_order_id,
                    deal_in_ticket, deal_out_ticket, broker_ticket, signal_id,
                    broker_symbol, symbol, direction, lot,
                    entry_price, close_price, initial_sl, final_sl, initial_tp, final_tp,
                    signal_time, open_time, close_time, duration_seconds, duration_str,
                    entry_type, strategy, setup_version, trading_session,
                    balance_before, equity_before, risk_usd, risk_pct, planned_rr, actual_r,
                    gross_profit, commission, swap, slippage, slippage_pts,
                    net_pnl, pnl_pips, result,
                    mae, mfe, mae_pips, mfe_pips, mae_mfe_status,
                    comment, account, raw_telemetry, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec_type, pos_id, t.get("order_id"), t.get("close_order_id"),
                    t.get("deal_in_ticket"), t.get("deal_out_ticket") or pos_id, t.get("broker_ticket") or pos_id, t.get("signal_id"),
                    broker_symbol, symbol, dir_clean, trade_lot,
                    entry_val, close_p, t.get("initial_sl"), t.get("final_sl"), t.get("initial_tp"), t.get("final_tp"),
                    t.get("signal_time"), open_time_iso, close_time_iso, dur_sec, dur_str,
                    t.get("entry_type") or "AUTO", t.get("strategy") or "ICT_SMC", t.get("setup_version") or "v1.0", t_session,
                    t.get("balance_before"), t.get("equity_before"), t.get("risk_usd"), t.get("risk_pct"), t.get("planned_rr"), t.get("actual_r"),
                    t.get("gross_profit"), t.get("commission"), t.get("swap"), t.get("slippage"), t.get("slippage_pts"),
                    net_profit, t.get("pnl_pips") or 0.0, t.get("result") or "CLOSED",
                    mae, mfe, mae_pips, mfe_pips, mae_mfe_status,
                    t.get("comment") or "", t.get("account") or "", t.get("raw_telemetry") or "", now_iso
                )
            )
            inserted_id = cursor.lastrowid

            # Синхронизируем broker_deals
            try:
                close_time_int = int(datetime.fromisoformat(close_time_iso.replace("Z", "+00:00")).timestamp())
            except Exception:
                close_time_int = int(datetime.now(timezone.utc).timestamp())
            await db.execute(
                """INSERT INTO broker_deals (
                    ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at,
                    entry_price, close_price, position_id, order_id, gross_profit, commission, swap, slippage, record_type, signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket) DO UPDATE SET
                    entry_price = excluded.entry_price,
                    close_price = excluded.close_price,
                    position_id = excluded.position_id,
                    profit_usd = excluded.profit_usd,
                    gross_profit = excluded.gross_profit,
                    commission = excluded.commission,
                    swap = excluded.swap,
                    record_type = excluded.record_type,
                    comment = excluded.comment""",
                (
                    pos_id, symbol, dir_clean, trade_lot, close_p, net_profit, close_time_int,
                    t.get("magic") or 888001, t.get("comment") or "", now_iso,
                    entry_val, close_p, pos_id, t.get("order_id"),
                    t.get("gross_profit"), t.get("commission"), t.get("swap"), t.get("slippage"),
                    rec_type, t.get("signal_id")
                )
            )
            await db.commit()
            return inserted_id
    except Exception as e:
        logger.error("save_trade_record error: %s", e)
        return 0


async def get_trade_history(
    record_type: str = "LIVE",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    direction: Optional[str] = None,
    strategy: Optional[str] = None,
    session: Optional[str] = None,
    result: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    start_time: Optional[Any] = None,
    end_time: Optional[Any] = None,
    trading_session: Optional[str] = None,
) -> Tuple[List[dict], int]:
    """
    Возвращает список подробных записей сделок из trade_history с гибкой фильтрацией:
    - record_type: 'LIVE', 'DEMO', 'TEST_SIGNAL', 'BACKTEST', или 'ALL'
    - date_from, date_to (или start_time, end_time): диапазон дат закрытия
    - direction: 'BUY', 'SELL'
    - strategy: название стратегии
    - session (или trading_session): торговая сессия
    - result: результат (TP, SL, BREAKEVEN, MANUAL_CLOSE, PARTIAL_CLOSE)
    - symbol: валютная пара
    Возвращает (trades: list[dict], total_count: int).
    """
    try:
        if start_time and not date_from:
            date_from = str(start_time)
        if end_time and not date_to:
            date_to = str(end_time)
        if trading_session and not session:
            session = trading_session

        conditions = []
        params = []

        if record_type and record_type.upper() != "ALL":
            rec_u = record_type.upper()
            if rec_u == "LIVE":
                conditions.append("(record_type = 'LIVE' OR record_type IS NULL)")
            else:
                conditions.append("record_type = ?")
                params.append(rec_u)

        if date_from:
            clean_df = date_from[:10]
            conditions.append("date(close_time) >= date(?)")
            params.append(clean_df)

        if date_to:
            clean_dt = date_to[:10]
            conditions.append("date(close_time) <= date(?)")
            params.append(clean_dt)

        if direction and direction.upper() != "ALL":
            d_clean = "BUY" if "BUY" in direction.upper() or "LONG" in direction.upper() else "SELL"
            conditions.append("direction = ?")
            params.append(d_clean)

        if strategy and strategy.upper() != "ALL":
            conditions.append("strategy LIKE ?")
            params.append(f"%{strategy}%")

        if session and session.upper() != "ALL":
            conditions.append("trading_session LIKE ?")
            params.append(f"%{session}%")

        if result and result.upper() != "ALL":
            res_u = result.upper()
            if res_u in ("TP", "WIN", "PROFIT"):
                conditions.append("(result LIKE '%TP%' OR net_pnl > 0)")
            elif res_u in ("SL", "LOSS"):
                conditions.append("(result LIKE '%SL%' OR (net_pnl < 0 AND result NOT LIKE '%BREAKEVEN%'))")
            elif res_u in ("BE", "BREAKEVEN"):
                conditions.append("(result LIKE '%BREAKEVEN%' OR result LIKE '%BE%')")
            else:
                conditions.append("result LIKE ?")
                params.append(f"%{result}%")

        if symbol and symbol.upper() != "ALL":
            conditions.append("symbol LIKE ?")
            params.append(f"%{symbol.upper()}%")

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row

            count_query = f"SELECT COUNT(*) FROM trade_history {where_clause}"
            cursor = await db.execute(count_query, params)
            total_count = (await cursor.fetchone())[0]

            select_query = f"""
                SELECT * FROM trade_history 
                {where_clause} 
                ORDER BY close_time DESC, id DESC 
                LIMIT ? OFFSET ?
            """
            cursor = await db.execute(select_query, params + [limit, offset])
            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total_count
    except Exception as e:
        logger.error("get_trade_history error: %s", e)
        return [], 0


async def get_trade_by_id(trade_id: int) -> Optional[dict]:
    """Возвращает подробную карточку сделки по ее первичному ключу ID в trade_history."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM trade_history WHERE id = ?", (trade_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("get_trade_by_id error: %s", e)
        return None


async def get_trade_by_position_id(position_id: int) -> Optional[dict]:
    """Возвращает сделку по тикету позиции MT5."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trade_history WHERE position_id = ? OR broker_ticket = ? ORDER BY id DESC LIMIT 1",
                (position_id, position_id)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("get_trade_by_position_id error: %s", e)
        return None


async def sync_broker_deals(deals: list[dict]) -> list[dict]:
    """
    Синхронизирует реальные закрытые сделки терминала MetaTrader 5:
    - Надёжно связывает вход и выход позиции по position_id/ticket
    - Вычисляет издержки (комиссия, своп, проскальзывание)
    - Рассчитывает R-multiple, MAE/MFE и длительность
    - Сохраняет подробную карточку в trade_history
    - Возвращает список НОВЫХ закрытых сделок
    """
    if not deals:
        return []
    try:
        new_deals = []
        reset_ts = await get_stats_reset_time()
        telemetry = {}
        try:
            from trading.execution_bridge import bridge_manager
            telemetry = bridge_manager.mt5_telemetry or {}
        except Exception:
            telemetry = {}

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row
            now_iso = datetime.now(timezone.utc).isoformat()
            for d in deals:
                ticket = int(d.get("ticket") or 0)
                if ticket <= 0:
                    continue
                close_time_int = int(d.get("time") or d.get("close_time") or 0)
                if reset_ts and close_time_int < reset_ts:
                    continue

                cursor = await db.execute("SELECT ticket FROM broker_deals WHERE ticket = ?", (ticket,))
                already_exists = await cursor.fetchone()

                pos_id = int(d.get("position_id") or d.get("pos_id") or ticket)
                order_id = int(d.get("order_id") or 0)
                close_order_id = int(d.get("close_order_id") or order_id)
                open_order_id = int(d.get("open_order_id") or order_id)
                deal_in_ticket = int(d.get("deal_in_ticket") or d.get("in_ticket") or 0)
                sym = str(d.get("symbol", "")).upper()
                broker_sym = str(d.get("broker_symbol") or sym)
                deal_type = str(d.get("type", "BUY")).upper()
                dir_clean = "BUY" if "BUY" in deal_type or "LONG" in deal_type else "SELL"
                lot = float(d.get("lot") or d.get("volume") or 0.01)
                close_p = float(d.get("close_price") or d.get("price") or 0.0)
                profit = float(d.get("profit") or 0.0)
                gross_profit = d.get("gross_profit")
                commission = d.get("commission")
                swap = d.get("swap")
                slippage = d.get("slippage")
                magic = int(d.get("magic") or 0)
                comment = str(d.get("comment") or "")
                rec_type = str(d.get("record_type") or "LIVE").upper()
                sig_id = int(d.get("signal_id") or 0)

                # ── Определение цены входа по НАДЁЖНОЙ СВЯЗИ с позицией ──
                entry_p = d.get("entry_price")
                if entry_p is not None:
                    try:
                        entry_p = float(entry_p)
                        if entry_p <= 0.0:
                            entry_p = None
                    except (ValueError, TypeError):
                        entry_p = None

                # 1. Поиск связанного сигнала по точному тикету позиции/ордера или signal_id
                sig_match = None
                if sig_id > 0:
                    c_sig = await db.execute("SELECT * FROM signals WHERE id = ?", (sig_id,))
                    sig_row = await c_sig.fetchone()
                    if sig_row:
                        sig_match = dict(sig_row)
                elif pos_id > 0:
                    c_sig = await db.execute(
                        "SELECT * FROM signals WHERE broker_ticket = ? OR position_id = ? ORDER BY id DESC LIMIT 1",
                        (pos_id, pos_id)
                    )
                    sig_row = await c_sig.fetchone()
                    if sig_row:
                        sig_match = dict(sig_row)

                if (entry_p is None or entry_p <= 0.0) and sig_match:
                    entry_p = float(sig_match.get("entry_price") or 0.0) or None

                # 2. Если сигнала нет, ищем в trade_history по position_id
                if (entry_p is None or entry_p <= 0.0) and pos_id > 0:
                    c_th = await db.execute(
                        "SELECT entry_price FROM trade_history WHERE position_id = ? AND entry_price IS NOT NULL LIMIT 1",
                        (pos_id,)
                    )
                    th_row = await c_th.fetchone()
                    if th_row and th_row[0]:
                        entry_p = float(th_row[0])

                # Важное правило: если точная цена входа неизвестна, НЕ подменяем entry_price = close_price!
                # entry_p остается None, чтобы в отчетах честно отображалось N/A

                # Финансовый расчет
                costs = calculate_trade_costs(
                    gross_profit=gross_profit,
                    commission=commission,
                    swap=swap,
                    slippage=slippage,
                    reported_profit=profit,
                )
                net_profit = costs["net_pnl"]

                # Пункты (pips)
                pip_mult = 100.0 if "JPY" in sym else (10.0 if "XAU" in sym else 10000.0)
                if entry_p and entry_p > 0 and close_p > 0:
                    raw_pips = (close_p - entry_p) * pip_mult if dir_clean == "BUY" else (entry_p - close_p) * pip_mult
                    pnl_pips = round(raw_pips, 1)
                else:
                    pnl_pips = 0.0

                # Время и длительность
                close_time_iso = (
                    datetime.fromtimestamp(close_time_int, tz=timezone.utc).isoformat()
                    if close_time_int > 0 else now_iso
                )
                open_time_val = d.get("open_time")
                if open_time_val and isinstance(open_time_val, (int, float)) and open_time_val > 0:
                    open_time_iso = datetime.fromtimestamp(open_time_val, tz=timezone.utc).isoformat()
                elif open_time_val and isinstance(open_time_val, str):
                    open_time_iso = open_time_val
                elif sig_match:
                    open_time_iso = sig_match.get("activated_at") or sig_match.get("created_at") or close_time_iso
                else:
                    open_time_iso = None

                dur_sec, dur_str = calculate_duration(open_time_iso, close_time_iso)
                t_session = detect_trading_session(open_time_iso or close_time_iso)

                # Результат (TP, SL, BREAKEVEN, MANUAL_CLOSE, PARTIAL)
                reason_u = str(comment or "").upper()
                if "PARTIAL" in reason_u:
                    res_status = reason_u if ("TP" in reason_u or "SL" in reason_u) else "PARTIAL_CLOSE"
                elif "MANUAL" in reason_u:
                    res_status = "MANUAL_CLOSE"
                elif "BREAKEVEN" in reason_u or (net_profit >= -0.50 and abs(pnl_pips) <= 3.5 and "TP" not in reason_u):
                    res_status = "BREAKEVEN"
                elif net_profit > 0:
                    res_status = "TP1_HIT"
                elif net_profit < 0:
                    res_status = "SL_HIT"
                else:
                    res_status = "BREAKEVEN"

                # R-Multiple
                init_sl = float(sig_match.get("initial_sl") or sig_match.get("stop_loss") or 0.0) if sig_match else None
                final_sl = float(sig_match.get("stop_loss") or 0.0) if sig_match else None
                init_tp = float(sig_match.get("initial_tp") or sig_match.get("take_profit_1") or 0.0) if sig_match else None
                final_tp = float(sig_match.get("take_profit_1") or 0.0) if sig_match else None
                planned_rr = float(sig_match.get("planned_rr") or sig_match.get("risk_reward") or 0.0) if sig_match else None
                if planned_rr is None and entry_p and init_sl and init_tp:
                    planned_rr = calculate_planned_rr(entry_p, init_sl, init_tp)

                risk_usd = float(sig_match.get("risk_usd") or 0.0) if sig_match else None
                risk_pct = float(sig_match.get("risk_pct") or 0.0) if sig_match else None
                actual_r = calculate_actual_r(
                    entry=entry_p,
                    close=close_p,
                    initial_sl=init_sl,
                    direction=dir_clean,
                    net_pnl=net_profit,
                    risk_usd=risk_usd,
                )

                # MAE / MFE
                mae_res = calculate_mae_mfe(
                    symbol=sym,
                    direction=dir_clean,
                    entry_price=entry_p if entry_p else close_p,
                    open_time=open_time_iso,
                    close_time=close_time_iso,
                    lot=lot,
                )

                strat = (sig_match.get("strategy") or sig_match.get("strategies_agreed")) if sig_match else "ICT_SMC"
                s_ver = sig_match.get("setup_version") if sig_match else "v1.0"

                # 1. Запись в broker_deals (с сохранением реальных entry_price и close_price)
                await db.execute(
                    """INSERT INTO broker_deals (
                        ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at,
                        entry_price, close_price, position_id, order_id, gross_profit, commission, swap, slippage, record_type, signal_id, open_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticket) DO UPDATE SET
                        entry_price = COALESCE(excluded.entry_price, broker_deals.entry_price),
                        close_price = excluded.close_price,
                        position_id = excluded.position_id,
                        lot = excluded.lot,
                        profit_usd = excluded.profit_usd,
                        gross_profit = excluded.gross_profit,
                        commission = excluded.commission,
                        swap = excluded.swap,
                        slippage = excluded.slippage,
                        record_type = excluded.record_type,
                        price = excluded.price,
                        comment = excluded.comment""",
                    (
                        ticket, sym, deal_type, lot, close_p, net_profit, close_time_int, magic, comment, now_iso,
                        entry_p, close_p, pos_id, open_order_id, costs["gross_profit"], costs["commission"], costs["swap"], costs["slippage"],
                        rec_type, sig_match.get("id") if sig_match else None,
                        int(datetime.fromisoformat(open_time_iso.replace("Z", "+00:00")).timestamp()) if open_time_iso else 0
                    ),
                )

                # 2. Запись в trade_history (полное досье)
                await db.execute(
                    """INSERT INTO trade_history (
                        record_type, position_id, order_id, close_order_id,
                        deal_in_ticket, deal_out_ticket, broker_ticket, signal_id,
                        broker_symbol, symbol, direction, lot,
                        entry_price, close_price, initial_sl, final_sl, initial_tp, final_tp,
                        signal_time, open_time, close_time, duration_seconds, duration_str,
                        entry_type, strategy, setup_version, trading_session,
                        balance_before, equity_before, risk_usd, risk_pct, planned_rr, actual_r,
                        gross_profit, commission, swap, slippage, slippage_pts,
                        net_pnl, pnl_pips, result,
                        mae, mfe, mae_pips, mfe_pips, mae_mfe_status,
                        comment, account, raw_telemetry, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec_type, pos_id, open_order_id, close_order_id,
                        deal_in_ticket, ticket, pos_id, sig_match.get("id") if sig_match else None,
                        broker_sym, sym, dir_clean, lot,
                        entry_p, close_p, init_sl, final_sl, init_tp, final_tp,
                        sig_match.get("created_at") if sig_match else None, open_time_iso, close_time_iso, dur_sec, dur_str,
                        sig_match.get("order_type") if sig_match else "AUTO", strat, s_ver, t_session,
                        sig_match.get("balance_before") if sig_match else None, sig_match.get("equity_before") if sig_match else None,
                        risk_usd, risk_pct, planned_rr, actual_r,
                        costs["gross_profit"], costs["commission"], costs["swap"], costs["slippage"], None,
                        net_profit, pnl_pips, res_status,
                        mae_res["mae"], mae_res["mfe"], mae_res["mae_pips"], mae_res["mfe_pips"], mae_res["status"],
                        comment, d.get("account") or (telemetry.get("account") if telemetry else "") or "", json.dumps(d), now_iso
                    )
                )

                # 3. Закрытие исходного сигнала в signals при наличии связи
                if sig_match and sig_match.get("id"):
                    await db.execute(
                        """UPDATE signals
                           SET status = ?, close_price = ?, pnl_pips = ?, closed_at = ?, result = ?,
                               actual_r = ?, gross_profit = ?, commission = ?, swap = ?, duration_seconds = ?
                           WHERE id = ?""",
                        (res_status, close_p, pnl_pips, now_iso, f"MT5: {net_profit:+.2f}$", actual_r,
                         costs["gross_profit"], costs["commission"], costs["swap"], dur_sec, sig_match["id"])
                    )

                if not already_exists:
                    new_deals.append({
                        "ticket": ticket,
                        "position_id": pos_id,
                        "symbol": sym,
                        "broker_symbol": broker_sym,
                        "type": deal_type,
                        "direction": dir_clean,
                        "lot": lot,
                        "entry_price": entry_p,
                        "close_price": close_p,
                        "price": close_p,
                        "profit": net_profit,
                        "net_pnl": net_profit,
                        "gross_profit": costs["gross_profit"],
                        "commission": costs["commission"],
                        "swap": costs["swap"],
                        "actual_r": actual_r,
                        "planned_rr": planned_rr,
                        "duration_str": dur_str,
                        "trading_session": t_session,
                        "comment": comment
                    })

            await db.commit()
        return new_deals
    except Exception as e:
        logger.error("sync_broker_deals error: %s", e)
        return []


async def get_recent_signals(limit: int = 20) -> list[dict]:
    """
    Возвращает реальные сделки и ордера брокера MT5 для истории.
    Для закрытых сделок гарантирует точные фактические цены входа (entry_price)
    и выхода (close_price), не подменяя их одним значением и не угадывая позицию по символу.
    """
    try:
        from trading.execution_bridge import bridge_manager
        telemetry = bridge_manager.mt5_telemetry

        results = []

        # 1. Открытые позиции в рынке
        for p in (telemetry.get("positions") or []):
            results.append({
                "ticket": p.get("ticket", 0),
                "position_id": p.get("ticket", 0),
                "symbol": p.get("symbol", ""),
                "direction": p.get("type", "BUY"),
                "order_type": "MARKET",
                "tag_emoji": "🚀",
                "status": "ACTIVE",
                "entry_price": p.get("price", 0.0),
                "close_price": None,
                "profit_usd": p.get("profit", 0.0),
                "net_pnl": p.get("profit", 0.0),
                "lot": p.get("lot", 0.01),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        # 2. Отложенные ордера в стакане
        for o in (telemetry.get("orders") or []):
            results.append({
                "ticket": o.get("ticket", 0),
                "position_id": o.get("ticket", 0),
                "symbol": o.get("symbol", ""),
                "direction": "BUY" if "BUY" in str(o.get("type", "")) else "SELL",
                "order_type": o.get("type", "LIMIT"),
                "tag_emoji": "⏳",
                "status": "PENDING",
                "entry_price": o.get("price", 0.0),
                "close_price": None,
                "profit_usd": 0.0,
                "net_pnl": 0.0,
                "lot": o.get("lot", 0.01),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        # 3. Закрытые сделки из trade_history (или broker_deals)
        reset_ts = await get_stats_reset_time()
        time_cond = f"WHERE close_time >= {reset_ts}" if reset_ts else ""

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            db.row_factory = aiosqlite.Row

            # Первоочередной источник — подробная таблица trade_history
            cursor = await db.execute(
                f"""SELECT * FROM trade_history 
                    {f"WHERE strftime('%s', close_time) >= '{reset_ts}'" if reset_ts else ""}
                    ORDER BY close_time DESC, id DESC LIMIT ?""",
                (limit,)
            )
            th_rows = await cursor.fetchall()
            if th_rows:
                for r in th_rows:
                    t = dict(r)
                    pnl = t.get("net_pnl", 0.0)
                    res_val = t.get("result", "")
                    if "TP" in res_val or pnl > 0:
                        status = "TP1_HIT"
                    elif "SL" in res_val or pnl < 0:
                        status = "SL_HIT"
                    else:
                        status = "BREAKEVEN"

                    results.append({
                        "id": t.get("id"),
                        "ticket": t.get("deal_out_ticket") or t.get("position_id") or t.get("broker_ticket") or 0,
                        "position_id": t.get("position_id") or 0,
                        "symbol": t.get("symbol") or t.get("broker_symbol") or "",
                        "direction": t.get("direction", "BUY"),
                        "order_type": t.get("entry_type") or "MARKET",
                        "tag_emoji": "✅" if pnl > 0 else ("🛡" if pnl == 0 else "🛑"),
                        "status": status,
                        "entry_price": t.get("entry_price"),
                        "close_price": t.get("close_price"),
                        "profit_usd": pnl,
                        "net_pnl": pnl,
                        "gross_profit": t.get("gross_profit"),
                        "commission": t.get("commission"),
                        "swap": t.get("swap"),
                        "actual_r": t.get("actual_r"),
                        "planned_rr": t.get("planned_rr"),
                        "lot": t.get("lot", 0.01),
                        "created_at": t.get("close_time") or t.get("created_at"),
                        "result": t.get("comment") or res_val,
                        "record_type": t.get("record_type", "LIVE"),
                        "duration_str": t.get("duration_str", "N/A"),
                        "trading_session": t.get("trading_session", "Unknown"),
                    })
            else:
                # Fallback на broker_deals со строгим разделением entry_price и close_price
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

                    ticket = d.get("ticket", 0)
                    pos_id = d.get("position_id") or ticket
                    close_p = d.get("close_price") or d.get("price", 0.0)
                    entry_p = d.get("entry_price")

                    # Если entry_price отсутствует, ищем ТОЛЬКО по надёжной связи
                    if (entry_p is None or entry_p == 0.0) and pos_id > 0:
                        cur_sig = await db.execute(
                            "SELECT entry_price FROM signals WHERE broker_ticket = ? OR position_id = ? ORDER BY id DESC LIMIT 1",
                            (pos_id, pos_id)
                        )
                        sig_row = await cur_sig.fetchone()
                        if sig_row and sig_row[0]:
                            entry_p = float(sig_row[0])
                        else:
                            cur_th = await db.execute(
                                "SELECT entry_price FROM trade_history WHERE position_id = ? OR broker_ticket = ? ORDER BY id DESC LIMIT 1",
                                (pos_id, pos_id)
                            )
                            th_row = await cur_th.fetchone()
                            if th_row and th_row[0]:
                                entry_p = float(th_row[0])
                            else:
                                # НЕ подменяем close_price! Оставляем None для честного N/A
                                entry_p = None

                    results.append({
                        "ticket": ticket,
                        "position_id": pos_id,
                        "symbol": d.get("symbol", ""),
                        "direction": d.get("deal_type", "BUY"),
                        "order_type": "MARKET",
                        "tag_emoji": "✅" if pnl > 0 else ("🛡" if pnl == 0 else "🛑"),
                        "status": status,
                        "entry_price": entry_p,
                        "close_price": close_p,
                        "profit_usd": pnl,
                        "net_pnl": pnl,
                        "lot": d.get("lot", 0.01),
                        "created_at": d.get("created_at"),
                        "result": d.get("comment") or "",
                    })

        return results[:limit]
    except Exception as e:
        logger.error("get_recent_signals error: %s", e)
        return []


async def get_stats(record_type: str = "LIVE") -> dict:
    """
    Возвращает честную статистику торговли по закрытым сделкам брокера MT5.
    Статистика строго сегментирована по типу записи (по умолчанию только LIVE сделки).
    Демо-сделки, тестовые сигналы и бэктесты никогда не смешиваются с реальной статистикой.
    """
    try:
        from trading.execution_bridge import bridge_manager
        is_online, ping = bridge_manager.is_mt5_online()
        telemetry = bridge_manager.mt5_telemetry

        open_positions = telemetry.get("positions") or []
        pending_orders = telemetry.get("orders") or []

        reset_ts = await get_stats_reset_time()

        # Построение условий фильтрации по типу записи и времени сброса
        conds = []
        params = []
        sig_rec_cond = ""
        sig_params = []
        if record_type and record_type.upper() != "ALL":
            rec_u = record_type.upper()
            if rec_u == "LIVE":
                conds.append("(record_type = 'LIVE' OR record_type IS NULL)")
                sig_rec_cond = "AND (record_type = 'LIVE' OR record_type IS NULL)"
            else:
                conds.append("record_type = ?")
                params.append(rec_u)
                sig_rec_cond = "AND record_type = ?"
                sig_params.append(rec_u)
        if reset_ts:
            conds.append("close_time >= ?")
            params.append(reset_ts)

        where_clause = ("WHERE " + " AND ".join(conds)) if conds else ""

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(f"SELECT COUNT(*) FROM broker_deals {where_clause}", params)
            deals_count = (await cursor.fetchone())[0]

            if deals_count > 0:
                win_cond = (" AND " if where_clause else "WHERE ") + "profit_usd > 0"
                cursor = await db.execute(f"SELECT COUNT(*) FROM broker_deals {where_clause} {win_cond}", params)
                wins = (await cursor.fetchone())[0]

                loss_cond = (" AND " if where_clause else "WHERE ") + "profit_usd < 0"
                cursor = await db.execute(f"SELECT COUNT(*) FROM broker_deals {where_clause} {loss_cond}", params)
                losses = (await cursor.fetchone())[0]

                be_cond = (" AND " if where_clause else "WHERE ") + "profit_usd == 0"
                cursor = await db.execute(f"SELECT COUNT(*) FROM broker_deals {where_clause} {be_cond}", params)
                breakevens = (await cursor.fetchone())[0]

                cursor = await db.execute(f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {where_clause}", params)
                total_profit_usd = (await cursor.fetchone())[0]

                # Win Rate рассчитывается как доля безубыточных и прибыльных сделок
                win_rate = ((deals_count - losses) / deals_count * 100) if deals_count > 0 else 0.0

                # Разбивка по символам
                cursor = await db.execute(
                    f"""SELECT symbol, COUNT(*) as cnt,
                               SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                               SUM(profit_usd) as pnl
                        FROM broker_deals {where_clause} GROUP BY symbol ORDER BY pnl DESC""",
                    params
                )
                by_symbol = {}
                for row in await cursor.fetchall():
                    s, cnt, w, pnl = row
                    by_symbol[s] = {"total": cnt, "wins": w, "profit_usd": round(pnl, 2)}

                # Разбивка по направлениям
                cursor = await db.execute(
                    f"""SELECT deal_type, COUNT(*) as cnt,
                               SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as w,
                               SUM(profit_usd) as pnl
                        FROM broker_deals {where_clause} GROUP BY deal_type""",
                    params
                )
                by_direction = {}
                for row in await cursor.fetchall():
                    d, cnt, w, pnl = row
                    by_direction[d] = {"total": cnt, "wins": w, "profit_usd": round(pnl, 2)}

                # Средний плановый R:R и фактический R
                cursor = await db.execute(
                    f"""SELECT AVG(CASE WHEN planned_rr > 0 THEN planned_rr WHEN risk_reward > 0 THEN risk_reward ELSE 2.0 END),
                               AVG(actual_r)
                        FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'CLOSED', 'BREAKEVEN', 'CLOSED_BE')
                        {sig_rec_cond}""",
                    sig_params
                )
                rr_row = await cursor.fetchone()
                avg_rr = round(float(rr_row[0]), 2) if rr_row and rr_row[0] else 2.0
                avg_actual_r = round(float(rr_row[1]), 2) if rr_row and rr_row[1] is not None else None

                # Реальные пункты
                cursor = await db.execute(
                    f"""SELECT COALESCE(SUM(pnl_pips), 0.0) 
                        FROM signals WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'CLOSED', 'BREAKEVEN', 'CLOSED_BE')
                        {sig_rec_cond}""",
                    sig_params
                )
                pips_row = await cursor.fetchone()
                total_pips = round(float(pips_row[0]), 1) if pips_row and pips_row[0] else 0.0

                # Валовая прибыль и убыток
                cursor = await db.execute(f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {where_clause} {win_cond}", params)
                gross_profit = (await cursor.fetchone())[0]
                cursor = await db.execute(f"SELECT COALESCE(SUM(profit_usd), 0.0) FROM broker_deals {where_clause} {loss_cond}", params)
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
                    "avg_actual_r": avg_actual_r,
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
                    "record_type": record_type,
                }
            else:
                return {
                    "total": 0, "open": len(open_positions), "closed": 0,
                    "wins": 0, "losses": 0, "expired": 0, "breakevens": 0,
                    "win_rate": 0.0, "total_profit_usd": 0.0, "total_pips": 0.0, "avg_rr": 0.0,
                    "avg_actual_r": None,
                    "by_direction": {}, "by_symbol": {},
                    "balance": telemetry.get("balance", 0.0), "equity": telemetry.get("equity", 0.0),
                    "broker": telemetry.get("broker", "—"), "account": telemetry.get("account", "—"),
                    "mt5_online": is_online,
                    "open_positions": open_positions, "pending_orders": pending_orders,
                    "record_type": record_type,
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
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute("SELECT value FROM system_settings WHERE key = 'drawdown_reset_time'")
            row = await cursor.fetchone()
            return row[0] if row else None
    except Exception:
        return None


async def set_drawdown_reset_now() -> bool:
    """Сбрасывает таймер просадки на текущий момент."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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


async def get_consecutive_sl_count(max_lookback_hours: float = 12.0, record_type: str = "LIVE") -> int:
    """
    Считает количество последовательных Stop Loss среди последних закрытых сигналов.
    Автоматически сбрасывается, если последний SL был закрыт более max_lookback_hours назад,
    или если администратор выполнил сброс просадки.
    Фильтруется по record_type (по умолчанию 'LIVE'), исключая взаимное влияние с DEMO/TEST/BACKTEST.
    """
    try:
        reset_time_str = await get_drawdown_reset_time()
        reset_dt = datetime.fromisoformat(reset_time_str) if reset_time_str else None
        if reset_dt and reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)

        cond = ""
        params = []
        if record_type and record_type.upper() != "ALL":
            if record_type.upper() == "LIVE":
                cond = "AND (record_type = 'LIVE' OR record_type IS NULL)"
            else:
                cond = "AND record_type = ?"
                params.append(record_type.upper())

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(
                f"""SELECT status, closed_at FROM signals 
                   WHERE status IN ('TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BREAKEVEN', 'CLOSED_BE') {cond}
                   ORDER BY closed_at DESC LIMIT 10""",
                params
            )
            rows = await cursor.fetchall()
            sl_count = 0
            latest_sl_dt = None
            now = datetime.now(timezone.utc)
            for status, closed_at_str in rows:
                if not closed_at_str:
                    continue
                try:
                    closed_at = datetime.fromisoformat(closed_at_str)
                    if closed_at.tzinfo is None:
                        closed_at = closed_at.replace(tzinfo=timezone.utc)

                    # 1. Если сделка закрыта до последнего сброса просадки — стоп
                    if reset_dt and closed_at < reset_dt:
                        break
                except Exception:
                    closed_at = None

                if status == 'SL_HIT':
                    if latest_sl_dt is None and closed_at is not None:
                        latest_sl_dt = closed_at
                    sl_count += 1
                else:
                    break

            # Если получено 3 стопа подряд: проверяем, прошло ли 12 часов с момента 3-го (последнего) стопа
            if sl_count >= 3 and latest_sl_dt is not None:
                age_since_latest = (now - latest_sl_dt).total_seconds() / 3600.0
                if age_since_latest > max_lookback_hours:
                    # 12 часов паузы после 3-го стопа истекли — автоматически сбрасываем серию в 0
                    await set_drawdown_reset_now()
                    return 0
            return sl_count
    except Exception as e:
        logger.error("get_consecutive_sl_count error: %s", e)
        return 0


async def get_today_signal_count(record_type: str = "LIVE") -> int:
    """Считает количество реально исполненных/активных сигналов за сегодня по времени брокера (исключая отменённые MT5)."""
    try:
        from utils.broker_time import get_broker_today_midnight
        today_start_iso = get_broker_today_midnight().astimezone(timezone.utc).isoformat()

        cond = ""
        params = [today_start_iso]
        if record_type and record_type.upper() != "ALL":
            if record_type.upper() == "LIVE":
                cond = "AND (record_type = 'LIVE' OR record_type IS NULL)"
            else:
                cond = "AND record_type = ?"
                params.append(record_type.upper())

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            cursor = await db.execute(
                f"""SELECT COUNT(*) FROM signals 
                   WHERE created_at >= ? 
                   AND status NOT IN ('CANCELLED_BY_MT5', 'CANCELLED_UNCONFIRMED', 'EXPIRED') {cond}""",
                params,
            )
            count = (await cursor.fetchone())[0]
            return count
    except Exception as e:
        logger.error("get_today_signal_count error: %s", e)
        return 0


async def get_last_signal_time_for_pair(symbol: str) -> Optional[datetime]:
    """Возвращает время последнего сигнала по данной паре (для cooldown). Персистентный."""
    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
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



async def get_today_broker_pnl(record_type: Optional[str] = "LIVE") -> dict:
    """
    Возвращает статистику закрытых сделок брокера за текущий торговый день MT5 (с 00:00:00 EET брокера):
    - total_pnl: суммарный закрытый PnL в USD
    - profit_sum: сумма прибыльных сделок
    - loss_sum: сумма убыточных сделок
    - deals_count: количество закрытых сделок за сегодня
    - wins: кол-во прибыльных
    - losses: кол-во убыточных
    """
    try:
        from utils.broker_time import get_broker_today_midnight_ts
        today_ts = get_broker_today_midnight_ts()

        async with aiosqlite.connect(str(DB_PATH), timeout=30.0) as db:
            query = """SELECT 
                    COALESCE(SUM(profit_usd), 0.0) as total_pnl,
                    COALESCE(SUM(CASE WHEN profit_usd > 0 THEN profit_usd ELSE 0.0 END), 0.0) as profit_sum,
                    COALESCE(SUM(CASE WHEN profit_usd < 0 THEN profit_usd ELSE 0.0 END), 0.0) as loss_sum,
                    COUNT(*) as deals_count,
                    COALESCE(SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END), 0) as wins,
                    COALESCE(SUM(CASE WHEN profit_usd < 0 THEN 1 ELSE 0 END), 0) as losses
                FROM broker_deals 
                WHERE close_time >= ?"""
            params = [today_ts]
            if record_type:
                query += " AND (record_type = ? OR (record_type IS NULL AND ? = 'LIVE'))"
                params.extend([record_type, record_type])
            cursor = await db.execute(query, tuple(params))
            row = await cursor.fetchone()
            if row:
                return {
                    "total_pnl": float(row[0]),
                    "profit_sum": float(row[1]),
                    "loss_sum": float(row[2]),
                    "deals_count": int(row[3]),
                    "wins": int(row[4]),
                    "losses": int(row[5]),
                    "today_ts": today_ts
                }
    except Exception as e:
        logger.error("get_today_broker_pnl error: %s", e)
    return {
        "total_pnl": 0.0,
        "profit_sum": 0.0,
        "loss_sum": 0.0,
        "deals_count": 0,
        "wins": 0,
        "losses": 0,
        "today_ts": 0
    }
