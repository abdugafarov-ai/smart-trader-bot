"""
Smart Trader Web App & Auto-Trading Bridge Server.
Легковесный асинхронный сервер на aiohttp.
Обслуживает:
1. Telegram Web App (Mini App) фронтенд
2. REST API для графиков и сигналов
3. Auto-Trading Bridge API для MetaTrader 4/5 (MQL4/MQL5)
"""

import asyncio
import json
import logging
from pathlib import Path
from aiohttp import web
from datetime import datetime, timezone

import config
from db.database import (
    get_active_signals, get_pending_signals, get_recent_signals,
    get_stats, update_signal_status, update_signal_sl
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Очередь команд для MetaTrader советника
# id -> order_dict
_bridge_command_queue: list[dict] = []
_executed_orders_log: list[dict] = []


async def handle_index(request: web.Request) -> web.Response:
    """Отдает главную страницу Telegram Web App."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return web.Response(text="Web App UI is initializing...", content_type="text/html")
    return web.FileResponse(index_file)


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
        # Берем активные сигналы за последние 3 часа
        active = await get_active_signals()
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
                "lot": config.AUTOTRADE_DEFAULT_LOT,
                "risk_percent": config.AUTOTRADE_DEFAULT_RISK,
                "magic_number": 888001,
            })

        return web.json_response({
            "status": "ok",
            "autotrade_enabled": config.AUTOTRADE_ENABLED,
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
    Body: {"signal_id": 12, "ticket": 98765432, "action": "OPENED", "price": 1.16210, "profit": 0.0}
    """
    try:
        data = await request.json()
        logger.info("MetaTrader Bridge report received: %s", data)
        _executed_orders_log.append({
            **data,
            "received_at": datetime.now(timezone.utc).isoformat()
        })
        return web.json_response({"status": "ok", "acknowledged": True})
    except Exception as e:
        logger.error("bridge_post_report error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=400)


def create_webapp_app() -> web.Application:
    """Создает приложение aiohttp с маршрутами."""
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/webapp", handle_index)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/v1/bridge/orders", bridge_get_orders)
    app.router.add_post("/api/v1/bridge/report", bridge_post_report)

    # Статические файлы (CSS, JS, картинки)
    if STATIC_DIR.exists():
        app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    return app


async def start_webapp_server(host: str = None, port: int = None) -> web.AppRunner:
    """Запускает HTTP-сервер Web App в фоне."""
    host = host or config.WEBAPP_HOST
    port = port or config.WEBAPP_PORT
    app = create_webapp_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🚀 Smart Trader Web App & Bridge running at http://%s:%d", host, port)
    return runner
