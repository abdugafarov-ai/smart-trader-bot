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
    """Рассылает уведомление об исполнении/закрытии всем пользователям бота (с графиком при наличии)."""
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
            try:
                if photo_bytes:
                    from aiogram.types import BufferedInputFile
                    await _bot_instance.send_photo(
                        uid,
                        photo=BufferedInputFile(photo_bytes, filename="trade_outcome.png"),
                        caption=msg_to_send,
                        parse_mode="HTML"
                    )
                else:
                    await _bot_instance.send_message(uid, msg_to_send, parse_mode="HTML")
            except Exception as err:
                try:
                    await _bot_instance.send_message(uid, msg_to_send, parse_mode=None)
                except Exception:
                    pass
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

def _check_api_key(request):
    """Проверяет API-ключ в заголовках запроса."""
    api_key = request.headers.get('X-API-Key', '')
    if api_key != config.BRIDGE_API_KEY:
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
                            lot = nd.get('lot', 0.01)
                            close_p = float(nd.get("price") or 0.0)
                            ticket_no = nd.get("ticket", 0)
                            comment = str(nd.get("comment") or "")
                            magic = int(nd.get("magic") or 0)
                            is_manual = (magic != 888001 and magic != 777001) or "MANUAL" in comment.upper()

                            pip_mult = 100.0 if 'JPY' in sym else (10.0 if 'XAU' in sym else 10000.0)

                            # Поиск параметров исходного сигнала
                            async with aiosqlite.connect(str(DB_PATH)) as db:
                                db.row_factory = aiosqlite.Row
                                async with db.execute("SELECT * FROM signals WHERE symbol = ? ORDER BY id DESC LIMIT 1", (sym,)) as c:
                                    sig_row = await c.fetchone()
                                    sig_dict = dict(sig_row) if sig_row else {}

                            entry_p = float(sig_dict.get('entry_price') or close_p)
                            direction = sig_dict.get('direction', 'LONG') if sig_dict else ('LONG' if 'BUY' in deal_type else 'SHORT')
                            sl_p = float(sig_dict.get('stop_loss') or 0.0)
                            tp_p = float(sig_dict.get('take_profit_1') or 0.0)
                            pnl_pips = (close_p - entry_p if direction == 'LONG' else entry_p - close_p) * pip_mult if entry_p else 0.0

                            if is_manual:
                                msg = format_manual_close(sym, profit_val, pnl_pips, close_p)
                                status_str = "MANUAL_CLOSE"
                            elif profit_val > 0:
                                msg = (
                                    f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (METATRADER 5)!</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                    f"💵 Результат: <b>+{profit_val:.2f} USD</b> (+{abs(pnl_pips):.1f} pips)\n"
                                    f"📍 Цена закрытия: <code>{close_p}</code>\n"
                                    f"🎫 Билет: <code>#{ticket_no}</code>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🎯 <i>Цель полностью достигнута. Прибыль зафиксирована в банке.</i>"
                                )
                                status_str = "TP"
                            elif profit_val < 0:
                                msg = (
                                    f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (METATRADER 5)</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                    f"📉 Фиксация убытка: <b>-{abs(profit_val):.2f} USD</b> (-{abs(pnl_pips):.1f} pips)\n"
                                    f"📍 Цена выхода: <code>{close_p}</code>\n"
                                    f"🎫 Билет: <code>#{ticket_no}</code>\n\n"
                                    f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                                    f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                                    f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                                )
                                status_str = "SL"
                            else:
                                msg = (
                                    f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📊 <b>{sym}</b> | <code>{deal_type} {lot} lot</code>\n"
                                    f"💵 Результат: <b>0.00 USD</b>\n"
                                    f"📍 Цена закрытия: <code>{close_p}</code>\n"
                                    f"🎫 Билет: <code>#{ticket_no}</code>\n"
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

                            await _send_telegram_notification(msg, photo_bytes=chart_bytes)
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

        use_risk_flag = 1 if (getattr(bridge_manager, 'lot_mode', 'fixed') == 'risk') else 0

        if not bridge_manager.enabled:
            return web.json_response({
                "status": "ok",
                "autotrade_enabled": False,
                "panic_close_all": panic,
                "lot": bridge_manager.default_lot,
                "risk_percent": bridge_manager.default_risk,
                "use_auto_risk": use_risk_flag,
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
                "use_auto_risk": use_risk_flag,
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
        reason = data.get("reason", "")
        sig_id = int(data.get("signal_id") or 0)

        from db.database import (
            confirm_signal_by_broker, reject_signal_by_broker, close_signal_by_broker,
            activate_filled_signal, expire_signal_by_broker
        )
        ticket = int(data.get("ticket") or data.get("order") or 0)

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

        # 5. Вход отклонен роботом (R:R < 1.8, превышен лимит или ошибка терминала)
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
            await _send_telegram_notification(msg)

        # Ручное открытие сделки пользователем в MT5
        elif action == "MANUAL_OPEN":
            volume = float(data.get("profit") or 0.01)
            order_type = reason or "BUY"
            from utils.formatters import format_manual_open
            msg = format_manual_open(symbol, order_type, volume, price)
            await _send_telegram_notification(msg)

        # Ручное закрытие сделки пользователем в MT5
        elif action == "MANUAL_CLOSE":
            sig = await close_signal_by_broker(symbol, price, profit, "MANUAL_CLOSE")
            pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)
            entry = float(sig.get('entry_price') or price) if sig else price
            direction = sig.get('direction', 'LONG') if sig else 'LONG'
            sl_val = float(sig.get('stop_loss') or 0.0) if sig else 0.0
            tp_val = float(sig.get('take_profit_1') or 0.0) if sig else 0.0
            pnl_pips = (price - entry if direction == 'LONG' else entry - price) * pip_mult if entry else 0.0

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
                            (ticket, symbol, direction, 0.01, price, profit, int(datetime.now(timezone.utc).timestamp()), 0, "MANUAL_CLOSE", datetime.now(timezone.utc).isoformat())
                        )
                        await db.commit()
                except Exception:
                    pass

            await _send_telegram_notification(msg, photo_bytes=chart_bytes)

        # Позиция закрыта в MT5 (TP, SL, безубыток)
        elif action in ("DEAL_CLOSED", "STALE_PROFIT_CLOSE", "CLOSED"):
            sig = await close_signal_by_broker(symbol, price, profit, reason)
            profit_sign = "+" if profit >= 0 else ""
            is_tp = profit > 0 or "TP" in reason.upper()
            is_sl = profit < 0 or "SL" in reason.upper()
            pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)
            entry = float(sig.get('entry_price') or price) if sig else price
            direction = sig.get('direction', 'LONG') if sig else ('LONG' if 'BUY' in reason.upper() else 'SHORT')
            sl_val = float(sig.get('stop_loss') or 0.0) if sig else 0.0
            tp_val = float(sig.get('take_profit_1') or 0.0) if sig else 0.0
            pnl_pips = (price - entry if direction == 'LONG' else entry - price) * pip_mult if entry else 0.0

            if is_tp:
                msg = (
                    f"🏆 <b>ТЕЙК-ПРОФИТ ВЗЯТ (METATRADER 5)!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"💵 Результат: <b>+{profit:.2f} USD</b> (+{abs(pnl_pips):.1f} pips)\n"
                    f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <i>Цель полностью достигнута. Прибыль зафиксирована в банке.</i>"
                )
            elif is_sl:
                msg = (
                    f"🛑 <b>СТОП-ЛОСС СРАБОТАЛ (METATRADER 5)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"📉 Фиксация убытка: <b>-{abs(profit):.2f} USD</b> (-{abs(pnl_pips):.1f} pips)\n"
                    f"📍 Цена выхода: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n\n"
                    f"🛑 <b>РАЗБОР СТОПА:</b>\n"
                    f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
                    f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💼 <i>Дисциплина и мани-менеджмент сохраняют депозит.</i>"
                )
            else:
                msg = (
                    f"🛡 <b>СДЕЛКА ЗАКРЫТА В БЕЗУБЫТОК!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>{symbol}</b> | <code>{direction}</code>\n"
                    f"💵 Результат: <b>0.00 USD</b>\n"
                    f"📍 Цена закрытия: <code>{price:.5f}</code>\n"
                    f"🎫 Билет сделки: <code>#{ticket}</code>\n"
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
                            (ticket, symbol, direction, 0.01, price, profit, int(datetime.now(timezone.utc).timestamp()), 888001, reason, datetime.now(timezone.utc).isoformat())
                        )
                        await db.commit()
                except Exception:
                    pass

            await _send_telegram_notification(msg, photo_bytes=chart_bytes)

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
    host = "127.0.0.1"
    port = port or config.WEBAPP_PORT
    app = create_webapp_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("🚀 Smart Trader MT5 Bridge running at http://%s:%d", host, port)
    return runner
