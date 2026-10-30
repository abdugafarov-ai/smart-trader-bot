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
_executed_orders_log: list[dict] = []  # Capped at 500 entries to prevent memory leak
_MAX_ORDERS_LOG = 500
_bot_instance = None


async def _send_telegram_notification(text: str, photo_bytes: bytes = None, text_admin: str = None):
    """Рассылает уведомление об исполнении/закрытии всем пользователям бота (с защитой контента от утечек)."""
    global _bot_instance
    if not _bot_instance:
        return
    try:
        from db.users import get_approved_user_ids
        recipients = await get_approved_user_ids()
        if config.ADMIN_ID and config.ADMIN_ID not in recipients:
            recipients.append(config.ADMIN_ID)
        for uid in recipients:
            msg_to_send = text_admin if (uid == config.ADMIN_ID and text_admin) else text
            protect = (uid != config.ADMIN_ID)
            try:
                if photo_bytes:
                    from aiogram.types import BufferedInputFile
                    await _bot_instance.send_photo(
                        uid,
                        photo=BufferedInputFile(photo_bytes, filename="trade_outcome.png"),
                        caption=msg_to_send,
                        parse_mode="HTML",
                        protect_content=protect
                    )
                else:
                    await _bot_instance.send_message(uid, msg_to_send, parse_mode="HTML", protect_content=protect)
            except Exception as err:
                try:
                    await _bot_instance.send_message(uid, msg_to_send, parse_mode=None, protect_content=protect)
                except Exception:
                    pass
                logger.error("Failed to send bridge notification to %d: %s", uid, err)
    except Exception as e:
        logger.error("Error sending bridge notification: %s", e)


async def _send_admin_only(text: str, photo_bytes: bytes = None):
    """Отправляет уведомление ИСКЛЮЧИТЕЛЬНО администратору (ручные сделки, ошибки брокера, статус терминала)."""
    global _bot_instance
    if not _bot_instance or not config.ADMIN_ID:
        return
    try:
        if photo_bytes:
            from aiogram.types import BufferedInputFile
            await _bot_instance.send_photo(
                config.ADMIN_ID,
                photo=BufferedInputFile(photo_bytes, filename="trade_outcome.png"),
                caption=text,
                parse_mode="HTML",
                protect_content=False
            )
        else:
            await _bot_instance.send_message(config.ADMIN_ID, text, parse_mode="HTML", protect_content=False)
    except Exception as err:
        try:
            await _bot_instance.send_message(config.ADMIN_ID, text, parse_mode=None, protect_content=False)
        except Exception:
            pass
        logger.error("Failed to send admin-only notification: %s", err)


async def handle_status(request: web.Request) -> web.Response:
    """Возвращает статус локального моста MT5."""
    return web.json_response({
        "status": "online",
        "service": "Smart Trader Local MT5 Bridge",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


def _check_public_api_auth(request: web.Request):
    """Защищает публичные эндпоинты от несанкционированного доступа извне (через туннель)."""
    key = (request.headers.get("X-API-Key") or request.query.get("api_key") or "").strip()
    if config.BRIDGE_API_KEY and key and key == config.BRIDGE_API_KEY:
        return
    # Разрешаем локальные запросы с самого сервера (без заголовков внешнего прокси Cloudflare)
    is_proxied = bool(request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"))
    if not is_proxied and request.remote in ("127.0.0.1", "::1", "localhost"):
        return
    raise web.HTTPForbidden(text='{"status":"error","message":"Forbidden: API key required"}', content_type="application/json")


async def api_signals(request: web.Request) -> web.Response:
    """Возвращает активные и недавние сигналы в формате JSON."""
    _check_public_api_auth(request)
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
    _check_public_api_auth(request)
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
    _check_public_api_auth(request)
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

# In-flight order dispatch tracker: signal_id -> timestamp (prevents duplicate sends on network lag/MT5 retries)
_dispatched_in_flight: dict[int, float] = {}

def _check_api_key(request):
    """Проверяет API-ключ в заголовках запроса (или разрешает локальный сокет без прокси)."""
    api_key = (request.headers.get('X-API-Key') or request.headers.get('X-Bridge-Key') or '').strip()
    if config.BRIDGE_API_KEY and api_key and api_key == config.BRIDGE_API_KEY:
        return
    is_proxied = bool(request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"))
    if not is_proxied and request.remote in ("127.0.0.1", "::1", "localhost"):
        return
    raise web.HTTPForbidden(text='Invalid API key')

async def bridge_get_orders(request: web.Request) -> web.Response:
    """
    Советник MT4/MT5 опрашивает этот эндпоинт раз в несколько секунд:
    GET /api/v1/bridge/orders
    Возвращает список сигналов, которые нужно открыть или модифицировать.
    """
    _check_api_key(request)
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
            server_time = int(post_data.get("server_time") or request.query.get("server_time") or 0)
            
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

            if history and isinstance(history, list) and len(history) > 0:
                try:
                    from db.database import sync_broker_deals, DB_PATH
                    import aiosqlite
                    from market.data_fetcher import DataFetcher
                    from utils.chart_generator import generate_outcome_chart
                    from utils.formatters import format_manual_close

                    new_closed_deals = await sync_broker_deals(history)
                    if new_closed_deals:
                        fetcher = DataFetcher()
                        for nd in new_closed_deals:
                            profit_val = float(nd.get("profit") or 0.0)
                            profit_sign = "+" if profit_val >= 0 else ""
                            sym = nd.get('symbol', '').upper()
                            deal_type = nd.get('type', 'BUY').upper()
                            lot = float(nd.get('lot') or bridge_manager.default_lot or 0.10)
                            close_p = float(nd.get("price") or 0.0)
                            ticket_no = nd.get("ticket", 0)
                            comment = str(nd.get("comment") or "")
                            magic = int(nd.get("magic") or 0)
                            is_manual = (magic != 888001 and magic != 777001) or "MANUAL" in comment.upper()

                            pip_mult = 100.0 if 'JPY' in sym else (10.0 if 'XAU' in sym else 10000.0)

                            pos_id = int(nd.get("position_id") or nd.get("pos_id") or 0)

                            # Поиск параметров исходного сигнала по надежному идентификатору (position_id / ticket)
                            sig_dict = {}
                            async with aiosqlite.connect(str(DB_PATH)) as db:
                                db.row_factory = aiosqlite.Row
                                if pos_id > 0:
                                    async with db.execute(
                                        "SELECT * FROM signals WHERE position_id = ? OR broker_ticket = ? OR mt5_ticket = ? OR mt5_order_id = ? ORDER BY id DESC LIMIT 1",
                                        (pos_id, pos_id, pos_id, pos_id)
                                    ) as c:
                                        sig_row = await c.fetchone()
                                        if sig_row:
                                            sig_dict = dict(sig_row)
                                if not sig_dict and ticket_no > 0:
                                    async with db.execute(
                                        "SELECT * FROM signals WHERE broker_ticket = ? OR mt5_ticket = ? OR mt5_order_id = ? ORDER BY id DESC LIMIT 1",
                                        (ticket_no, ticket_no, ticket_no)
                                    ) as c:
                                        sig_row = await c.fetchone()
                                        if sig_row:
                                            sig_dict = dict(sig_row)
                                if not sig_dict:
                                    # Fallback: поиск по символу с приоритетом открытых/ожидающих сигналов
                                    async with db.execute(
                                        "SELECT * FROM signals WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE', 'PENDING') ORDER BY id DESC LIMIT 1",
                                        (sym,)
                                    ) as c:
                                        sig_row = await c.fetchone()
                                        if sig_row:
                                            sig_dict = dict(sig_row)
                            entry_p = float(nd['entry_price']) if nd.get('entry_price') else (float(sig_dict['entry_price']) if sig_dict.get('entry_price') else None)
                            direction = sig_dict.get('direction', 'LONG') if sig_dict else ('LONG' if 'BUY' in deal_type else 'SHORT')
                            sl_p = float(sig_dict.get('stop_loss') or 0.0)
                            tp_p = float(sig_dict.get('take_profit_1') or 0.0)
                            pnl_pips = (close_p - entry_p if direction == 'LONG' else entry_p - close_p) * pip_mult if (entry_p and close_p) else None

                            if is_manual:
                                msg = format_manual_close(sym, profit_val, pnl_pips, close_p)
                                status_str = "MANUAL_CLOSE"
                                chart_bytes = None
                                try:
                                    df_c = await fetcher.fetch_ohlcv(sym, "H1", limit=60)
                                    if df_c is not None and not df_c.empty:
                                        chart_bytes = generate_outcome_chart(
                                            df=df_c, symbol=sym, direction=direction,
                                            entry=entry_p, stop_loss=sl_p, take_profit=tp_p,
                                            close_price=close_p, status=status_str,
                                            profit_usd=profit_val, timeframe="H1"
                                        )
                                except Exception as chart_err:
                                    logger.error("Outcome chart generation error for sync_broker_deals: %s", chart_err)
                                await _send_admin_only(msg, photo_bytes=chart_bytes)
                            else:
                                pips_pos_str = f"+{abs(pnl_pips):.1f} pips" if pnl_pips is not None else "N/A pips"
                                pips_neg_str = f"-{abs(pnl_pips):.1f} pips" if pnl_pips is not None else "N/A pips"
                                pips_be_str = f"({pnl_pips:+.1f} pips)" if pnl_pips is not None else "(N/A pips)"

                                if profit_val > 0:
                                    msg_admin = (
                                        f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (METATRADER 5)!</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                        f"💵 Результат: <b>+{profit_val:.2f} USD</b> ({pips_pos_str})\n"
                                        f"📍 Цена закрытия: <code>{close_p}</code>\n"
                                        f"🎫 Билет: <code>#{ticket_no}</code>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🎯 <i>Цель полностью достигнута. Прибыль зафиксирована в банке.</i>"
                                    )
                                    msg_client = (
                                        f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (СИГНАЛ ЗАКРЫТ)!</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type}</code>\n"
                                        f"📈 Результат: <b>{pips_pos_str}</b> (Цель достигнута 🎯)\n"
                                        f"📍 Цена выхода: <code>{close_p}</code>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"🎯 <i>Цель полностью достигнута по алгоритму ICT/SMC. Прибыль зафиксирована.</i>"
                                    )
                                    status_str = "TP"
                                elif profit_val < 0:
                                    msg_admin = (
                                        f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (METATRADER 5)</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                        f"📉 Фиксация убытка: <b>-{abs(profit_val):.2f} USD</b> ({pips_neg_str})\n"
                                        f"📍 Цена выхода: <code>{close_p}</code>\n"
                                        f"🎫 Билет: <code>#{ticket_no}</code>\n\n"
                                        f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                                        f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                                        f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                                    )
                                    msg_client = (
                                        f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (ФИКСАЦИЯ УБЫТКА)</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type}</code>\n"
                                        f"📉 Итог: <b>{pips_neg_str}</b>\n"
                                        f"📍 Цена выхода: <code>{close_p}</code>\n\n"
                                        f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                                        f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                                        f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                                    )
                                    status_str = "SL"
                                else:
                                    msg_admin = (
                                        f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                        f"💵 Результат: <b>{profit_val:+.2f} USD</b> {pips_be_str}\n"
                                        f"📍 Цена закрытия: <code>{close_p}</code>\n"
                                        f"🎫 Билет: <code>#{ticket_no}</code>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"💼 <i>Позиция закрыта в безубыток без риска для баланса.</i>"
                                    )
                                    msg_client = (
                                        f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"📊 <b>{sym}</b> | <code>{deal_type}</code>\n"
                                        f"📍 Цена закрытия: <code>{close_p}</code>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"💼 <i>Позиция закрыта в безубыток без риска для баланса.</i>"
                                    )
                                    status_str = "CLOSED"

                                chart_bytes = None
                                try:
                                    df_c = await fetcher.fetch_ohlcv(sym, "H1", limit=60)
                                    if df_c is not None and not df_c.empty:
                                        chart_bytes = generate_outcome_chart(
                                            df=df_c, symbol=sym, direction=direction,
                                            entry=entry_p, stop_loss=sl_p, take_profit=tp_p,
                                            close_price=close_p, status=status_str,
                                            profit_usd=profit_val, timeframe="H1"
                                        )
                                except Exception as chart_err:
                                    logger.error("Outcome chart generation error for sync_broker_deals: %s", chart_err)

                                await _send_telegram_notification(msg_client, photo_bytes=chart_bytes, text_admin=msg_admin)
                except Exception as hist_err:
                    logger.error("Failed to sync broker deals from EA: %s", hist_err)

            if balance > 0 or equity > 0 or broker or positions is not None or orders_list is not None or server_time > 0:
                bridge_manager.update_telemetry(
                    balance=balance, equity=equity, margin_free=margin_free,
                    broker=broker, account=account,
                    positions=positions if positions is not None else [],
                    orders=orders_list if orders_list is not None else [],
                    server_time=server_time
                )
            else:
                bridge_manager.mt5_telemetry["last_ping"] = datetime.now(timezone.utc)
        except Exception as tele_err:
            logger.debug("Telemetry parsing error: %s", tele_err)

        # Проверка времени принудительного закрытия внутридневных позиций до ролловера (EOD Flat 20:40 UTC / Пт 19:30 UTC)
        bridge_manager.check_and_trigger_eod_flat()

        panic = bridge_manager.panic_close_requested
        if panic:
            bridge_manager.panic_close_requested = False
            logger.warning("Sending panic_close_all=True to MT5 terminal!")

        use_risk_flag = 1 if (getattr(bridge_manager, 'lot_mode', 'risk') in ('risk', 'dynamic')) else 0

        # 0. Проверка аварийного хард-стопа по эквити ($4,450.00 с запасом $50 до пола $4,400.00)
        from utils.broker_time import get_broker_today_date_str
        today_str = get_broker_today_date_str()
        is_hard_stop, hard_reason = bridge_manager.check_equity_hard_stop()
        if is_hard_stop:
            if getattr(bridge_manager, 'hard_stop_alerted_date', '') != today_str:
                bridge_manager.hard_stop_alerted_date = today_str
                alert_text = (
                    f"🚨 <b>КРИТИЧЕСКАЯ АВАРИЯ: СРАБОТАЛ ХАРД-СТОП ПО ЭКВИТИ ($4,450.00)!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🛑 Эквити: <b>{bridge_manager.mt5_telemetry.get('equity', 0.0):.2f} USD</b>\n"
                    f"⚠️ Порог хард-стопа: <b>${bridge_manager.hard_equity_stop_usd:.2f} USD</b> (пол брокера: $4,400.00)\n"
                    f"🔒 Все рыночные позиции закрыты, автопилот заблокирован!\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Депозит челленджа FundingPips защищен от фатальной просадки брокера.</i>"
                )
                await _send_telegram_notification(alert_text)
            return web.json_response({
                "status": "ok",
                "autotrade_enabled": False,
                "daily_loss_locked": True,
                "panic_close_all": True,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
                "orders": [],
                "reason": hard_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # 1. Проверка Hard Daily Drawdown Limit
        from db.database import get_today_broker_pnl, get_bot_setting
        today_stats = await get_today_broker_pnl()
        is_daily_locked, today_net_pnl, today_dd_pct, start_bal = bridge_manager.check_daily_loss(
            today_closed_pnl=today_stats.get("total_pnl", 0.0),
            today_deals_count=today_stats.get("deals_count", 0)
        )
        if is_daily_locked:
            # Отправляем экстренное тревожное сообщение в Telegram один раз за день
            if bridge_manager.daily_lock_alerted_date != today_str:
                bridge_manager.daily_lock_alerted_date = today_str
                alert_text = (
                    f"🚨 <b>ВНИМАНИЕ: СРАБОТАЛ HARD DAILY DRAWDOWN LIMIT!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📉 Дневной PnL: <b>{today_net_pnl:.2f} USD</b> (<code>-{today_dd_pct:.2f}%</code>)\n"
                    f"🛑 Лимит допустимого убытка: <b>{bridge_manager.max_daily_loss_pct:.1f}%</b>\n"
                    f"🔒 Авто-торговля <b>АВТОМАТИЧЕСКИ ЗАМОРОЖЕНА</b> до 00:00 сервера MT5 (EET).\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Капитал защищен. Алгоритм сберег баланс и предотвратил серию тильтовых стопов. "
                    f"Сбросить блокировку вручную при необходимости можно в меню настроек автопилота.</i>"
                )
                await _send_telegram_notification(alert_text)

            return web.json_response({
                "status": "ok",
                "autotrade_enabled": False,
                "daily_loss_locked": True,
                "panic_close_all": panic,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
                "orders": [],
                "reason": bridge_manager.daily_lock_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        if not bridge_manager.enabled:
            return web.json_response({
                "status": "ok",
                "autotrade_enabled": False,
                "daily_loss_locked": False,
                "panic_close_all": panic,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
                "orders": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # Берем только новые активные и неподтвержденные сигналы из базы данных
        from db.database import DB_PATH
        import aiosqlite
        import time as _time
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM signals 
                   WHERE status = 'ACTIVE'
                     AND (broker_confirmed IS NULL OR broker_confirmed = 0)
                     AND broker_ticket IS NULL
                   ORDER BY id DESC"""
            )
            rows = await cursor.fetchall()
            active = [dict(r) for r in rows]

        trading_mode = await get_bot_setting("trading_mode", "prop")
        orders = []
        current_active_slots = len(bridge_manager.mt5_telemetry.get("positions") or []) + len(bridge_manager.mt5_telemetry.get("orders") or [])
        max_allowed_slots = 1 if trading_mode == "micro" else bridge_manager.max_concurrent_positions
        dispatched_symbols = set()

        from db.database import get_today_signal_count
        try:
            raw_count = await get_today_signal_count()
            today_signal_count = int(raw_count) if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool) else 0
        except Exception:
            today_signal_count = 0
        active_prof = config.get_active_profile()
        max_daily_trades = int(active_prof.get("max_daily_new_trades", getattr(config, "MAX_SIGNALS_PER_DAY", 5)))
        if today_signal_count >= max_daily_trades:
            logger.info("Daily signal limit reached (%d/%d). Orders held.", today_signal_count, max_daily_trades)
            return web.json_response({
                "status": "ok",
                "autotrade_enabled": bridge_manager.enabled,
                "panic_close_all": panic,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
                "orders": [],
                "reason": f"Дневной лимит сделок исчерпан ({today_signal_count}/{max_daily_trades})",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        from db.database import get_consecutive_sl_count
        consec_sl = await get_consecutive_sl_count(max_lookback_hours=12.0)

        for sig in (active or []):
            sym = sig.get("symbol")
            if not sym or sym in dispatched_symbols:
                continue

            sig_id = int(sig.get("id") or 0)
            now_ts = _time.time()
            # Защита от дублирования при повторных запросах/таймаутах MT5:
            if sig_id in _dispatched_in_flight and (now_ts - _dispatched_in_flight[sig_id]) < 45.0:
                logger.debug("Signal #%s (%s) already in-flight to MT5, skipping duplicate dispatch", sig_id, sym)
                continue

            # Проверка актуальности сигнала (запрет исполнения устаревших сигналов при включении автопилота с задержкой)
            if not sig.get("broker_confirmed") and sig.get("status", "ACTIVE") in ("ACTIVE", "PENDING"):
                created_raw = sig.get("created_at")
                if created_raw:
                    try:
                        created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=timezone.utc)
                        age_sec = (datetime.now(timezone.utc) - created_dt).total_seconds()
                        ord_t = str(sig.get("order_type", "BUY_MARKET")).upper()
                        max_age_sec = 900.0 if "MARKET" in ord_t else 14400.0  # 15m for MARKET, 4h for LIMIT
                        if age_sec > max_age_sec:
                            logger.warning(
                                "Signal #%s (%s, %s) EXPIRED before MT5 dispatch (age %.0fs > %.0fs)",
                                sig.get("id"), sym, ord_t, age_sec, max_age_sec
                            )
                            await update_signal_status(
                                sig.get("id"),
                                "EXPIRED",
                                result=f"NO TRADE: истёк срок актуальности сигнала ({int(age_sec // 60)} мин)"
                            )
                            continue
                    except Exception as exp_err:
                        logger.debug("Signal #%s created_at parse skipped: %s", sig.get("id"), exp_err)

            # Если активен 12-часовой кулдаун после 3 стопов подряд, не отправляем новые неподтвержденные сигналы
            if consec_sl >= 3 and not sig.get("broker_confirmed"):
                logger.info("Signal #%s (%s) held by 12h 3-SL Cooldown (%d SLs)", sig.get("id"), sym, consec_sl)
                continue

            # Институциональная проверка R:R (строго >= 1:2.0 для XAUUSD, >= 1:1.5 для Forex / Research Profile)
            entry_p = float(sig.get("entry_price") or 0.0)
            sl_p = float(sig.get("stop_loss") or 0.0)
            tp_p = float(sig.get("take_profit_1") or sig.get("take_profit_2") or 0.0)
            if entry_p <= 0 or sl_p <= 0 or tp_p <= 0:
                logger.warning("Signal #%s (%s) skipped: missing entry/SL/TP", sig.get("id"), sym)
                continue

            risk_d = abs(entry_p - sl_p)
            reward_d = abs(tp_p - entry_p)
            if risk_d <= 0:
                continue
            sig_rr = reward_d / risk_d
            sym_clean = sym.upper().replace("/", "").replace("=X", "")
            prof_min_rr = float(active_prof.get("min_rr", 2.0 if ("XAU" in sym_clean or "GOLD" in sym_clean) else 1.5))
            min_rr_req = max(1.0, prof_min_rr - 0.05)
            if sig_rr < min_rr_req:
                logger.warning("Signal #%s (%s) rejected: R:R %.2f < %.2f", sig.get("id"), sym, sig_rr, min_rr_req)
                continue

            # Проверка дневного лимита сделок и свободных слотов в текущем цикле отправки
            if (today_signal_count + len(orders)) >= max_daily_trades:
                logger.info("Daily signal capacity reached: %d/%d (holding remaining signals)",
                            today_signal_count + len(orders), max_daily_trades)
                break

            if (current_active_slots + len(orders)) >= max_allowed_slots:
                logger.info("Batch slot capacity reached: %d/%d (holding remaining signals)",
                            current_active_slots + len(orders), max_allowed_slots)
                break

            # Единый институциональный расчет риска и объема (0.25% базовый, 0.50% макс):
            # Если минимальный лот 0.01 превышает риск-бюджет для данного стопа -> REJECT (NO TRADE)!
            sl_dist = risk_d
            if "JPY" in sym_clean:
                sl_pips = sl_dist / 0.01
            elif "XAU" in sym_clean or "GOLD" in sym_clean:
                sl_pips = sl_dist
            else:
                sl_pips = sl_dist / 0.0001

            max_safe_lot = bridge_manager.calculate_lot(
                sym, sl_pips, sl_dist_usd=sl_dist, entry=entry_p, sl=sl_p,
                is_real_order=True, require_confirmed_specs=True
            )
            if max_safe_lot <= 0.0:
                logger.warning(
                    "Signal #%s (%s) REJECTED: min lot 0.01 risk ($%.2f) exceeds allowed risk budget (%.2f%%)",
                    sig.get("id"), sym, sl_dist, bridge_manager.default_risk
                )
                await update_signal_status(
                    sig.get("id"),
                    "CANCELLED_BY_MT5",
                    result=f"NO TRADE: риск на 0.01 лота (${sl_dist:.2f}) превышает бюджет ({bridge_manager.default_risk:.2f}%)"
                )
                continue

            if getattr(bridge_manager, 'lot_mode', 'risk') in ('risk', 'dynamic'):
                order_lot = max_safe_lot
            else:
                req_lot = float(sig.get("lot") or bridge_manager.default_lot)
                if req_lot <= 0.0 or req_lot > max_safe_lot + 1e-9:
                    logger.warning(
                        "Signal #%s (%s) REJECTED: fixed lot %.2f exceeds max safe lot %.2f for SL %.2f",
                        sig.get("id"), sym, req_lot, max_safe_lot, sl_dist
                    )
                    await update_signal_status(
                        sig.get("id"),
                        "CANCELLED_BY_MT5",
                        result=f"NO TRADE: фиксированный лот {req_lot:.2f} превышает риск-бюджет (макс {max_safe_lot:.2f})"
                    )
                    continue
                order_lot = round(req_lot, 2)

            spec = getattr(bridge_manager, "symbol_specs", {}).get(sym_clean, {})
            contract_mult = float(spec.get("contract_size") or (100.0 if ("XAU" in sym_clean or "GOLD" in sym_clean) else (1000.0 if "JPY" in sym_clean else 100000.0)))
            comm_per_lot = float(getattr(config, "COMMISSION_PER_LOT_USD", 3.00))
            slip_buf = float(getattr(config, "SLIPPAGE_BUFFER_USD", 0.20))
            expected_risk_usd = round(order_lot * (sl_dist + slip_buf) * contract_mult + (order_lot * comm_per_lot), 2)

            # Повторная проверка ожидаемого риска перед отправкой ордера
            eff_bal = bridge_manager.get_effective_balance(0.0)
            risk_budget_usd = eff_bal * (min(bridge_manager.default_risk, config.AUTOTRADE_MAX_RISK) / 100.0)
            if eff_bal > 0 and expected_risk_usd > risk_budget_usd + 0.10:
                logger.warning(
                    "Signal #%s (%s) REJECTED: expected risk ($%.2f) exceeds risk budget ($%.2f)",
                    sig.get("id"), sym, expected_risk_usd, risk_budget_usd
                )
                await update_signal_status(
                    sig.get("id"),
                    "CANCELLED_BY_MT5",
                    result=f"NO TRADE: ожидаемый риск ${expected_risk_usd:.2f} превышает бюджет ${risk_budget_usd:.2f}"
                )
                continue

            # Институциональная многофакторная валидация перед входом в рынок (включая суммарный открытый риск)
            can_open, hold_reason = bridge_manager.can_open_new_position(
                sym, trading_mode=trading_mode, new_trade_risk_usd=expected_risk_usd
            )
            if not can_open:
                logger.debug("Signal #%s (%s) held by Risk Guard: %s", sig.get("id"), sym, hold_reason)
                continue

            dispatched_symbols.add(sym)
            _dispatched_in_flight[sig_id] = now_ts
            orders.append({
                "id": sig.get("id"),
                "symbol": sym,
                "direction": sig.get("direction"), # LONG или SHORT
                "order_type": sig.get("order_type", "BUY_MARKET"),
                "entry": sig.get("entry_price"),
                "stop_loss": sig.get("stop_loss"),
                "tp1": sig.get("take_profit_1"),
                "tp2": sig.get("take_profit_2"),
                "breakeven_applied": bool(sig.get("breakeven_applied", 0)),
                "lot": order_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
                "commission_per_lot": comm_per_lot,
                "magic_number": 888001,
            })

        return web.json_response({
            "status": "ok",
            "autotrade_enabled": bridge_manager.enabled,
            "panic_close_all": panic,
            "lot": bridge_manager.default_lot,
            "risk_percent": bridge_manager.default_risk,
            "use_auto_risk": use_risk_flag,
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
    _check_api_key(request)
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
        # Prevent memory leak: trim log to last N entries
        if len(_executed_orders_log) > _MAX_ORDERS_LOG:
            del _executed_orders_log[:len(_executed_orders_log) - _MAX_ORDERS_LOG]

        symbol = data.get("symbol", "")
        action = data.get("action", "").upper()
        price = float(data.get("price") or 0.0)
        profit = float(data.get("profit") or 0.0)
        from trading.execution_bridge import bridge_manager
        lot = float(data.get("lot") or data.get("volume") or bridge_manager.default_lot or 0.10)
        reason = data.get("reason", "")
        sig_id = int(data.get("signal_id") or 0)

        from db.database import (
            confirm_signal_by_broker, reject_signal_by_broker, close_signal_by_broker,
            activate_filled_signal, expire_signal_by_broker
        )
        ticket = int(data.get("ticket") or data.get("order") or 0)
        if sig_id > 0:
            _dispatched_in_flight.pop(sig_id, None)

        # 1. Лимитный ордер выставлен в стакан
        if action in ("BUY_LIMIT", "SELL_LIMIT"):
            await confirm_signal_by_broker(symbol, action, price, ticket=ticket, signal_id=sig_id)
            order_desc = "BUY LIMIT (Покупка)" if "BUY" in action else "SELL LIMIT (Продажа)"
            msg_admin = (
                f"⏳ <b>ЛИМИТНЫЙ ОРДЕР ВЫСТАВЛЕН В MT5</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | {order_desc}\n"
                f"📍 Цена: <code>{price:.5f}</code>\n"
                f"💼 <i>Отложенный ордер размещен в биржевом стакане MetaTrader 5.</i>"
            )
            msg_client = (
                f"⏳ <b>СИГНАЛ ВЫСТАВЛЕН: ОЖИДАНИЕ ВХОДА</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | {order_desc}\n"
                f"📍 Рекомендуемая цена лимита: <code>{price:.5f}</code>\n"
                f"💼 <i>Выставьте отложенный ордер в своём терминале по указанной цене.</i>"
            )
            await _send_telegram_notification(msg_client, text_admin=msg_admin)

        # 2. Лимитный ордер исполнился брокером (DEAL_ENTRY_IN -> позиция в рынке)
        elif action in ("ORDER_FILLED", "LIMIT_FILLED"):
            await activate_filled_signal(symbol, price, ticket=ticket, signal_id=sig_id)
            msg_admin = (
                f"🚀 <b>ЛИМИТНЫЙ ОРДЕР СРАБОТАЛ (В РЫНКЕ)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b>\n"
                f"📍 Цена фактического входа: <code>{price:.5f}</code>\n"
                f"💼 <i>Цена коснулась уровня лимита. Позиция открыта в MetaTrader 5!</i>"
            )
            msg_client = (
                f"⚡ <b>СИГНАЛ АКТИВИРОВАН: ВХОД В РЫНОК</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b>\n"
                f"📍 Цена входа: <code>{price:.5f}</code>\n"
                f"💼 <i>Цена коснулась уровня. Позиция в рынке. Сопровождайте сделку до Take Profit!</i>"
            )
            await _send_telegram_notification(msg_client, text_admin=msg_admin)

        # 3. Лимитный ордер снят брокером или истек по таймауту
        elif action in ("LIMIT_EXPIRED", "ORDER_CANCELED", "ORDER_CANCELLED", "EXPIRED"):
            await expire_signal_by_broker(symbol, signal_id=sig_id, reason=reason)
            import html
            safe_reason = html.escape(str(reason or ""))
            msg_admin = (
                f"⏰ <b>ЛИМИТНЫЙ ОРДЕР СНЯТ / ИСТЁК В MT5</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b>\n"
                f"ℹ️ Причина: <code>{safe_reason or 'Истек срок ожидания (снят)'}</code>\n"
                f"💼 <i>Ордер удален из биржевого стакана. Торговый слот освобожден.</i>"
            )
            msg_client = (
                f"⏰ <b>СИГНАЛ ОТМЕНЁН / ИСТЁК СРОК ОЖИДАНИЯ</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b>\n"
                f"ℹ️ Причина: <code>{safe_reason or 'Цена не дошла до лимита в отведённое время'}</code>\n"
                f"💼 <i>Отмените отложенный ордер в своём терминале.</i>"
            )
            await _send_telegram_notification(msg_client, text_admin=msg_admin)

        # 4. Рыночный ордер сразу открыт
        elif action in ("BUY", "SELL", "OPENED"):
            await confirm_signal_by_broker(symbol, action, price, ticket=ticket, signal_id=sig_id)
            order_desc = "BUY (Покупка)" if "BUY" in action else "SELL (Продажа)"
            msg_admin = (
                f"🚀 <b>ОРДЕР ИСПОЛНЕН В METATRADER 5</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | {order_desc}\n"
                f"📍 Цена входа: <code>{price:.5f}</code>\n"
                f"💼 <i>Сделка подтверждена брокером и реально открыта в терминале!</i>"
            )
            msg_client = (
                f"🚀 <b>СИГНАЛ: ВХОД ПО РЫНКУ</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | {order_desc}\n"
                f"📍 Цена входа: <code>{price:.5f}</code>\n"
                f"💼 <i>Позиция открыта. Установите Take Profit и Stop Loss согласно сигналу!</i>"
            )
            await _send_telegram_notification(msg_client, text_admin=msg_admin)

        # 5. Вход отклонен роботом (R:R < 1.8, превышен лимит или ошибка терминала) — ТОЛЬКО АДМИНИСТРАТОРУ
        elif action.startswith("REJECTED"):
            await reject_signal_by_broker(symbol, reason, signal_id=sig_id)
            import html
            safe_reason = html.escape(str(reason or ""))
            if "RR" in action:
                msg = (
                    f"⛔ <b>ВХОД ОТМЕНЁН РОБОТОМ MT5 (ФИЛЬТР РИСКА)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"🛡 Причина: <b>{safe_reason}</b>\n\n"
                    f"💼 <i>Рыночная цена сместилась до исполнения. Робот заблокировал вход ради защиты депозита. Ордер в MT5 НЕ открыт.</i>"
                )
            elif "LIMIT" in action:
                msg = (
                    f"⚠️ <b>ВХОД ПРОПУЩЕН РОБОТОМ MT5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"💼 Причина: <b>{safe_reason}</b>\n\n"
                    f"<i>Все торговые слоты заняты. Новый ордер заблокирован.</i>"
                )
            else:
                msg = (
                    f"⚠️ <b>ОШИБКА ИСПОЛНЕНИЯ В METATRADER 5</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b>\n"
                    f"❌ Причина: <code>{safe_reason}</code>\n\n"
                    f"💼 <i>Ордер в MT5 НЕ был открыт. Проверьте статус терминала.</i>"
                )
            await _send_admin_only(msg)

        # Ошибка модификации стоп-лосса брокером
        elif action == "SL_MODIFY_FAILED":
            logger.warning("SL modification failed in MT5 for %s ticket %s: %s", symbol, ticket, reason)
            import html
            safe_reason = html.escape(str(reason or ""))
            msg = (
                f"⚠️ <b>ОШИБКА МОДИФИКАЦИИ STOP LOSS В METATRADER 5!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | Билет: <code>#{ticket}</code>\n"
                f"❌ Причина: <code>{safe_reason}</code>\n\n"
                f"💼 <i>Проверьте статус позиции и уровни стопа в MT5 вручную!</i>"
            )
            await _send_admin_only(msg)

        # Ручное открытие сделки пользователем в MT5 — ТОЛЬКО АДМИНИСТРАТОРУ
        elif action == "MANUAL_OPEN":
            volume = float(data.get("profit") or 0.01)
            order_type = reason or "BUY"
            from utils.formatters import format_manual_open
            msg = format_manual_open(symbol, order_type, volume, price)
            await _send_admin_only(msg)

        # Ручное закрытие сделки пользователем в MT5 — ТОЛЬКО АДМИНИСТРАТОРУ
        elif action == "MANUAL_CLOSE":
            sig = await close_signal_by_broker(symbol, price, profit, "MANUAL_CLOSE")
            pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)
            entry = float(sig.get('entry_price')) if (sig and sig.get('entry_price')) else None
            direction = sig.get('direction', 'LONG') if sig else 'LONG'
            sl_val = float(sig.get('stop_loss') or 0.0) if sig else 0.0
            tp_val = float(sig.get('take_profit_1') or 0.0) if sig else 0.0
            pnl_pips = (price - entry if direction == 'LONG' else entry - price) * pip_mult if (entry is not None and price) else None

            from utils.formatters import format_manual_close
            msg = format_manual_close(symbol, profit, pnl_pips, price)

            chart_bytes = None
            try:
                from market.data_fetcher import DataFetcher
                from utils.chart_generator import generate_outcome_chart
                df_c = await DataFetcher().fetch_ohlcv(symbol, "H1", limit=75)
                if df_c is not None and not df_c.empty:
                    chart_bytes = generate_outcome_chart(
                        df=df_c, symbol=symbol, direction=direction,
                        entry=entry, stop_loss=sl_val, take_profit=tp_val,
                        close_price=price, status="MANUAL_CLOSE",
                        profit_usd=profit, timeframe="H1",
                        signal_time=(sig.get('created_at') or sig.get('activated_at')) if sig else None
                    )
            except Exception as chart_err:
                logger.error("Failed to generate manual close chart: %s", chart_err)

            if ticket > 0:
                try:
                    from db.database import DB_PATH
                    import aiosqlite
                    async with aiosqlite.connect(str(DB_PATH)) as db:
                        await db.execute(
                            """INSERT OR IGNORE INTO broker_deals (ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ticket, symbol, direction, lot, price, profit, int(datetime.now(timezone.utc).timestamp()), 0, "MANUAL_CLOSE", datetime.now(timezone.utc).isoformat())
                        )
                        await db.commit()
                except Exception:
                    pass

            await _send_admin_only(msg, photo_bytes=chart_bytes)

        # 4.5. Частичная фиксация 50% объема (Partial Close 50% на пуле ликвидности)
        elif action in ("PARTIAL_CLOSE", "TP1_PARTIAL"):
            try:
                from db.database import DB_PATH
                import aiosqlite
                async with aiosqlite.connect(str(DB_PATH)) as db:
                    await db.execute(
                        "UPDATE signals SET status = 'TP1_PARTIAL' WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE')",
                        (symbol,)
                    )
                    if ticket > 0:
                        await db.execute(
                            """INSERT OR IGNORE INTO broker_deals (ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ticket, symbol, "PARTIAL_CLOSE", lot, price, profit, int(datetime.now(timezone.utc).timestamp()), 888001, reason, datetime.now(timezone.utc).isoformat())
                        )
                    await db.commit()
            except Exception as part_err:
                logger.error("PARTIAL_CLOSE db update error: %s", part_err)

            msg_part = (
                f"🎯 <b>ЧАСТИЧНАЯ ФИКСАЦИЯ 50% (PARTIAL CLOSE)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | Билет: <code>#{ticket}</code>\n"
                f"💵 Зафиксировано: <b>+{profit:+.2f} USD</b> ({lot:.2f} lot)\n"
                f"📍 Цена фиксации: <code>{price:.5f}</code>\n"
                f"🛡 Ликвидность: <i>{reason or 'Opposing Liquidity Pool'}</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 <i>Остаток 50% объема удерживается как безопасный раннер (SL в Б/У за защитным свингом) до финального TP!</i>"
            )
            await _send_telegram_notification(msg_part, text_admin=msg_part)

        # Структурный Безубыток или защита ролловера активирована (преодоление 1.0R -> стоп за Protected Swing)
        elif action in ("AUTO_BREAKEVEN", "MIDNIGHT_ROLLOVER_GUARD"):
            try:
                from db.database import DB_PATH
                import aiosqlite
                async with aiosqlite.connect(str(DB_PATH)) as db:
                    pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)
                    pip_size = 1.0 / pip_mult
                    async with db.execute(
                        "SELECT id, direction, entry_price FROM signals WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE', 'TP1_PARTIAL') ORDER BY id DESC LIMIT 1",
                        (symbol,)
                    ) as cur:
                        sig_row = await cur.fetchone()
                        if sig_row:
                            s_id, s_dir, s_entry = sig_row
                            s_entry = float(s_entry or price)
                            be_sl = s_entry + (2.0 * pip_size) if s_dir == 'LONG' else s_entry - (2.0 * pip_size)
                            await db.execute(
                                "UPDATE signals SET breakeven_applied = 1, stop_loss = ? WHERE id = ?",
                                (round(be_sl, 5), s_id)
                            )
                        else:
                            await db.execute(
                                "UPDATE signals SET breakeven_applied = 1 WHERE symbol = ? AND status IN ('OPEN', 'ACTIVE', 'TP1_PARTIAL')",
                                (symbol,)
                            )
                    await db.commit()
            except Exception as be_err:
                logger.error("AUTO_BREAKEVEN db update error: %s", be_err)
            msg_be = (
                f"🛡 <b>СТРУКТУРНЫЙ БЕЗУБЫТОК АКТИВИРОВАН (MT5)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>{symbol}</b> | Билет: <code>#{ticket}</code>\n"
                f"📈 Преодолен барьер 1.0R: <b>+{profit:.2f} USD</b>\n"
                f"🔒 <b>Стоп-лосс перенесён за защитный локальный свинг (Protected Swing)!</b>\n"
                f"ℹ️ Причина: <code>{reason or action}</code>\n"
                f"🎯 <i>Позиция в полной безопасности. Риск потерь ликвидирован.</i>"
            )
            await _send_admin_only(msg_be)

        # Позиция закрыта в MT5 (TP, SL, безубыток, EOD Flat)
        elif action in ("DEAL_CLOSED", "STALE_PROFIT_CLOSE", "CLOSED", "CLOSED_EOD_FLAT"):
            sig = await close_signal_by_broker(symbol, price, profit, reason)
            profit_sign = "+" if profit >= 0 else ""
            reason_u = str(reason or "").upper()
            is_be = "BREAKEVEN" in reason_u or (sig and sig.get("status") == "BREAKEVEN")
            is_tp = (not is_be) and (profit > 0 or "TP" in reason_u)
            is_sl = (not is_be) and (profit < 0 or "SL" in reason_u)
            pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)
            entry = float(sig.get('entry_price')) if (sig and sig.get('entry_price')) else None
            direction = sig.get('direction', 'LONG') if sig else ('LONG' if 'BUY' in reason.upper() else 'SHORT')
            sl_val = float(sig.get('stop_loss') or 0.0) if sig else 0.0
            tp_val = float(sig.get('take_profit_1') or 0.0) if sig else 0.0
            pnl_pips = (price - entry if direction == 'LONG' else entry - price) * pip_mult if (entry is not None and price) else None

            pips_pos_str = f"+{abs(pnl_pips):.1f} pips" if pnl_pips is not None else "N/A"
            pips_neg_str = f"-{abs(pnl_pips):.1f} pips" if pnl_pips is not None else "N/A"
            pips_be_str = f"{pnl_pips:+.1f} pips" if pnl_pips is not None else "N/A"

            if is_tp:
                msg_admin = (
                    f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (METATRADER 5)!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"💵 Результат: <b>+{profit:.2f} USD</b> ({pips_pos_str})\n"
                    f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <i>Цель полностью достигнута. Прибыль зафиксирована в банке.</i>"
                )
                msg_client = (
                    f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (СИГНАЛ ЗАКРЫТ)!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"📈 Результат: <b>{pips_pos_str}</b> (Цель достигнута 🎯)\n"
                    f"📍 Цена выхода: <code>{price:.5f}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <i>Сделка полностью закрыта с фиксацией прибыли по алгоритму ICT/SMC.</i>"
                )
            elif is_sl:
                msg_admin = (
                    f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (METATRADER 5)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"📉 Фиксация убытка: <b>-{abs(profit):.2f} USD</b> ({pips_neg_str})\n"
                    f"📍 Цена выхода: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n\n"
                    f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                    f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                    f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                )
                msg_client = (
                    f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (ФИКСАЦИЯ УБЫТКА)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"📉 Итог: <b>{pips_neg_str}</b>\n"
                    f"📍 Цена выхода: <code>{price:.5f}</code>\n\n"
                    f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                    f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                    f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                )
            else:
                msg_admin = (
                    f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"💵 Результат: <b>{profit:+.2f} USD</b> ({pips_be_str})\n"
                    f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Позиция закрыта в безубыток без риска для баланса.</i>"
                )
                msg_client = (
                    f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Позиция закрыта в безубыток без риска для баланса.</i>"
                )

            chart_bytes = None
            try:
                from market.data_fetcher import DataFetcher
                from utils.chart_generator import generate_outcome_chart
                df_c = await DataFetcher().fetch_ohlcv(symbol, "H1", limit=75)
                if df_c is not None and not df_c.empty:
                    status_str = "TP" if is_tp else ("SL" if is_sl else "CLOSED")
                    chart_bytes = generate_outcome_chart(
                        df=df_c, symbol=symbol, direction=direction,
                        entry=entry, stop_loss=sl_val, take_profit=tp_val,
                        close_price=price, status=status_str,
                        profit_usd=profit, timeframe="H1",
                        signal_time=(sig.get('created_at') or sig.get('activated_at')) if sig else None
                    )
            except Exception as chart_err:
                logger.error("Failed to generate deal closed chart: %s", chart_err)

            if ticket > 0:
                try:
                    from db.database import DB_PATH
                    import aiosqlite
                    async with aiosqlite.connect(str(DB_PATH)) as db:
                        await db.execute(
                            """INSERT OR IGNORE INTO broker_deals (ticket, symbol, deal_type, lot, price, profit_usd, close_time, magic, comment, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (ticket, symbol, direction, lot, price, profit, int(datetime.now(timezone.utc).timestamp()), 888001, reason, datetime.now(timezone.utc).isoformat())
                        )
                        await db.commit()
                except Exception:
                    pass

            await _send_telegram_notification(msg_client, photo_bytes=chart_bytes, text_admin=msg_admin)

        return web.json_response({"status": "ok", "acknowledged": True})
    except Exception as e:
        logger.error("bridge_post_report error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def api_history(request: web.Request) -> web.Response:
    """Возвращает журнал закрытых сделок с фильтрацией."""
    _check_public_api_auth(request)
    try:
        from db.database import get_trade_history
        from utils.trade_export import sanitize_trade_record

        params = request.query
        record_type = params.get("record_type", "LIVE")
        if record_type and record_type.upper() == "ALL":
            record_type = None
        start_time = int(params["start_time"]) if "start_time" in params else None
        end_time = int(params["end_time"]) if "end_time" in params else None
        direction = params.get("direction")
        strategy = params.get("strategy")
        session = params.get("session") or params.get("trading_session")
        result = params.get("result")
        symbol = params.get("symbol")
        limit = min(int(params.get("limit", 100)), 1000)
        offset = int(params.get("offset", 0))

        trades, total_count = await get_trade_history(
            record_type=record_type,
            start_time=start_time,
            end_time=end_time,
            direction=direction,
            strategy=strategy,
            trading_session=session,
            result=result,
            symbol=symbol,
            limit=limit,
            offset=offset,
        )

        sanitized = [sanitize_trade_record(t) for t in trades]
        return web.json_response({
            "status": "ok",
            "trades": sanitized,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("api_history error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def api_history_detail(request: web.Request) -> web.Response:
    """Возвращает детальную карточку сделки по id."""
    _check_public_api_auth(request)
    try:
        from db.database import get_trade_by_id
        from utils.trade_export import sanitize_trade_record

        trade_id = int(request.match_info["id"])
        trade = await get_trade_by_id(trade_id)
        if not trade:
            return web.json_response({"status": "error", "message": "Trade not found"}, status=404)
        return web.json_response({
            "status": "ok",
            "trade": sanitize_trade_record(trade),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.error("api_history_detail error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def api_export_csv(request: web.Request) -> web.Response:
    """Экспорт журнала сделок в безопасный CSV."""
    _check_public_api_auth(request)
    try:
        from db.database import get_trade_history
        from utils.trade_export import export_trades_to_csv

        params = request.query
        record_type = params.get("record_type", "LIVE")
        if record_type and record_type.upper() == "ALL":
            record_type = None

        date_from = params.get("date_from") or params.get("start_time")
        date_to = params.get("date_to") or params.get("end_time")

        trades, _ = await get_trade_history(
            record_type=record_type,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            direction=params.get("direction"),
            strategy=params.get("strategy"),
            trading_session=params.get("session") or params.get("trading_session"),
            result=params.get("result"),
            symbol=params.get("symbol"),
            limit=5000,
            offset=0,
        )
        csv_data = export_trades_to_csv(trades)
        filename = f"smart_trader_journal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        return web.Response(
            body=csv_data.encode("utf-8"),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("api_export_csv error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def api_export_json(request: web.Request) -> web.Response:
    """Экспорт журнала сделок в безопасный JSON."""
    _check_public_api_auth(request)
    try:
        from db.database import get_trade_history
        from utils.trade_export import export_trades_to_json

        params = request.query
        record_type = params.get("record_type", "LIVE")
        if record_type and record_type.upper() == "ALL":
            record_type = None

        date_from = params.get("date_from") or params.get("start_time")
        date_to = params.get("date_to") or params.get("end_time")

        trades, _ = await get_trade_history(
            record_type=record_type,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            direction=params.get("direction"),
            strategy=params.get("strategy"),
            trading_session=params.get("session") or params.get("trading_session"),
            result=params.get("result"),
            symbol=params.get("symbol"),
            limit=5000,
            offset=0,
        )
        json_data = export_trades_to_json(trades)
        filename = f"smart_trader_journal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        return web.Response(
            body=json_data.encode("utf-8"),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("api_export_json error: %s", e)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def handle_web_history(request: web.Request) -> web.Response:
    """HTML веб-панель журнала аудита и истории сделок."""
    _check_public_api_auth(request)
    try:
        from db.database import get_trade_history, get_stats
        from utils.trade_export import sanitize_trade_record

        params = request.query
        rec_type = params.get("record_type", "LIVE")
        q_rec_type = None if rec_type.upper() == "ALL" else rec_type
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        direction = params.get("direction")
        strategy = params.get("strategy")
        result = params.get("result")
        session = params.get("session") or params.get("trading_session")
        symbol = params.get("symbol")

        from urllib.parse import urlencode
        q_params = {}
        if rec_type: q_params["record_type"] = rec_type
        if date_from: q_params["date_from"] = date_from
        if date_to: q_params["date_to"] = date_to
        if direction: q_params["direction"] = direction
        if strategy: q_params["strategy"] = strategy
        if session: q_params["session"] = session
        if result: q_params["result"] = result
        if symbol: q_params["symbol"] = symbol
        q_str = urlencode(q_params)

        trades, total = await get_trade_history(
            record_type=q_rec_type,
            date_from=date_from,
            date_to=date_to,
            direction=direction,
            strategy=strategy,
            result=result,
            trading_session=session,
            symbol=symbol,
            limit=100
        )
        stats = await get_stats(record_type=rec_type if rec_type.upper() != "ALL" else "LIVE")

        rows_html = ""
        for t in trades:
            st = sanitize_trade_record(t)
            pnl = st.get("net_pnl")
            pnl_cls = "pnl-win" if pnl is not None and pnl > 0 else ("pnl-loss" if pnl is not None and pnl < 0 else "pnl-be")
            pnl_str = f"{pnl:+.2f} USD" if pnl is not None else "N/A"
            r_val = st.get("actual_r")
            r_str = f"{r_val:+.2f}R" if r_val is not None else "N/A"
            entry_p = f"{st.get('entry_price'):.5f}" if st.get('entry_price') is not None else "N/A"
            close_p = f"{st.get('close_price'):.5f}" if st.get('close_price') is not None else "N/A"
            comm = f"{st.get('commission'):.2f}" if st.get('commission') is not None else "N/A"
            swap = f"{st.get('swap'):.2f}" if st.get('swap') is not None else "N/A"
            slip = f"{st.get('slippage'):.1f}p" if st.get('slippage') is not None else "N/A"
            rows_html += f"""
            <tr>
                <td>#{st.get('position_id') or st.get('id')}</td>
                <td><span class="badge badge-type">{st.get('record_type')}</span></td>
                <td><b>{st.get('symbol')}</b></td>
                <td><span class="badge {'badge-buy' if 'BUY' in st.get('direction', '') else 'badge-sell'}">{st.get('direction')}</span></td>
                <td>{st.get('lot')}</td>
                <td>{entry_p}</td>
                <td>{close_p}</td>
                <td class="{pnl_cls}"><b>{pnl_str}</b></td>
                <td class="{pnl_cls}">{r_str}</td>
                <td>{comm} / {swap}</td>
                <td>{slip}</td>
                <td>{st.get('trading_session', 'N/A')}</td>
                <td>{st.get('duration_str', 'N/A')}</td>
                <td><span class="badge badge-res">{st.get('result', 'CLOSED')}</span></td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Trader Bot — Журнал Аудита Сделок</title>
    <style>
        :root {{
            --bg-primary: #0a0e17;
            --bg-secondary: #121826;
            --bg-card: #1a2234;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent: #2f81f7;
            --win: #238636;
            --loss: #da3633;
            --be: #d29922;
            --border: #30363d;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-primary);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
            padding: 24px;
        }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
        .header h1 {{ font-size: 22px; font-weight: 700; color: #fff; }}
        .header-actions a {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 8px 14px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 13px;
            margin-left: 8px;
            display: inline-block;
        }}
        .header-actions a:hover {{ background: var(--accent); color: #fff; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}
        .stat-card .title {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; }}
        .stat-card .value {{ font-size: 20px; font-weight: 700; margin-top: 6px; }}
        .filters {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px 16px;
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
        }}
        .filters select, .filters input {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-primary);
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 13px;
        }}
        .filters button {{
            background: var(--accent);
            border: none;
            color: #fff;
            padding: 7px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            font-size: 13px;
        }}
        th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: var(--bg-secondary); color: var(--text-secondary); font-weight: 600; text-transform: uppercase; font-size: 11px; }}
        tr:hover {{ background: rgba(255,255,255,0.02); }}
        .pnl-win {{ color: #3fb950; font-weight: 600; }}
        .pnl-loss {{ color: #f85149; font-weight: 600; }}
        .pnl-be {{ color: #d29922; font-weight: 600; }}
        .badge {{ padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; }}
        .badge-buy {{ background: rgba(35, 134, 54, 0.2); color: #3fb950; }}
        .badge-sell {{ background: rgba(218, 54, 51, 0.2); color: #f85149; }}
        .badge-type {{ background: rgba(47, 129, 247, 0.2); color: #58a6ff; }}
        .badge-res {{ background: rgba(139, 148, 158, 0.2); color: #8b949e; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🏛 SMART TRADER | ЖУРНАЛ АУДИТА СДЕЛОК</h1>
            <p style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">Институциональный аудит исполнения ордеров MetaTrader 5</p>
        </div>
        <div class="header-actions">
            <a href="/api/v1/export/csv?{q_str}">📥 Скачать CSV</a>
            <a href="/api/v1/export/json?{q_str}">📥 Скачать JSON</a>
            <a href="/api/v1/history?{q_str}">⚡ JSON API</a>
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="title">Всего Сделок ({rec_type})</div>
            <div class="value">{stats.get('closed', len(trades))}</div>
        </div>
        <div class="stat-card">
            <div class="title">Суммарный PnL</div>
            <div class="value {'pnl-win' if stats.get('total_profit_usd', 0) >= 0 else 'pnl-loss'}">{stats.get('total_profit_usd', 0.0):+.2f} USD</div>
        </div>
        <div class="stat-card">
            <div class="title">Win Rate</div>
            <div class="value">{stats.get('win_rate', 0.0):.1f}%</div>
        </div>
        <div class="stat-card">
            <div class="title">Profit Factor</div>
            <div class="value">{stats.get('profit_factor', 0.0):.2f}</div>
        </div>
        <div class="stat-card">
            <div class="title">Средний Actual R</div>
            <div class="value">{f"{stats.get('avg_actual_r'):+.2f}R" if stats.get('avg_actual_r') is not None else 'N/A'}</div>
        </div>
    </div>

    <form class="filters" method="GET" action="/history">
        <label>Тип:
            <select name="record_type">
                <option value="LIVE" {'selected' if rec_type == 'LIVE' else ''}>LIVE (Реал)</option>
                <option value="DEMO" {'selected' if rec_type == 'DEMO' else ''}>DEMO</option>
                <option value="TEST_SIGNAL" {'selected' if rec_type == 'TEST_SIGNAL' else ''}>TEST_SIGNAL</option>
                <option value="BACKTEST" {'selected' if rec_type == 'BACKTEST' else ''}>BACKTEST</option>
                <option value="ALL" {'selected' if rec_type == 'ALL' else ''}>ВСЕ ТИПЫ</option>
            </select>
        </label>
        <label>С даты:
            <input type="date" name="date_from" value="{date_from or ''}">
        </label>
        <label>По дату:
            <input type="date" name="date_to" value="{date_to or ''}">
        </label>
        <label>Направление:
            <select name="direction">
                <option value="">Все</option>
                <option value="BUY" {'selected' if direction == 'BUY' else ''}>BUY</option>
                <option value="SELL" {'selected' if direction == 'SELL' else ''}>SELL</option>
            </select>
        </label>
        <label>Сессия:
            <select name="session">
                <option value="">Все</option>
                <option value="London" {'selected' if session == 'London' else ''}>London</option>
                <option value="London-NY Overlap" {'selected' if session == 'London-NY Overlap' else ''}>London-NY Overlap</option>
                <option value="New York" {'selected' if session == 'New York' else ''}>New York</option>
                <option value="Asian" {'selected' if session == 'Asian' else ''}>Asian</option>
            </select>
        </label>
        <label>Стратегия:
            <input type="text" name="strategy" placeholder="ICT_SMC..." value="{strategy or ''}" style="width: 110px;">
        </label>
        <label>Итог:
            <select name="result">
                <option value="">Все</option>
                <option value="WIN" {'selected' if result == 'WIN' else ''}>WIN (Профит)</option>
                <option value="LOSS" {'selected' if result == 'LOSS' else ''}>LOSS (Убыток)</option>
                <option value="BREAKEVEN" {'selected' if result == 'BREAKEVEN' else ''}>BREAKEVEN (Б/У)</option>
            </select>
        </label>
        <button type="submit">Применить Фильтры</button>
    </form>

    <table>
        <thead>
            <tr>
                <th>Ticket / ID</th>
                <th>Тип</th>
                <th>Символ</th>
                <th>Сделка</th>
                <th>Лот</th>
                <th>Вход</th>
                <th>Выход</th>
                <th>Чистый PnL</th>
                <th>Факт R</th>
                <th>Комис / Своп</th>
                <th>Slip</th>
                <th>Сессия</th>
                <th>Длительность</th>
                <th>Итог</th>
            </tr>
        </thead>
        <tbody>
            {rows_html if rows_html else '<tr><td colspan="14" style="text-align: center; color: var(--text-secondary); padding: 30px;">Сделок не найдено</td></tr>'}
        </tbody>
    </table>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        logger.error("handle_web_history error: %s", e)
        return web.Response(text=f"<h1>Error loading history: {e}</h1>", content_type="text/html", status=500)


def create_webapp_app() -> web.Application:
    """Создает приложение aiohttp для локального MT5 моста."""
    app = web.Application()
    app.router.add_get("/", handle_status)
    app.router.add_get("/health", handle_status)
    app.router.add_get("/history", handle_web_history)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/v1/history", api_history)
    app.router.add_get("/api/v1/history/{id}", api_history_detail)
    app.router.add_get("/api/v1/export/csv", api_export_csv)
    app.router.add_get("/api/v1/export/json", api_export_json)
    app.router.add_get("/api/v1/bridge/orders", bridge_get_orders)
    app.router.add_post("/api/v1/bridge/orders", bridge_get_orders)
    app.router.add_post("/api/v1/bridge/report", bridge_post_report)
    return app


async def start_webapp_server(host: str = None, port: int = None, bot=None) -> web.AppRunner:
    """Запускает HTTP-сервер MT5 Bridge в фоне."""
    global _bot_instance
    if bot:
        _bot_instance = bot
    host = "127.0.0.1"
    port = port or config.WEBAPP_PORT
    app = create_webapp_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🚀 Smart Trader MT5 Bridge running at http://%s:%d", host, port)
    return runner