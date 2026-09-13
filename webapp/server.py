"""
Smart Trader — MetaTrader 4/5 Auto-Trading Bridge Server.
Легковесный локальный асинхронный сервер на aiohttp.
Обслуживает:
1. Auto-Trading Bridge API для MetaTrader 4/5 (MQL4/MQL5)
2. JSON API для сигналов и статистики
"""

import asyncio
import json
import logging
from aiohttp import web
from datetime import datetime, timezone

import config
from db.database import (
    get_active_signals, get_pending_signals, get_recent_signals,
    get_stats, update_signal_status, update_signal_sl
)

logger = logging.getLogger(__name__)

# Очередь команд для MetaTrader советника
# id -> order_dict
_bridge_command_queue: list[dict] = []
_executed_orders_log: list[dict] = []
_bot_instance = None


async def _send_telegram_notification(text: str):
    """Рассылает уведомление об исполнении/закрытии в MT5 всем пользователям бота."""
    global _bot_instance
    if not _bot_instance:
        return
    try:
        from db.users import get_approved_user_ids
        recipients = await get_approved_user_ids()
        if config.ADMIN_ID and config.ADMIN_ID not in recipients:
            recipients.append(config.ADMIN_ID)
        for uid in recipients:
            try:
                await _bot_instance.send_message(uid, text, parse_mode="HTML")
            except Exception as err:
                logger.error("Failed to send bridge notification to %d: %s", uid, err)
    except Exception as e:
        logger.error("Error sending bridge notification: %s", e)


async def handle_status(request: web.Request) -> web.Response:
    """Возвращает статус локального моста MT5."""
    return web.json_response({
        "status": "online",
        "service": "Smart Trader Local MT5 Bridge",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


async def api_signals(request: web.Request) -> web.Response:
    """Возвращает активные и недавние сигналы в формате JSON."""
    try:
        active = await get_active_signals()
        pending = await get_pending_signals()
        recent = await get_recent_signals(limit=20)
        return web.json_response({
            "status": "ok",
            "active": active or [],
            "pending": pending or [],
            "recent": recent or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("api_signals error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def api_stats(request: web.Request) -> web.Response:
    """Возвращает общую статистику торговли."""
    try:
        stats = await get_stats()
        return web.json_response({
            "status": "ok",
            "stats": stats or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("api_stats error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def api_events(request: web.Request) -> web.Response:
    """Возвращает экономический календарь High-Impact событий."""
    try:
        from news.economic_calendar import EconomicCalendar
        calendar = EconomicCalendar(config.TIMEZONE)
        events = await calendar.get_events_for_display()
        events_json = []
        for e in events:
            events_json.append({
                "title": e.title,
                "country": e.country,
                "impact": e.impact,
                "date_str": e.date_str,
                "time_str": e.time_str,
                "forecast": e.forecast,
                "previous": e.previous,
                "affected_pairs": e.affected_pairs,
                "minutes_until": e.minutes_until,
            })
        return web.json_response({
            "status": "ok",
            "events": events_json,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("api_events error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


# ═══════════════════════════════════════════════════════════
# AUTO-TRADING BRIDGE ENDPOINTS (ДЛЯ METATRADER 4/5)
# ═══════════════════════════════════════════════════════════

async def bridge_get_orders(request: web.Request) -> web.Response:
    """
    Советник MT4/MT5 опрашивает этот эндпоинт раз в несколько секунд:
    GET /api/v1/bridge/orders
    Возвращает список сигналов, которые нужно открыть или модифицировать.
    """
    try:
        from datetime import datetime, timezone
        from trading.execution_bridge import bridge_manager
        
        # Обновляем телеметрию MT5 при каждом опросе (поддерживает POST JSON и GET Query)
        try:
            post_data = {}
            if request.method == "POST":
                try:
                    post_data = await request.json()
                except Exception:
                    try:
                        raw_text = await request.text()
                        clean_text = raw_text.strip().rstrip('\x00').strip()
                        if "}" in clean_text:
                            clean_text = clean_text[:clean_text.rfind("}") + 1]
                        post_data = json.loads(clean_text)
                    except Exception:
                        post_data = {}

            balance = float(post_data.get("balance") or request.query.get("balance") or request.headers.get("X-MT5-Balance") or 0.0)
            equity = float(post_data.get("equity") or request.query.get("equity") or request.headers.get("X-MT5-Equity") or 0.0)
            margin_free = float(post_data.get("margin_free") or request.query.get("margin_free") or request.headers.get("X-MT5-Margin-Free") or 0.0)
            broker = str(post_data.get("broker") or request.query.get("broker") or request.headers.get("X-MT5-Broker") or "")
            account = str(post_data.get("account") or request.query.get("account") or request.headers.get("X-MT5-Account") or "")
            
            positions = post_data.get("positions")
            orders_list = post_data.get("orders") or post_data.get("orders_list")
            quotes = post_data.get("quotes")
            history = post_data.get("history")

            # Fallback к GET query параметрам при необходимости
            import urllib.parse
            if positions is None and request.query.get("positions"):
                positions = json.loads(urllib.parse.unquote(request.query.get("positions")))
            if orders_list is None and request.query.get("orders_list"):
                orders_list = json.loads(urllib.parse.unquote(request.query.get("orders_list")))
            if quotes is None and (request.query.get("quotes") or request.headers.get("X-MT5-Quotes")):
                quotes_raw = request.query.get("quotes") or request.headers.get("X-MT5-Quotes")
                quotes = json.loads(urllib.parse.unquote(quotes_raw))

            if quotes:
                bridge_manager.update_quotes(quotes)

            # Синхронизация реальных закрытых сделок брокера в базу данных
            if history and isinstance(history, list) and len(history) > 0:
                try:
                    from db.database import sync_broker_deals
                    new_closed_deals = await sync_broker_deals(history)
                    if new_closed_deals:
                        for nd in new_closed_deals:
                            profit_val = float(nd.get("profit") or 0.0)
                            profit_sign = "+" if profit_val >= 0 else ""
                            if profit_val > 0:
                                emoji = "🏆"
                                title = "ТЕЙК-ПРОФИТ ВЗЯТ (METATRADER 5)!"
                            elif profit_val == 0:
                                emoji = "🛡"
                                title = "СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!"
                            else:
                                emoji = "🛑"
                                title = "СТОП-ЛОСС СРАБОТАЛ (METATRADER 5)!"

                            new_bal_text = f"\n💰 Баланс счёта: <code>{balance:.2f} USD</code>" if balance > 0 else ""
                            msg = (
                                f"{emoji} <b>{title}</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 <b>{nd['symbol']}</b> | <code>{nd['type']} {nd['lot']} lot</code>\n"
                                f"💵 Финансовый результат: <b>{profit_sign}{profit_val:.2f} USD</b>\n"
                                f"📍 Цена закрытия: <code>{nd['price']}</code>\n"
                                f"🎫 Билет сделки: <code>#{nd['ticket']}</code>"
                                f"{new_bal_text}\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"💼 <i>Сделка зафиксирована брокером. Статистика обновлена.</i>"
                            )
                            await _send_telegram_notification(msg)
                except Exception as hist_err:
                    logger.error("Failed to sync broker deals from EA: %s", hist_err)

            if balance > 0 or equity > 0 or broker or positions is not None or orders_list is not None:
                bridge_manager.update_telemetry(
                    balance=balance, equity=equity, margin_free=margin_free,
                    broker=broker, account=account,
                    positions=positions if positions is not None else [],
                    orders=orders_list if orders_list is not None else []
                )
            else:
                bridge_manager.mt5_telemetry["last_ping"] = datetime.now(timezone.utc)
        except Exception as tele_err:
            logger.debug("Telemetry parsing error: %s", tele_err)

        panic = bridge_manager.panic_close_requested
        if panic:
            bridge_manager.panic_close_requested = False
            logger.warning("Sending panic_close_all=True to MT5 terminal!")

        if not bridge_manager.enabled:
            return web.json_response({
                "status": "ok",
                "autotrade_enabled": False,
                "panic_close_all": panic,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "orders": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Берем активные и свежие ожидающие сигналы за последние 4 часа
        from db.database import DB_PATH
        import aiosqlite
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM signals 
                   WHERE status IN ('ACTIVE', 'OPEN', 'TP1_PARTIAL', 'PENDING')
                   AND datetime(created_at) >= datetime('now', '-4 hours')
                   ORDER BY id DESC"""
            )
            rows = await cursor.fetchall()
            active = [dict(r) for r in rows]

        orders = []
        for sig in (active or []):
            orders.append({
                "id": sig.get("id"),
                "symbol": sig.get("symbol"),
                "direction": sig.get("direction"), # LONG или SHORT
                "order_type": sig.get("order_type", "BUY_MARKET"),
                "entry": sig.get("entry_price"),
                "stop_loss": sig.get("stop_loss"),
                "tp1": sig.get("take_profit_1"),
                "tp2": sig.get("take_profit_2"),
                "breakeven_applied": bool(sig.get("breakeven_applied", 0)),
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "magic_number": 888001,
            })

        return web.json_response({
            "status": "ok",
            "autotrade_enabled": bridge_manager.enabled,
            "panic_close_all": panic,
            "lot": bridge_manager.default_lot,
            "risk_percent": bridge_manager.default_risk,
            "orders": orders,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("bridge_get_orders error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def bridge_post_report(request: web.Request) -> web.Response:
    """
    Советник MT4/MT5 сообщает боту о результате исполнения:
    POST /api/v1/bridge/report
    Body: {"symbol": "USDJPY", "action": "BUY", "price": 154.68, "profit": 0.0, "reason": "...", "signal_id": 140}
    """
    try:
        try:
            data = await request.json()
        except Exception:
            raw_text = await request.text()
            clean_text = raw_text.strip().rstrip('\x00').strip()
            if "}" in clean_text:
                clean_text = clean_text[:clean_text.rfind("}") + 1]
            data = json.loads(clean_text)
        logger.info("MetaTrader Bridge report received: %s", data)
        _executed_orders_log.append({
            **data,
            "received_at": datetime.now(timezone.utc).isoformat()
        })

        symbol = data.get("symbol", "")
        action = data.get("action", "").upper()
        price = float(data.get("price") or 0.0)
        profit = float(data.get("profit") or 0.0)
        reason = data.get("reason", "")
        sig_id = int(data.get("signal_id") or 0)

        from db.database import (
            confirm_signal_by_broker, reject_signal_by_broker, close_signal_by_broker
        )

        # 1. Ордер успешно открыт или выставлен лимит
        if action in ("BUY", "SELL", "BUY_LIMIT", "SELL_LIMIT", "OPENED"):
            await confirm_signal_by_broker(symbol, action, price, signal_id=sig_id)
            order_desc = "BUY (Покупка)" if "BUY" in action else "SELL (Продажа)"
            if "LIMIT" in action:
                msg = (
                    f"⏳ <b>ЛИМИТНЫЙ ОРДЕР ВЫСТАВЛЕН В MT5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | {order_desc}\n"
                    f"📍 Цена: <code>{price:.5f}</code>\n"
                    f"💼 <i>Отложенный ордер размещен в биржевом стакане MetaTrader 5.</i>"
                )
            else:
                msg = (
                    f"🚀 <b>ОРДЕР ИСПОЛНЕН В METATRADER 5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | {order_desc}\n"
                    f"📍 Цена входа: <code>{price:.5f}</code>\n"
                    f"💼 <i>Сделка подтверждена брокером и реально открыта в терминале!</i>"
                )
            await _send_telegram_notification(msg)

        # 2. Вход отклонен роботом (R:R < 1.8, превышен лимит или ошибка терминала)
        elif action.startswith("REJECTED"):
            await reject_signal_by_broker(symbol, reason, signal_id=sig_id)
            if "RR" in action:
                msg = (
                    f"⛔ <b>ВХОД ОТМЕНЁН РОБОТОМ MT5 (ФИЛЬТР РИСКА)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"🛡 Причина: <b>{reason}</b>\n\n"
                    f"💼 <i>Рыночная цена сместилась до исполнения. Робот заблокировал вход ради защиты депозита. Ордер в MT5 НЕ открыт.</i>"
                )
            elif "LIMIT" in action:
                msg = (
                    f"⚠️ <b>ВХОД ПРОПУЩЕН РОБОТОМ MT5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"💼 Причина: <b>{reason}</b>\n\n"
                    f"<i>Все торговые слоты заняты. Новый ордер заблокирован.</i>"
                )
            else:
                msg = (
                    f"⚠️ <b>ОШИБКА ИСПОЛНЕНИЯ В METATRADER 5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"❌ Причина: <code>{reason}</code>\n\n"
                    f"💼 <i>Ордер в MT5 НЕ был открыт. Проверьте статус терминала.</i>"
                )
            await _send_telegram_notification(msg)

        # 3. Позиция закрыта в MT5 (TP, SL, микро-TP, ручное закрытие)
        elif action in ("DEAL_CLOSED", "STALE_PROFIT_CLOSE", "CLOSED"):
            await close_signal_by_broker(symbol, price, profit, reason)
            profit_sign = "+" if profit >= 0 else ""
            emoji = "💰" if profit > 0 else ("🛡" if profit == 0 else "🛑")
            msg = (
                f"{emoji} <b>СДЕЛКА ЗАКРЫТА В METATRADER 5</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b>\n"
                f"💵 Результат: <b>{profit_sign}{profit:.2f} USD</b>\n"
                f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                f"🎯 Детали: <code>{reason}</code>\n"
                f"💼 <i>Баланс терминала обновлён.</i>"
            )
            await _send_telegram_notification(msg)

        return web.json_response({"status": "ok", "acknowledged": True})
    except Exception as e:
        logger.error("bridge_post_report error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=400)


def create_webapp_app() -> web.Application:
    """Создает приложение aiohttp для локального MT5 моста."""
    app = web.Application()
    app.router.add_get("/", handle_status)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/v1/bridge/orders", bridge_get_orders)
    app.router.add_post("/api/v1/bridge/orders", bridge_get_orders)
    app.router.add_post("/api/v1/bridge/report", bridge_post_report)
    return app


async def start_webapp_server(host: str = None, port: int = None, bot=None) -> web.AppRunner:
    """Запускает HTTP-сервер MT5 Bridge в фоне."""
    global _bot_instance
    if bot:
        _bot_instance = bot
    host = host or config.WEBAPP_HOST
    port = port or config.WEBAPP_PORT
    app = create_webapp_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🚀 Smart Trader MT5 Bridge running at http://%s:%d", host, port)
    return runner
