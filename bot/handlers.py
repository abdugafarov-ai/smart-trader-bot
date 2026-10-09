from datetime import datetime, timezone, timedelta
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
import logging
import asyncio
import html
logger = logging.getLogger(__name__)

from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies import ALL_STRATEGIES, STRATEGY_MAP
from sessions.trading_sessions import TradingSessions
from bot.guide import get_guide_step, get_total_steps, GUIDE_STEPS
from bot.keyboards import (
    main_menu_keyboard, admin_menu_keyboard, client_menu_keyboard,
    back_keyboard, guide_keyboard, admin_approve_keyboard,
    analysis_result_keyboard, terminal_dashboard_keyboard, panic_confirm_keyboard,
    autotrade_keyboard, cancel_custom_lot_keyboard, admin_users_crm_keyboard, admin_user_card_keyboard,
    client_subscription_keyboard, client_support_keyboard
)
from utils.formatters import (
    format_indicators, format_strategy, format_multi_tf_analysis,
    format_notification, format_signals_summary, format_news_alert,
    format_welcome, format_help,
    format_stats, format_history,
    format_crm_user_card, format_my_subscription
)
from strategies.base import (
    FullAnalysisResult, MultiTFResult, TimeframeAnalysis
)
import config

router = Router()
fetcher = DataFetcher()
sessions = TradingSessions(config.TIMEZONE)

user_state: dict[int, dict] = {}

def get_user_state(user_id: int) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {
            "symbol": config.DEFAULT_SYMBOLS[0] if config.DEFAULT_SYMBOLS else "EURUSD",
            "timeframe": config.DEFAULT_TIMEFRAME,
            "guide_step": 0
        }
    return user_state[user_id]

async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None, parse_mode="HTML"):
    """Отвечает на callback и отправляет новое сообщение в чат, сохраняя историю и предыдущие вкладки."""
    try:
        await callback.answer()
    except Exception:
        pass

    msg = callback.message
    if not msg:
        return None

    # Снимаем клавиатуру с предыдущего сообщения, чтобы кнопки не дублировались,
    # но сам ТЕКСТ предыдущего экрана остаётся в ленте чата нетронутым!
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        return await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e_html:
        try:
            logger.warning("safe_edit HTML parse failed (%s), falling back to plain text", e_html)
            return await msg.answer(text, reply_markup=reply_markup, parse_mode=None)
        except Exception as e:
            logger.error("safe_edit message send failed: %s", e)
            return None

def split_message_text(text: str, max_chunk: int = 4000) -> list[str]:
    """Разбивает длинный текст по строкам/абзацам, не разрывая HTML-теги."""
    if len(text) <= max_chunk:
        return [text]
    chunks = []
    lines = text.split("\n")
    cur = ""
    for line in lines:
        if len(cur) + len(line) + 1 > max_chunk:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [text]

from utils.emoji_markers import get_random_marker

async def run_multi_tf_analysis(symbol: str) -> MultiTFResult:
    tf_analyses = []
    
    for tf in config.MULTI_TF_LIST:
        df = await fetcher.fetch_ohlcv(symbol, tf)
        if df is None or df.empty or len(df) < 20:
            continue
        df = TechnicalIndicators.calculate_all(df)
        indicators = TechnicalIndicators.calculate(df)
        
        strategy_results = []
        for strategy in ALL_STRATEGIES:
            result = strategy.analyze(df, symbol, tf)
            strategy_results.append(result)
        
        # Получаем сигнал ICT/SMC как главный якорный сигнал таймфрейма
        ict_res = next((r for r in strategy_results if r.name == 'ICT / Smart Money Concepts'), strategy_results[0])
        tf_dir = ict_res.signal.direction
        tf_conf = ict_res.signal.confidence
        order_type = ict_res.signal.order_type
        
        tf_analyses.append(TimeframeAnalysis(
            timeframe=tf, direction=tf_dir, order_type=order_type, confidence=tf_conf,
            strategies=strategy_results, indicators=indicators
        ))
    
    if not tf_analyses:
        return MultiTFResult(symbol=symbol, tag_emoji=get_random_marker())
    
    non_neutral = [t for t in tf_analyses if t.direction != 'NEUTRAL']
    if non_neutral:
        long_tfs = sum(1 for t in non_neutral if t.direction == 'LONG')
        short_tfs = sum(1 for t in non_neutral if t.direction == 'SHORT')
        if long_tfs > short_tfs:
            overall_dir = 'LONG'
            tf_agree = long_tfs
        elif short_tfs > long_tfs:
            overall_dir = 'SHORT'
            tf_agree = short_tfs
        else:
            overall_dir = 'NEUTRAL'
            tf_agree = 0
    else:
        overall_dir = 'NEUTRAL'
        tf_agree = 0
    
    # Институциональная Top-Down модель:
    # Тренд определяется старшими TF, а вход (Entry/SL/TP) берется с рабочего таймфрейма (H1 -> M15 -> H4)
    best_tf = None
    for target_tf in ["H1", "M15", "H4", "D1"]:
        cand = next((t for t in tf_analyses if t.timeframe == target_tf and t.direction == overall_dir), None)
        if cand:
            ict_cand = next((s for s in cand.strategies if s.name == 'ICT / Smart Money Concepts'), None)
            if ict_cand and ict_cand.signal.direction == overall_dir and ict_cand.signal.entry:
                best_tf = cand
                break
                
    if not best_tf:
        matching_tfs = [t for t in tf_analyses if t.direction == overall_dir]
        best_tf = max(matching_tfs, key=lambda t: t.confidence) if matching_tfs else tf_analyses[0]
    
    # Берем институциональные параметры ICT/SMC из рабочего таймфрейма
    best_ict = next((s for s in best_tf.strategies if s.name == 'ICT / Smart Money Concepts'), None)
    
    if best_ict and best_ict.signal.direction == overall_dir and overall_dir != 'NEUTRAL':
        entry = best_ict.signal.entry
        sl = best_ict.signal.stop_loss
        tp1 = best_ict.signal.take_profit_1
        tp2 = best_ict.signal.take_profit_2
        rr1 = round(best_ict.signal.risk_reward, 1) if best_ict.signal.risk_reward else None
        current_price = best_ict.signal.current_price
        order_type = best_ict.signal.order_type
    else:
        entry = sl = tp1 = tp2 = rr1 = current_price = None
        order_type = "BUY_LIMIT" if overall_dir == "LONG" else "SELL_LIMIT"

    # Рассчитываем rr2
    if entry and sl and tp2 and abs(entry - sl) > 0:
        rr2 = round(abs(tp2 - entry) / abs(entry - sl), 1)
    else:
        rr2 = None
    
    # Пипсы
    pip_mult = 100.0 if 'JPY' in symbol else (10.0 if 'XAU' in symbol else 10000.0)

    pips_sl = round(abs(entry - sl) * pip_mult, 1) if entry and sl else None
    pips_tp1 = round(abs(tp1 - entry) * pip_mult, 1) if entry and tp1 else None
    pips_tp2 = round(abs(tp2 - entry) * pip_mult, 1) if entry and tp2 else None
    
    # Звезды уверенности: ТОЛЬКО 4★ и 5★ (строгий порог по просьбе трейдера)
    # Требуется: tf_agree >= 2 И R:R >= 2.0
    if overall_dir != 'NEUTRAL' and rr1 and rr1 >= 2.0 and tf_agree >= 2:
        overall_stars = 5 if tf_agree >= 3 else 4
    else:
        overall_stars = 0
        overall_dir = "NEUTRAL"
        entry = sl = tp1 = tp2 = rr1 = None

    # Институциональный Daily Bias (D1) фильтр:
    # Запрещено открывать позицию против сильного дневного уклона (D1 confidence >= 3)
    d1_cand = next((t for t in tf_analyses if t.timeframe == "D1"), None)
    if d1_cand and d1_cand.direction != "NEUTRAL" and d1_cand.direction != overall_dir:
        if d1_cand.confidence >= 3:
            overall_dir = "NEUTRAL"
            overall_stars = 0
            entry = sl = tp1 = tp2 = rr1 = None

    verdicts = []
    for s in best_tf.strategies:
        if s.signal.direction == overall_dir and s.signal.direction != 'NEUTRAL':
            verdicts.append((s.emoji, s.name, f"✅ {s.signal.direction}"))
        else:
            verdicts.append((s.emoji, s.name, "❌ нейтрально"))
    
    return MultiTFResult(
        symbol=symbol,
        tag_emoji=get_random_marker(),
        tf_analyses=tf_analyses,
        overall_direction=overall_dir,
        order_type=order_type,
        current_price=current_price,
        overall_stars=overall_stars,
        tf_agreement=tf_agree,
        total_tfs=len(tf_analyses),
        strategy_agreement=1 if overall_dir != 'NEUTRAL' else 0,
        total_strategies=1,
        entry=entry, stop_loss=sl,
        take_profit_1=tp1, take_profit_2=tp2,
        risk_reward_1=rr1, risk_reward_2=rr2,
        pips_sl=pips_sl, pips_tp1=pips_tp1, pips_tp2=pips_tp2,
        strategy_verdicts=verdicts,
        session_text=sessions.format_sessions_text() + DataFetcher.get_weekend_note()
    )

async def run_full_analysis(symbol: str, timeframe: str) -> FullAnalysisResult:
    df = await fetcher.fetch_ohlcv(symbol, timeframe)
    if df is None or df.empty:
        return None
    df = TechnicalIndicators.calculate_all(df)
    indicators = TechnicalIndicators.calculate(df)
    
    strategy_results = []
    for strategy in ALL_STRATEGIES:
        result = strategy.analyze(df, symbol, timeframe)
        strategy_results.append(result)
    
    session_text = sessions.format_sessions_text()
    
    directions = [r.signal.direction for r in strategy_results if r.signal.direction != 'NEUTRAL']
    longs = directions.count('LONG')
    shorts = directions.count('SHORT')
    
    if longs > shorts:
        overall = 'LONG'
        agreeing = longs
    elif shorts > longs:
        overall = 'SHORT'
        agreeing = shorts
    else:
        overall = 'NEUTRAL'
        agreeing = 0
    
    entry_vals = [r.signal.entry for r in strategy_results if r.signal.entry and r.signal.direction == overall]
    sl_vals = [r.signal.stop_loss for r in strategy_results if r.signal.stop_loss and r.signal.direction == overall]
    tp1_vals = [r.signal.take_profit_1 for r in strategy_results if r.signal.take_profit_1 and r.signal.direction == overall]
    tp2_vals = [r.signal.take_profit_2 for r in strategy_results if r.signal.take_profit_2 and r.signal.direction == overall]
    
    entry_s = sum(entry_vals) / len(entry_vals) if entry_vals else None
    sl_s = sum(sl_vals) / len(sl_vals) if sl_vals else None
    tp1_s = sum(tp1_vals) / len(tp1_vals) if tp1_vals else None
    tp2_s = sum(tp2_vals) / len(tp2_vals) if tp2_vals else None
    rr_s = None
    if entry_s and sl_s and tp1_s and abs(entry_s - sl_s) > 0:
        rr_s = abs(tp1_s - entry_s) / abs(entry_s - sl_s)
    
    return FullAnalysisResult(
        symbol=symbol,
        timeframe=timeframe,
        indicators=indicators,
        strategies=strategy_results,
        session_text=session_text,
        overall_direction=overall,
        overall_confidence=min(5, agreeing),
        strategies_agreeing=agreeing,
        total_strategies=len(ALL_STRATEGIES),
        entry_suggestion=entry_s,
        stop_suggestion=sl_s,
        tp1_suggestion=tp1_s,
        tp2_suggestion=tp2_s,
        rr_suggestion=rr_s,
    )

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id

    # Админ — всегда в Пульт Администратора
    if user_id == config.ADMIN_ID:
        await message.answer(format_welcome(is_admin=True), reply_markup=admin_menu_keyboard(), parse_mode="HTML")
        return

    from bot.keyboards import guest_welcome_keyboard
    from db.users import get_user_status
    status = await get_user_status(user_id)

    if status == "approved":
        await message.answer(format_welcome(is_admin=False), reply_markup=client_menu_keyboard(), parse_mode="HTML")
    elif status == "revoked":
        await message.answer(
            "🔒 <b>Доступ к терминалу приостановлен администратором.</b>\n\n"
            "Действие тарифа завершено или доступ был деактивирован.\n"
            "Для возобновления подписки выберите подходящий тариф ниже или свяжитесь с поддержкой.",
            reply_markup=guest_welcome_keyboard(),
            parse_mode="HTML"
        )
    elif status == "expired":
        await message.answer(
            "⏳ <b>Срок действия вашей подписки истёк.</b>\n\n"
            "Для продления доступа выберите подходящий тариф ниже или обратитесь к администратору.",
            reply_markup=guest_welcome_keyboard(),
            parse_mode="HTML"
        )
    elif status == "pending":
        await message.answer(
            "⏳ <b>Ваша заявка уже находится на рассмотрении.</b>\n\n"
            "Администратор скоро проверит доступ и активирует тариф.\n"
            "Ожидайте мгновенного уведомления в этом чате! 🔔",
            reply_markup=guest_welcome_keyboard(),
            parse_mode="HTML"
        )
    elif status == "rejected":
        await message.answer(
            "❌ <b>Ваша заявка была отклонена ранее.</b>\n\n"
            "Свяжитесь с администратором для уточнения деталей.",
            reply_markup=guest_welcome_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "🏛 <b>ДОБРО ПОЖАЛОВАТЬ В SMART TRADER TERMINAL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🎯 <b>Профессиональные торговые сигналы Smart Money / ICT:</b>\n"
            "• 17 торговых инструментов (Forex Majors + Crosses + Золото)\n"
            "• Точные уровни входа (Entry, Stop Loss, Take Profit 1 & 2)\n"
            "• Анализ институционального капитала в реальном времени\n\n"
            "🎁 <b>Активируйте бесплатный тест-драйв на 3 дня</b> или отправьте заявку на доступ нажатием кнопки ниже 👇",
            reply_markup=guest_welcome_keyboard(),
            parse_mode="HTML"
        )


@router.message(Command("sessions"))
async def cmd_sessions(message: Message):
    from bot.keyboards import back_keyboard
    text = sessions.format_sessions_text()
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")


@router.message(Command("news"))
async def cmd_news(message: Message):
    from news.economic_calendar import EconomicCalendar
    from bot.keyboards import back_keyboard
    try:
        calendar = EconomicCalendar(config.TIMEZONE)
        events = await calendar.get_events_for_display()
        if not events:
            text = (
                "📰 <b>МАКРОЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "🟢 <i>Важных новостей (High Impact) на ближайшее время не обнаружено. Рынок спокоен.</i>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            header = (
                "📰 <b>МАКРОЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ (HIGH IMPACT)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ <i>Отображаются только ключевые события высокой важности (красные новости):</i>\n\n"
            )
            texts = [header]
            for e in events[:10]:
                texts.append(calendar.format_event(e) + "\n")
            texts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Во время выхода красных новостей робот защищает сделки и избегает опасных импульсов.</i>")
            text = "\n".join(texts)
        await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка загрузки календаря: {e}", reply_markup=back_keyboard(), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message):
    from bot.keyboards import help_menu_keyboard
    await message.answer(format_help(), reply_markup=help_menu_keyboard(), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    from db.database import get_stats
    stats = await get_stats()
    text = format_stats(stats)
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

@router.message(Command("history"))
async def cmd_history(message: Message):
    from db.database import get_recent_signals
    signals = await get_recent_signals(limit=15)
    text = format_history(signals)
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")


@router.message(Command("reset_stats"))
async def cmd_reset_stats(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return
    from db.database import reset_all_stats
    await reset_all_stats()
    await message.answer(
        "🧹 <b>СТАТИСТИКА И ИСТОРИЯ УСПЕШНО СБРОШЕНЫ!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Все прошлые закрытые сделки и статистика вин-рейта очищены.\n"
        "📊 Отсчёт статистики начинается с <b>нуля (0.0% Win Rate, 0 сделок)</b>.\n\n"
        "ℹ️ <i>Текущие открытые позиции и лимитные ордера в MT5 сохранены.</i>",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )



@router.message(Command("autotrade"))
async def cmd_autotrade(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return
    parts = message.text.split()
    from trading.execution_bridge import bridge_manager
    if len(parts) > 1:
        sub = parts[1].lower()
        if sub in ("on", "1", "start", "enable"):
            bridge_manager.set_enabled(True)
            await message.answer("✅ <b>АВТОПИЛОТ ВКЛЮЧЕН!</b>\nВсе подтвержденные сигналы передаются в MetaTrader советник.", parse_mode="HTML")
            return
        elif sub in ("off", "0", "stop", "disable"):
            bridge_manager.set_enabled(False)
            await message.answer("🛑 <b>АВТОПИЛОТ ВЫКЛЮЧЕН.</b>\nСоветник не будет открывать новые сделки.", parse_mode="HTML")
            return

    status = bridge_manager.get_status()
    st_badge = "🟢 ВКЛЮЧЕН" if status["enabled"] else "🔴 ВЫКЛЮЧЕН"
    text = (
        f"🤖 <b>СТАТУС МОСТА АВТО-ТОРГОВЛИ (METATRADER BRIDGE):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Статус: <b>{st_badge}</b>\n"
        f"• Риск на сделку: <code>{status['risk_percent']}%</code>\n"
        f"• Торговый лот: <code>{status['default_lot']}</code>\n"
        f"• Подключено терминалов: <code>{status['terminals_connected']}</code>\n\n"
        f"Управление:\n"
        f"• <code>/autotrade on</code> — включить автопилот\n"
        f"• <code>/autotrade off</code> — выключить автопилот\n"
        f"• <code>/risk 1.5</code> — установить процент риска (от 0.1% до 5.0%)\n"
        f"• <code>/lot 0.05</code> — установить фиксированный лот"
    )
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

@router.message(Command("risk"))
async def cmd_risk(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return
    parts = message.text.split()
    if len(parts) > 1:
        try:
            val = float(parts[1].replace(',', '.'))
            from trading.execution_bridge import bridge_manager
            from db.database import set_bot_setting
            bridge_manager.set_risk(val)
            await set_bot_setting("trading_risk", str(bridge_manager.default_risk))
            await set_bot_setting("lot_mode", "risk")
            await message.answer(f"✅ Риск на сделку установлен: <b>{bridge_manager.default_risk:.1f}%</b> (Режим: Динамический)", parse_mode="HTML")
            return
        except ValueError:
            pass
    await message.answer("Использование: <code>/risk 1.0</code> (процент риска от 0.1% до 5.0%)", parse_mode="HTML")

@router.message(Command("lot"))
async def cmd_lot(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return
    parts = message.text.split()
    if len(parts) > 1:
        try:
            val = float(parts[1].replace(',', '.'))
            from trading.execution_bridge import bridge_manager
            from db.database import set_bot_setting
            bridge_manager.set_lot(val)
            await set_bot_setting("trading_lot", str(bridge_manager.default_lot))
            await set_bot_setting("lot_mode", "fixed")
            await message.answer(f"✅ Фиксированный лот установлен: <b>{bridge_manager.default_lot:.2f}</b> (Режим: Фиксированный)", parse_mode="HTML")
            return
        except ValueError:
            pass
    await message.answer("Использование: <code>/lot 0.02</code> (размер лота от 0.01 до 10.0)", parse_mode="HTML")

@router.message(Command("terminal"))
@router.message(Command("account"))
async def cmd_terminal(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import terminal_dashboard_keyboard
    text = bridge_manager.format_terminal_dashboard()
    await message.answer(text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    is_admin = (callback.from_user.id == config.ADMIN_ID)
    kb = admin_menu_keyboard() if is_admin else client_menu_keyboard()
    title = "🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА (ADMIN):</b>" if is_admin else "🏛 <b>ГЛАВНОЕ МЕНЮ:</b>"
    await safe_edit(callback, title, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("guide:"))
async def cb_guide(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    
    if action == "start":
        state["guide_step"] = 0
        await safe_edit(callback, get_guide_step(0), reply_markup=guide_keyboard(0), parse_mode="HTML")
    elif action == "next":
        state["guide_step"] += 1
        step = state["guide_step"]
        if step < get_total_steps():
            await safe_edit(callback, get_guide_step(step), reply_markup=guide_keyboard(step), parse_mode="HTML")
        else:
            await safe_edit(callback, "🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")
    elif action == "skip":
        await safe_edit(callback, "🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")

async def get_crm_view(tab: str = "all", page: int = 1, page_size: int = 8):
    """Формирует данные и клавиатуру для CRM панели администратора с поиском, финансами, вкладками и пагинацией."""
    from db.users import get_all_users_filtered, get_all_users, get_crm_finance_summary
    from bot.keyboards import admin_users_crm_keyboard

    all_users = await get_all_users()
    active_cnt = sum(1 for u in all_users if u.get("status") == "approved" and u.get("telegram_id") != config.ADMIN_ID)
    revoked_cnt = sum(1 for u in all_users if u.get("status") in ("revoked", "expired"))
    pending_cnt = sum(1 for u in all_users if u.get("status") == "pending")
    total_clients = len([u for u in all_users if u.get("telegram_id") != config.ADMIN_ID])

    fin = await get_crm_finance_summary()

    filtered_users = await get_all_users_filtered(filter_type=tab)
    total_items = len(filtered_users)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * page_size
    page_users = filtered_users[start_idx:start_idx + page_size]

    tab_titles = {
        "all": "Все клиенты",
        "active": "Активные подписчики",
        "revoked": "Отключенные / Истёкшие",
        "pending": "Новые заявки"
    }
    tab_title = tab_titles.get(tab, "Клиенты")

    month_name = fin.get('month_name') or 'текущий месяц'
    text = (
        "👥 <b>УПРАВЛЕНИЕ КЛИЕНТАМИ И ПОДПИСКАМИ (CRM)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Выручка CRM:</b> <b>${fin['total_usd']:.2f}</b> (за {month_name}: <code>${fin['month_usd']:.2f}</code>)\n"
        f"📊 <b>Клиентов в базе:</b> {total_clients} | <b>Платящих:</b> {fin['unique_clients']}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• 🟢 Активных подписок: <b>{active_cnt}</b>\n"
        f"• 🔴 Отключенных / Истёкших: <b>{revoked_cnt}</b>\n"
        f"• ⏳ Ожидающих заявок: <b>{pending_cnt}</b>\n\n"
        f"📂 <b>Вкладка:</b> {tab_title} ({total_items})\n\n"
        "<i>Нажмите на клиента для карточки, поиска, заметок или продления 👇</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = admin_users_crm_keyboard(page_users, tab=tab, page=page, total_pages=total_pages)
    return text, kb


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu_actions(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    if action in ("analyze", "indicators", "strategy", "signals", "equity", "backtest"):
        text = (
            "ℹ️ <b>Раздел отключен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Робот переведён на полностью автономный режим через терминал MetaTrader 5.\n"
            "Ручной анализ и отдельные индикаторы больше не требуются — робот сам находит "
            "сетапы и выставляет ордера в MT5.\n\n"
            "Используйте кнопку <b>«🖥 Мой Терминал MT5»</b> для контроля позиций и баланса."
        )
        await safe_edit(callback, text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "sessions":
        text = sessions.format_sessions_text()
        await safe_edit(callback, text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "news":
        try:
            from news.economic_calendar import EconomicCalendar
            calendar = EconomicCalendar(config.TIMEZONE)
            events = await calendar.get_events_for_display()
            if not events:
                await safe_edit(
                    callback,
                    "📰 <b>МАКРОЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🟢 <i>Важных новостей (High Impact) на ближайшее время не обнаружено. Рынок спокоен.</i>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    reply_markup=back_keyboard(),
                    parse_mode="HTML"
                )
                return
            header = (
                "📰 <b>МАКРОЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ (HIGH IMPACT)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ <i>Отображаются только ключевые события высокой важности (красные новости):</i>\n\n"
            )
            texts = [header]
            for e in events[:10]:
                texts.append(calendar.format_event(e) + "\n")
            texts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 <i>Во время выхода красных новостей робот защищает сделки и избегает опасных импульсов.</i>")
            full_text = "\n".join(texts)
            await safe_edit(callback, full_text, reply_markup=back_keyboard(), parse_mode="HTML")
        except Exception as e:
            await safe_edit(callback, f"⚠️ Ошибка загрузки календаря: {e}", reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "stats":
        from db.database import get_stats
        stats = await get_stats()
        text = format_stats(stats)
        await safe_edit(callback, text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "history":
        from db.database import get_recent_signals
        signals = await get_recent_signals(limit=15)
        text = format_history(signals)
        await safe_edit(callback, text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "terminal":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        from trading.execution_bridge import bridge_manager
        from bot.keyboards import terminal_dashboard_keyboard
        text = bridge_manager.format_terminal_dashboard()
        await safe_edit(callback, text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")
    elif action == "help":
        from bot.keyboards import help_menu_keyboard
        is_admin = (callback.from_user.id == config.ADMIN_ID)
        await safe_edit(callback, format_help(is_admin=is_admin), reply_markup=help_menu_keyboard(), parse_mode="HTML")
    elif action == "my_sub":
        from db.users import get_user
        u_data = await get_user(callback.from_user.id)
        if not u_data:
            u_data = {"telegram_id": callback.from_user.id, "tariff": "PRO", "status": "approved"}
        text = format_my_subscription(u_data)
        await safe_edit(callback, text, reply_markup=client_subscription_keyboard(), parse_mode="HTML")
    elif action == "support":
        username = config.ADMIN_USERNAME
        admin_link = f"@{username}" if username else f"ID: {config.ADMIN_ID}"
        text = (
            "💬 <b>СЛУЖБА ПОДДЕРЖКИ & МЕНЕДЖЕР</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "По всем вопросам работы бота, подключения к счёту или продления тарифа обращайтесь к администратору:\n\n"
            f"👤 <b>Связь с нами:</b> {admin_link}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await safe_edit(callback, text, reply_markup=client_support_keyboard(), parse_mode="HTML")
    elif action == "crm":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        text, kb = await get_crm_view(tab="all", page=1)
        await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")
    elif action == "broadcast":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        from bot.keyboards import broadcast_cancel_keyboard
        text = (
            "📢 <b>МАССОВАЯ РАССЫЛКА КЛИЕНТАМ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Для отправки объявления всем активным клиентам отправьте команду:\n\n"
            "<code>/broadcast Ваш текст объявления...</code>\n\n"
            "<i>Сообщение будет мгновенно доставлено всем одобренным клиентам бота.</i>"
        )
        await safe_edit(callback, text, reply_markup=broadcast_cancel_keyboard(), parse_mode="HTML")
    elif action == "autotrade":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        from trading.execution_bridge import bridge_manager
        from bot.keyboards import autotrade_keyboard
        from db.database import get_bot_setting
        trading_mode = await get_bot_setting("trading_mode", "micro")
        status_emoji = "🟢 ВКЛЮЧЕН (АКТИВЕН)" if bridge_manager.enabled else "🔴 ПРИОСТАНОВЛЕН (ПАУЗА)"

        if trading_mode == "micro":
            profile_name = "🛡️ Режим «Микро-депозит»"
            profile_desc = (
                "• Золото (XAUUSD): ❌ <b>ОТКЛЮЧЕНО</b> (защита депозита)\n"
                "• Макс. сделок в рынке: <b>1</b> (свободная маржа)\n"
                "• Макс. стоп-лосс: <b>≤ 18 пипсов</b> (риск ~$1.80)\n"
                "• Режим сделок: <b>Pure Swing</b> (свободный ход до Take Profit)"
            )
            pool_str = "16 валютных пар (без Золота)"
        else:
            profile_name = "👑 Режим: Институционал"
            profile_desc = (
                "• Золото (XAUUSD): ✅ <b>ВКЛЮЧЕНО</b>\n"
                "• Макс. сделок в рынке: <b>до 7</b>\n"
                "• Макс. стоп-лосс: по структуре ICT/SMC\n"
                "• Режим сделок: <b>Pure Swing</b> (удержание до полного Take Profit)"
            )
            pool_str = "17 пар (Форекс + Золото)"

        lot_display = f"{bridge_manager.default_lot:.2f}"
        if getattr(bridge_manager, 'lot_mode', 'fixed') == "fixed":
            lot_status_badge = f"<b>{lot_display}</b> (✅ Активен: Фиксированный)"
            risk_status_badge = f"{bridge_manager.default_risk:.1f}%"
        else:
            lot_status_badge = f"Динамический (по риску)"
            risk_status_badge = f"<b>{bridge_manager.default_risk:.1f}%</b> (✅ Активен: Динамический)"

        text = (
            f"⚙️ <b>НАСТРОЙКИ АВТОПИЛОТА (MT5 BRIDGE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 <b>Статус авто-торговли:</b> {status_emoji}\n"
            f"🎯 <b>Активный профиль:</b> <b>{profile_name}</b>\n"
            f"{profile_desc}\n\n"
            f"📊 <b>Рабочий лот:</b> {lot_status_badge}\n"
            f"⚖️ <b>Риск на сделку:</b> {risk_status_badge}\n"
            f"💱 <b>Инструментов в пуле:</b> <code>{pool_str}</code>\n"
        f"🛑 <b>Дневной лимит просадки:</b> <code>{bridge_manager.max_daily_loss_pct:.1f}%</code> "
        f"{'(🚨 ЗАБЛОКИРОВАН)' if bridge_manager.daily_loss_locked else '(🟢 Норма)'}\n\n"
            f"Используйте кнопки ниже для быстрого управления 👇\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = autotrade_keyboard(
            enabled=bridge_manager.enabled,
            mode=trading_mode,
            current_lot=bridge_manager.default_lot,
            current_risk=bridge_manager.default_risk,
            lot_mode=getattr(bridge_manager, 'lot_mode', 'fixed'),
            daily_limit=getattr(bridge_manager, 'max_daily_loss_pct', 3.0),
            daily_locked=getattr(bridge_manager, 'daily_loss_locked', False)
        )
        await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")
    elif action == "server_status":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        from utils.server_health import get_server_health_dashboard
        from bot.keyboards import server_status_keyboard
        text = await get_server_health_dashboard()
        await safe_edit(callback, text, reply_markup=server_status_keyboard(), parse_mode="HTML")
    elif action == "clear_cache":
        if callback.from_user.id != config.ADMIN_ID:
            await callback.answer("❌ Доступно только администратору!", show_alert=True)
            return
        from utils.server_health import execute_cache_cleanup
        from bot.keyboards import server_cleaned_keyboard
        await callback.answer("🧹 Очищаю кэш и временный мусор...", show_alert=False)
        res = await execute_cache_cleanup()
        freed_str = res.get("bytes_freed_str", "0 B")
        files_cnt = res.get("files_removed", 0)
        disk_free = res.get("disk_free_str", "—")
        actions_list = "\n".join([f"• {a}" for a in res.get("actions", [])])
        text = (
            "✅ <b>СЕРВЕР УСПЕШНО ОЧИЩЕН & ОПТИМИЗИРОВАН</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🧹 <b>Удалено мусора:</b> <code>{files_cnt} файлов</code> (<b>{freed_str}</b>)\n"
            f"💾 <b>Свободно на диске теперь:</b> <b>{disk_free}</b> 🟢\n\n"
            f"<b>Выполненные операции:</b>\n{actions_list}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 <i>Память освобождена, база данных сжата. Риск зависания устранён!</i>"
        )
        await safe_edit(callback, text, reply_markup=server_cleaned_keyboard(), parse_mode="HTML")
    elif action == "main":
        is_admin = (callback.from_user.id == config.ADMIN_ID)
        kb = admin_menu_keyboard() if is_admin else client_menu_keyboard()
        title = "🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА (ADMIN):</b>" if is_admin else "🏛 <b>ГЛАВНОЕ МЕНЮ:</b>"
        await safe_edit(callback, title, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "terminal:refresh")
async def cb_terminal_refresh(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import terminal_dashboard_keyboard
    await callback.answer("🔄 Данные из MT5 обновлены!")
    text = bridge_manager.format_terminal_dashboard()
    await safe_edit(callback, text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "terminal:panic_confirm")
async def cb_panic_confirm(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from bot.keyboards import panic_confirm_keyboard
    text = (
        "🛑 <b>ЭКСТРЕННАЯ ПАНИКА: ПОДТВЕРЖДЕНИЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Вы уверены, что хотите немедленно закрыть ВСЕ открытые позиции в рынке "
        "и отменить ВСЕ отложенные лимитные ордера в терминале MetaTrader 5?\n\n"
        "<i>Это действие необратимо!</i>"
    )
    await safe_edit(callback, text, reply_markup=panic_confirm_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "terminal:panic_exec")
async def cb_panic_exec(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import terminal_dashboard_keyboard
    await callback.answer("🚨 Запрос отправлен в MT5!", show_alert=True)
    bridge_manager.request_panic_close()
    text = (
        "🚨 <b>КОМАНДА ЭКСТРЕННОГО ЗАКРЫТИЯ ОТПРАВЛЕНА В MT5!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Робот в терминале MetaTrader 5 получил команду на закрытие всех открытых позиций "
        "и снятие всех отложенных лимитных ордеров.\n\n"
        "⏳ <i>Исполнение произойдёт при следующем запросе терминала (1–3 секунды).</i>"
    )
    await safe_edit(callback, text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "server:refresh")
async def cb_server_refresh(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from utils.server_health import get_server_health_dashboard
    from bot.keyboards import server_status_keyboard
    await callback.answer("🔄 Данные сервера обновлены!")
    text = await get_server_health_dashboard()
    await safe_edit(callback, text, reply_markup=server_status_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "server:clear_cache")
async def cb_server_clear_cache(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from utils.server_health import execute_cache_cleanup
    from bot.keyboards import server_cleaned_keyboard
    await callback.answer("🧹 Очищаю кэш и временный мусор...", show_alert=False)
    res = await execute_cache_cleanup()
    freed_str = res.get("bytes_freed_str", "0 B")
    files_cnt = res.get("files_removed", 0)
    disk_free = res.get("disk_free_str", "—")
    actions_list = "\n".join([f"• {a}" for a in res.get("actions", [])])
    text = (
        "✅ <b>СЕРВЕР УСПЕШНО ОЧИЩЕН & ОПТИМИЗИРОВАН</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧹 <b>Удалено мусора:</b> <code>{files_cnt} файлов</code> (<b>{freed_str}</b>)\n"
        f"💾 <b>Свободно на диске теперь:</b> <b>{disk_free}</b> 🟢\n\n"
        f"<b>Выполненные операции:</b>\n{actions_list}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <i>Память освобождена, база данных сжата. Риск зависания устранён!</i>"
    )
    await safe_edit(callback, text, reply_markup=server_cleaned_keyboard(), parse_mode="HTML")


@router.message(Command("server"))
@router.message(Command("health"))
async def cmd_server_health(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.")
        return
    from utils.server_health import get_server_health_dashboard
    from bot.keyboards import server_status_keyboard
    text = await get_server_health_dashboard()
    await message.answer(text, reply_markup=server_status_keyboard(), parse_mode="HTML")


@router.message(Command("clean"))
@router.message(Command("clear_cache"))
async def cmd_clear_cache(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.")
        return
    from utils.server_health import execute_cache_cleanup
    from bot.keyboards import server_cleaned_keyboard
    wait_msg = await message.answer("🧹 Очищаю кэш и временный мусор...")
    res = await execute_cache_cleanup()
    freed_str = res.get("bytes_freed_str", "0 B")
    files_cnt = res.get("files_removed", 0)
    disk_free = res.get("disk_free_str", "—")
    actions_list = "\n".join([f"• {a}" for a in res.get("actions", [])])
    text = (
        "✅ <b>СЕРВЕР УСПЕШНО ОЧИЩЕН & ОПТИМИЗИРОВАН</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧹 <b>Удалено мусора:</b> <code>{files_cnt} файлов</code> (<b>{freed_str}</b>)\n"
        f"💾 <b>Свободно на диске теперь:</b> <b>{disk_free}</b> 🟢\n\n"
        f"<b>Выполненные операции:</b>\n{actions_list}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <i>Память освобождена, база данных сжата. Риск зависания устранён!</i>"
    )
    await wait_msg.edit_text(text, reply_markup=server_cleaned_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("autotrade:"))
async def cb_autotrade_actions(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import autotrade_keyboard, cancel_custom_lot_keyboard
    from db.database import get_bot_setting, set_bot_setting

    parts = callback.data.split(":")
    action = parts[1]
    if action == "on":
        bridge_manager.set_enabled(True)
        await set_bot_setting("autotrade_enabled", "true")
        await callback.answer("🟢 Автопилот включен!")
    elif action == "off":
        bridge_manager.set_enabled(False)
        await set_bot_setting("autotrade_enabled", "false")
        await callback.answer("🔴 Автопилот приостановлен!")
    elif action == "lot":
        val = float(parts[2])
        bridge_manager.set_lot(val)
        await set_bot_setting("trading_lot", str(val))
        await set_bot_setting("lot_mode", "fixed")
        await callback.answer(f"🔹 Лот установлен: {val:.2f}")
    elif action == "risk":
        val = float(parts[2])
        bridge_manager.set_risk(val)
        await set_bot_setting("trading_risk", str(val))
        await set_bot_setting("lot_mode", "risk")
        await callback.answer(f"⚖️ Риск установлен: {val:.1f}%")
    elif action == "custom_lot":
        state = get_user_state(callback.from_user.id)
        state["awaiting_custom_lot"] = True
        prompt_text = (
            "✍️ <b>Ввод собственного размера лота</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Текущий лот: <code>{bridge_manager.default_lot:.2f}</code>\n\n"
            "Напишите в ответ желаемое число в чат сообщением.\n"
            "<i>Примеры: <code>0.03</code>, <code>0.07</code>, <code>0.15</code>, <code>0.50</code></i>\n\n"
            "📌 <b>Ограничения:</b>\n"
            "• Минимум: <b>0.01</b>\n"
            "• Максимум: <b>10.00</b>\n"
            "• Шаг: <b>0.01</b>\n\n"
            "<i>Для отмены нажмите кнопку ниже 👇</i>"
        )
        await safe_edit(callback, prompt_text, reply_markup=cancel_custom_lot_keyboard(), parse_mode="HTML")
        return
    elif action == "cancel_custom_lot":
        state = get_user_state(callback.from_user.id)
        state["awaiting_custom_lot"] = False
        await callback.answer("Ввод лота отменён")
    elif action == "daily_limit":
        val = float(parts[2])
        bridge_manager.set_daily_loss_limit(val)
        await set_bot_setting("max_daily_loss_pct", str(val))
        await callback.answer(f"🛡️ Дневной лимит допустимого убытка: {val:.1f}%")
    elif action == "unlock_daily":
        bridge_manager.unlock_daily_loss()
        await callback.answer("🔓 Дневной замок просадки успешно сброшен!", show_alert=True)
    elif action == "mode":
        new_mode = parts[2]  # "micro" or "prop"
        await set_bot_setting("trading_mode", new_mode)
        mode_text = "🛡️ Режим «Микро-депозит» активирован!" if new_mode == "micro" else "👑 Режим: Институционал активирован!"
        await callback.answer(mode_text, show_alert=True)
    elif action == "mode_noop":
        cur_mode = parts[2]
        name = "🛡️ Режим «Микро-депозит»" if cur_mode == "micro" else "👑 Режим: Институционал"
        await callback.answer(f"✅ {name} уже активен! Для смены нажмите на соседнюю кнопку.", show_alert=False)
        return

    trading_mode = await get_bot_setting("trading_mode", "micro")
    status_emoji = "🟢 ВКЛЮЧЕН (АКТИВЕН)" if bridge_manager.enabled else "🔴 ПРИОСТАНОВЛЕН (ПАУЗА)"

    if trading_mode == "micro":
        profile_name = "🛡️ Режим «Микро-депозит»"
        profile_desc = (
            "• Золото (XAUUSD): ❌ <b>ОТКЛЮЧЕНО</b> (защита депозита)\n"
            "• Макс. сделок в рынке: <b>1</b> (свободная маржа)\n"
            "• Макс. стоп-лосс: <b>≤ 18 пипсов</b> (риск ~$1.80)\n"
            "• Режим сделок: <b>Pure Swing</b> (свободный ход до Take Profit)"
        )
        pool_str = "16 валютных пар (без Золота)"
    else:
        profile_name = "👑 Режим: Институционал"
        profile_desc = (
            "• Золото (XAUUSD): ✅ <b>ВКЛЮЧЕНО</b>\n"
            "• Макс. сделок в рынке: <b>до 7</b>\n"
            "• Макс. стоп-лосс: по структуре ICT/SMC\n"
            "• Режим сделок: <b>Pure Swing</b> (удержание до полного Take Profit)"
        )
        pool_str = "17 пар (Форекс + Золото)"

    lot_display = f"{bridge_manager.default_lot:.2f}"
    if getattr(bridge_manager, 'lot_mode', 'fixed') == "fixed":
        lot_status_badge = f"<b>{lot_display}</b> (✅ Активен: Фиксированный)"
        risk_status_badge = f"{bridge_manager.default_risk:.1f}%"
    else:
        lot_status_badge = f"Динамический (по риску)"
        risk_status_badge = f"<b>{bridge_manager.default_risk:.1f}%</b> (✅ Активен: Динамический)"

    text = (
        f"⚙️ <b>НАСТРОЙКИ АВТОПИЛОТА (MT5 BRIDGE)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📡 <b>Статус авто-торговли:</b> {status_emoji}\n"
        f"🎯 <b>Активный профиль:</b> <b>{profile_name}</b>\n"
        f"{profile_desc}\n\n"
        f"📊 <b>Рабочий лот:</b> {lot_status_badge}\n"
        f"⚖️ <b>Риск на сделку:</b> {risk_status_badge}\n"
        f"💱 <b>Инструментов в пуле:</b> <code>{pool_str}</code>\n"
        f"🛑 <b>Дневной лимит просадки:</b> <code>{bridge_manager.max_daily_loss_pct:.1f}%</code> "
        f"{'(🚨 ЗАБЛОКИРОВАН)' if bridge_manager.daily_loss_locked else '(🟢 Норма)'}\n\n"
        f"Используйте кнопки ниже для быстрого управления 👇\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = autotrade_keyboard(
        enabled=bridge_manager.enabled,
        mode=trading_mode,
        current_lot=bridge_manager.default_lot,
        current_risk=bridge_manager.default_risk,
        lot_mode=getattr(bridge_manager, 'lot_mode', 'fixed'),
        daily_limit=getattr(bridge_manager, 'max_daily_loss_pct', 3.0),
        daily_locked=getattr(bridge_manager, 'daily_loss_locked', False)
    )
    await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_user_text_input(message: Message):
    """Обработчик текстового ввода администратора (в частности, ручной ввод лота)."""
    if message.from_user.id != config.ADMIN_ID:
        return

    state = get_user_state(message.from_user.id)

    # 1. CRM Search
    if state.get("awaiting_crm_search"):
        state["awaiting_crm_search"] = False
        query = message.text.strip()
        from db.users import search_users, get_user_ltv
        from bot.keyboards import admin_user_card_keyboard, cancel_crm_search_keyboard
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton

        results = await search_users(query)
        if not results:
            prompt = (
                f"🔍 <b>ПОИСК КЛИЕНТА В CRM</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"По запросу <code>{html.escape(query)}</code> никого не найдено.\n\n"
                f"Попробуйте ввести другой username или Telegram ID 👇"
            )
            state["awaiting_crm_search"] = True
            await message.answer(prompt, reply_markup=cancel_crm_search_keyboard(), parse_mode="HTML")
            return

        if len(results) == 1:
            u = results[0]
            target_id = u["telegram_id"]
            is_active = (u.get("status") == "approved")
            is_life = bool(u.get("is_lifetime", 0))
            ltv_usd, ltv_cnt = await get_user_ltv(target_id)
            card_text = format_crm_user_card(u, ltv_usd=ltv_usd, payments_cnt=ltv_cnt)
            await message.answer(f"✅ <b>Клиент найден!</b>\n\n{card_text}",
                                 reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life),
                                 parse_mode="HTML")
            return

        builder = InlineKeyboardBuilder()
        for u in results[:10]:
            uid = u["telegram_id"]
            un = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
            fn = u.get("first_name") or "Клиент"
            builder.row(InlineKeyboardButton(text=f"👤 {fn} ({un})", callback_data=f"crm:user:{uid}"))
        builder.row(InlineKeyboardButton(text="◀️ В Главное Меню CRM", callback_data="menu:crm"))

        await message.answer(
            f"🔍 <b>РЕЗУЛЬТАТЫ ПОИСКА ({len(results)}):</b>\n"
            f"Выберите клиента из списка ниже 👇",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    # 2. CRM Admin Notes
    if state.get("awaiting_crm_notes"):
        target_id = state.pop("awaiting_crm_notes")
        notes_text = html.escape(message.text.strip())
        from db.users import update_admin_notes, get_user, get_user_ltv
        from bot.keyboards import admin_user_card_keyboard
        await update_admin_notes(target_id, notes_text)
        u = await get_user(target_id)
        if u:
            is_active = (u.get("status") == "approved")
            is_life = bool(u.get("is_lifetime", 0))
            ltv_usd, ltv_cnt = await get_user_ltv(target_id)
            card_text = format_crm_user_card(u, ltv_usd=ltv_usd, payments_cnt=ltv_cnt)
            await message.answer(f"✅ <b>Заметка успешно сохранена!</b>\n\n{card_text}",
                                 reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life),
                                 parse_mode="HTML")
        return

    # 3. CRM Direct Message
    if state.get("awaiting_crm_dm"):
        target_id = state.pop("awaiting_crm_dm")
        dm_text = html.escape(message.text.strip())
        from db.users import get_user, get_user_ltv
        from bot.keyboards import admin_user_card_keyboard
        u = await get_user(target_id)
        un = f"@{u['username']}" if u and u.get("username") else f"ID {target_id}"

        client_msg = (
            "📩 <b>СООБЩЕНИЕ ОТ АДМИНИСТРАЦИИ SMART TRADER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{html.escape(dm_text)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Вы можете ответить на это сообщение прямо в чате бота.</i>"
        )
        try:
            await message.bot.send_message(target_id, client_msg, parse_mode="HTML")
            sent_ok = True
        except Exception as e:
            sent_ok = False
            err_msg = str(e)

        if u:
            is_active = (u.get("status") == "approved")
            is_life = bool(u.get("is_lifetime", 0))
            ltv_usd, ltv_cnt = await get_user_ltv(target_id)
            status_report = f"✅ <b>Сообщение успешно доставлено клиенту {un}!</b>" if sent_ok else f"❌ <b>Ошибка отправки ({err_msg})</b>"
            card_text = format_crm_user_card(u, ltv_usd=ltv_usd, payments_cnt=ltv_cnt)
            await message.answer(f"{status_report}\n\n{card_text}",
                                 reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life),
                                 parse_mode="HTML")
        return

    # 4. CRM Custom Days
    if state.get("awaiting_crm_custom_days"):
        target_id = state.pop("awaiting_crm_custom_days")
        try:
            days_val = int(message.text.strip())
            if days_val <= 0 or days_val > 3650:
                raise ValueError
        except ValueError:
            from bot.keyboards import cancel_crm_action_keyboard
            state["awaiting_crm_custom_days"] = target_id
            await message.answer("⚠️ <b>Некорректное число дней!</b>\nВведите целое число от 1 до 3650:",
                                 reply_markup=cancel_crm_action_keyboard(target_id), parse_mode="HTML")
            return

        from db.users import extend_subscription, get_user, get_user_ltv
        from bot.keyboards import admin_user_card_keyboard
        ok, new_exp = await extend_subscription(target_id, days=days_val)
        exp_date_str = new_exp[:10] if new_exp else "успешно"
        try:
            await message.bot.send_message(
                target_id,
                f"💎 <b>Администратор продлил ваш доступ на {days_val} дней!</b>\n\n"
                f"Подписка активна до: <code>{exp_date_str}</code> 🚀\n"
                f"Отправьте /start чтобы открыть терминал.",
                parse_mode="HTML"
            )
        except Exception:
            pass

        u = await get_user(target_id)
        if u:
            is_active = (u.get("status") == "approved")
            is_life = bool(u.get("is_lifetime", 0))
            ltv_usd, ltv_cnt = await get_user_ltv(target_id)
            card_text = format_crm_user_card(u, ltv_usd=ltv_usd, payments_cnt=ltv_cnt)
            await message.answer(f"➕ <b>Доступ успешно продлён на {days_val} дн. (до {exp_date_str})!</b>\n\n{card_text}",
                                 reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life),
                                 parse_mode="HTML")
        return

    if state.get("awaiting_custom_lot"):
        raw_text = message.text.strip().replace(',', '.')
        try:
            val = float(raw_text)
        except ValueError:
            await message.answer(
                "❌ <b>Некорректный формат!</b> Вы ввели не число.\n\n"
                f"Получено: <code>{html.escape(message.text)}</code>\n"
                "Пожалуйста, отправьте корректное число (например: <code>0.03</code>, <code>0.07</code> или <code>0.15</code>).\n\n"
                "<i>Допустимый диапазон: от 0.01 до 10.00</i>",
                reply_markup=cancel_custom_lot_keyboard(),
                parse_mode="HTML"
            )
            return

        if val < 0.01:
            await message.answer(
                "⚠️ <b>Слишком маленький лот!</b>\n\n"
                f"Вы указали: <code>{val}</code>\n"
                "Минимально допустимый торговый лот у брокера — <b>0.01</b>.\n\n"
                "Пожалуйста, введите значение <b>0.01</b> или выше:",
                reply_markup=cancel_custom_lot_keyboard(),
                parse_mode="HTML"
            )
            return

        if val > 10.0:
            await message.answer(
                "⚠️ <b>Слишком большой лот!</b>\n\n"
                f"Вы указали: <code>{val}</code>\n"
                "Максимальный безопасный лот в системе ограничен <b>10.00</b> (защита депозита от моментального слива).\n\n"
                "Пожалуйста, укажите разумный лот от <b>0.01 до 10.00</b>:",
                reply_markup=cancel_custom_lot_keyboard(),
                parse_mode="HTML"
            )
            return

        val = round(val, 2)
        state["awaiting_custom_lot"] = False

        from trading.execution_bridge import bridge_manager
        from db.database import set_bot_setting, get_bot_setting
        from bot.keyboards import autotrade_keyboard

        bridge_manager.set_lot(val)
        await set_bot_setting("trading_lot", str(val))
        await set_bot_setting("lot_mode", "fixed")

        trading_mode = await get_bot_setting("trading_mode", "micro")
        status_emoji = "🟢 ВКЛЮЧЕН (АКТИВЕН)" if bridge_manager.enabled else "🔴 ПРИОСТАНОВЛЕН (ПАУЗА)"

        if trading_mode == "micro":
            profile_name = "🛡️ Режим «Микро-депозит»"
            profile_desc = (
                "• Золото (XAUUSD): ❌ <b>ОТКЛЮЧЕНО</b> (защита депозита)\n"
                "• Макс. сделок в рынке: <b>1</b> (свободная маржа)\n"
                "• Макс. стоп-лосс: <b>≤ 18 пипсов</b> (риск ~$1.80)\n"
                "• Режим сделок: <b>Pure Swing</b> (свободный ход до Take Profit)"
            )
            pool_str = "16 валютных пар (без Золота)"
        else:
            profile_name = "👑 Режим: Институционал"
            profile_desc = (
                "• Золото (XAUUSD): ✅ <b>ВКЛЮЧЕНО</b>\n"
                "• Макс. сделок в рынке: <b>до 7</b>\n"
                "• Макс. стоп-лосс: по структуре ICT/SMC\n"
                "• Режим сделок: <b>Pure Swing</b> (удержание до полного Take Profit)"
            )
            pool_str = "17 пар (Форекс + Золото)"

        text = (
            f"✅ <b>Рабочий лот успешно установлен: {val:.2f}</b>\n\n"
            f"⚙️ <b>НАСТРОЙКИ АВТОПИЛОТА (MT5 BRIDGE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 <b>Статус авто-торговли:</b> {status_emoji}\n"
            f"🎯 <b>Активный профиль:</b> <b>{profile_name}</b>\n"
            f"{profile_desc}\n\n"
            f"📊 <b>Рабочий лот:</b> <b>{val:.2f}</b> (✅ Активен: Фиксированный)\n"
            f"⚖️ <b>Риск на сделку:</b> <code>{bridge_manager.default_risk:.1f}%</code>\n"
            f"💱 <b>Инструментов в пуле:</b> <code>{pool_str}</code>\n"
        f"🛑 <b>Дневной лимит просадки:</b> <code>{bridge_manager.max_daily_loss_pct:.1f}%</code> "
        f"{'(🚨 ЗАБЛОКИРОВАН)' if bridge_manager.daily_loss_locked else '(🟢 Норма)'}\n\n"
            f"Используйте кнопки ниже для быстрого управления 👇\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = autotrade_keyboard(
            enabled=bridge_manager.enabled,
            mode=trading_mode,
            current_lot=bridge_manager.default_lot,
            current_risk=bridge_manager.default_risk,
            lot_mode=getattr(bridge_manager, 'lot_mode', 'fixed'),
            daily_limit=getattr(bridge_manager, 'max_daily_loss_pct', 3.0),
            daily_locked=getattr(bridge_manager, 'daily_loss_locked', False)
        )
        await message.answer(text, reply_markup=kb, parse_mode="HTML")




@router.callback_query(F.data.startswith("sym:"))
async def cb_symbol(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    state["symbol"] = symbol
    await safe_edit(callback, f"🏛 <i>Глубокий анализ {symbol} по модели ICT/SMC...</i>", parse_mode="HTML")
    res = await run_multi_tf_analysis(symbol)
    if res:
        text = format_multi_tf_analysis(res)
        can_exec = bool(res.overall_direction != "NEUTRAL" and res.entry and res.stop_loss and res.overall_stars >= 3)
        kb = analysis_result_keyboard(symbol, can_execute=can_exec)
        chunks = split_message_text(text, 4000)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await safe_edit(callback, chunk, reply_markup=kb, parse_mode="HTML")
            else:
                try:
                    await callback.message.answer(chunk, parse_mode="HTML")
                except Exception:
                    await callback.message.answer(chunk, parse_mode=None)
    else:
        await safe_edit(callback, "⚠️ Ошибка получения котировок.", reply_markup=back_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("exec_mt5:"))
async def cb_exec_mt5(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор может отправлять ордера в MT5!", show_alert=True)
        return
    symbol = callback.data.split(":")[1]
    await callback.answer("⏳ Анализирую и отправляю в MT5...", show_alert=False)
    
    try:
        from db.database import save_signal, check_signal_exists
        res = await run_multi_tf_analysis(symbol)
        
        if not res or res.overall_direction == "NEUTRAL" or not res.entry:
            await callback.message.answer(f"⚠️ По {symbol} сейчас нет четкого направленного сетапа (NEUTRAL). Ордер не создан.", parse_mode="HTML")
            return
            
        strategies_str = ", ".join([f"{e} {n}: {v}" for e, n, v in res.strategy_verdicts])
        timeframes_str = ", ".join([f"{t.timeframe}: {t.direction}" for t in res.tf_analyses])

        await save_signal(
            symbol=symbol,
            direction=res.overall_direction,
            order_type=res.order_type,
            tag_emoji=res.tag_emoji,
            stars=max(4, res.overall_stars),
            current_price=res.current_price,
            entry_price=res.entry,
            stop_loss=res.stop_loss,
            take_profit_1=res.take_profit_1,
            take_profit_2=res.take_profit_2,
            risk_reward=res.risk_reward_1,
            strategies_agreed=strategies_str,
            timeframes_agreed=timeframes_str,
        )
        
        confirm_text = (
            f"🚀 <b>СИГНАЛ ПЕРЕДАН В METATRADER 5!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>СИМВОЛ:</b> <code>{symbol}</code>\n"
            f"<b>ТИП:</b> <code>{res.order_type}</code> [{res.overall_direction}]\n"
            f"📍 <b>ENTRY:</b> <code>{res.entry}</code>\n"
            f"🛑 <b>STOP LOSS:</b> <code>{res.stop_loss}</code>\n"
            f"🎯 <b>TAKE PROFIT 1:</b> <code>{res.take_profit_1}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Советник MT5 примет и выставит ордер при очередном 3-секундном цикле.</i>"
        )
        await callback.message.answer(confirm_text, parse_mode="HTML", reply_markup=back_keyboard())
    except Exception as e:
        logger.error("cb_exec_mt5 error: %s", e, exc_info=True)
        await callback.message.answer(f"❌ Ошибка отправки в MT5: {e}", parse_mode="HTML")




# ═══════════════════════════════════════════════════════════
# СИСТЕМА ДОСТУПА
# ═══════════════════════════════════════════════════════════

@router.message(Command("request"))
async def cmd_request(message: Message):
    """Подача заявки на доступ к боту и выбор тарифа."""
    user_id = message.from_user.id
    if user_id == config.ADMIN_ID:
        await message.answer("👑 Вы администратор бота. Полный доступ уже активен!", parse_mode=None)
        return

    from bot.keyboards import request_options_keyboard
    from db.users import get_user_status
    status = await get_user_status(user_id)

    if status == "approved":
        await message.answer("✅ У вас уже есть активный доступ к терминалу! Отправьте /start", parse_mode=None)
        return

    text = (
        "💎 <b>ОФОРМЛЕНИЕ ДОСТУПА В SMART TRADER BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите желаемый вариант доступа:\n\n"
        "• 🎁 <b>Бесплатный Тест-драйв:</b> 3 дня без оплаты\n"
        "• 💎 <b>Тариф 1 Месяц:</b> $50\n"
        "• 🚀 <b>Тариф 3 Месяца:</b> $140 <i>(выгода $10)</i>\n"
        "• 👑 <b>Тариф 1 Год:</b> $500 <i>(выгода $100)</i>\n\n"
        "Нажмите на кнопку ниже, чтобы моментально передать заявку администратору 👇"
    )
    await message.answer(text, reply_markup=request_options_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("req:"))
async def cb_request_actions(callback: CallbackQuery):
    """Обработка выбора тарифа или подачи заявки клиентом."""
    parts = callback.data.split(":")
    action = parts[1]

    user_id = callback.from_user.id
    username = callback.from_user.username or ""
    first_name = callback.from_user.first_name or "Клиент"

    if user_id == config.ADMIN_ID:
        await callback.answer("👑 Вы администратор.", show_alert=True)
        return

    from db.users import get_user_status, request_access
    status = await get_user_status(user_id)
    if status == "approved":
        await callback.answer("✅ У вас уже активен доступ к сигналам!", show_alert=True)
        return

    if action in ("start", "plans"):
        from bot.keyboards import request_options_keyboard
        text = (
            "💎 <b>ВЫБОР ТАРИФА SMART TRADER BOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "• 🎁 <b>Бесплатный Тест-драйв:</b> 3 дня без оплаты\n"
            "• 💎 <b>Тариф 1 Месяц:</b> $50\n"
            "• 🚀 <b>Тариф 3 Месяца:</b> $140 <i>(выгода $10)</i>\n"
            "• 👑 <b>Тариф 1 Год:</b> $500 <i>(выгода $100)</i>\n\n"
            "Выберите желаемый вариант ниже 👇"
        )
        await safe_edit(callback, text, reply_markup=request_options_keyboard(), parse_mode="HTML")
        return

    elif action == "type":
        plan_code = parts[2] if len(parts) > 2 else "trial"
        if plan_code == "trial":
            from db.users import has_used_trial, activate_trial
            from bot.keyboards import request_options_keyboard, client_menu_keyboard
            already_used = await has_used_trial(user_id)
            if already_used:
                await safe_edit(
                    callback,
                    "❌ <b>Вы уже использовали бесплатный пробный период!</b>\n\n"
                    "Бесплатный 3-дневный тест-драйв предоставляется только один раз на аккаунт.\n\n"
                    "Для продолжения получения институциональных сигналов выберите подходящий тариф ниже 👇",
                    reply_markup=request_options_keyboard(),
                    parse_mode="HTML"
                )
                return

            ok = await activate_trial(user_id, username=username, first_name=first_name, days=3)
            if ok:
                success_text = (
                    "🎉 <b>БЕСПЛАТНЫЙ ТЕСТ-ДРАЙВ АКТИВИРОВАН!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Вам открыт полный доступ ко всем сигналам на <b>3 дня (72 часа)</b> без ограничений! 🚀\n\n"
                    "• Все 17 инструментов (Forex мажоры, кроссы и Золото XAUUSD)\n"
                    "• Алгоритмы Smart Money / ICT (BOS, OB, FVG, OTE)\n"
                    "• Точки входа, Take Profit и Stop Loss с R:R от 1:2.5\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Нажмите кнопку ниже или отправьте /start, чтобы открыть меню! 📊"
                )
                await safe_edit(callback, success_text, reply_markup=client_menu_keyboard(), parse_mode="HTML")

                if config.ADMIN_ID:
                    try:
                        safe_fn = html.escape(str(first_name or ""))
                        un_text = f"@{html.escape(username)}" if username else f"ID: <code>{user_id}</code>"
                        await callback.bot.send_message(
                            config.ADMIN_ID,
                            f"🎁 <b>НОВЫЙ ТЕСТ-ДРАЙВ АКТИВИРОВАН:</b>\n"
                            f"👤 Имя: <b>{safe_fn}</b> ({un_text})\n"
                            f"🆔 Telegram ID: <code>{user_id}</code>\n"
                            f"⏳ Срок: 3 дня (72 часа) | Статус: Активен",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            else:
                await callback.answer("⚠️ Ошибка активации пробного периода. Попробуйте позже.", show_alert=True)
            return

        # Платные тарифы (1m, 3m, 1y) — выставление инвойса с реквизитами
        plan_info = {
            "1m": {"label": "💎 Тариф 1 Месяц", "price": "$50", "days": 30, "usd": 50.0},
            "3m": {"label": "🚀 Тариф 3 Месяца", "price": "$140 (скидка $10)", "days": 90, "usd": 140.0},
            "1y": {"label": "👑 Тариф 1 Год", "price": "$500 (скидка $100)", "days": 365, "usd": 500.0},
        }
        info = plan_info.get(plan_code, plan_info["1m"])
        from bot.keyboards import payment_invoice_keyboard
        invoice_text = (
            f"💎 <b>ОФОРМЛЕНИЕ ПОДПИСКИ SMART TRADER BOT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Выбранный план:</b> <b>{info['label']}</b>\n"
            f"💵 <b>Сумма к оплате:</b> <b>{info['price']}</b>\n"
            f"⏳ <b>Срок действия:</b> <b>{info['days']} дней</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💳 <b>РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:</b>\n\n"
            f"💵 <b>USDT (TRC-20):</b>\n"
            f"<code>TNPn5XgKSm37482nE4Qj1N3i185x95gM7Z</code>\n"
            f"<i>(Сеть TRON TRC-20 — копируется кликом)</i>\n\n"
            f"💎 <b>TON / Telegram Wallet:</b>\n"
            f"<code>UQDF28yH6mG9V4f31l9vC7qZ6y_sLw...</code> <i>(или по запросу)</i>\n\n"
            f"💳 <b>Банковская карта (РФ / СНГ / UZS):</b>\n"
            f"<i>Номер карты для перевода уточняйте у администратора</i>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>ИНСТРУКЦИЯ ПОСЛЕ ОПЛАТЫ:</b>\n"
            f"1. Нажмите кнопку <b>«📸 Отправить чек об оплате»</b> ниже.\n"
            f"2. Прикрепите скриншот или фото квитанции в этот чат.\n"
            f"3. Бот мгновенно передаст чек администратору для активации!"
        )
        await safe_edit(callback, invoice_text, reply_markup=payment_invoice_keyboard(plan_code), parse_mode="HTML")
        return

    elif action == "receipt":
        plan_code = parts[2] if len(parts) > 2 else "1m"
        state = get_user_state(user_id)
        state["awaiting_receipt_plan"] = plan_code
        from bot.keyboards import cancel_receipt_keyboard
        prompt_text = (
            "📸 <b>ОТПРАВЬТЕ ЧЕК ОБ ОПЛАТЕ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Отправьте фото, скриншот или файл квитанции об оплате в этот чат.\n\n"
            "Бот моментально передаст его администратору для включения доступа! 🚀"
        )
        await safe_edit(callback, prompt_text, reply_markup=cancel_receipt_keyboard(), parse_mode="HTML")
        return


@router.message(F.photo)
async def handle_payment_receipt_photo(message: Message):
    """Приём чека об оплате в виде фото и прямая пересылка администратору (0 байт на диске VPS)."""
    state = get_user_state(message.from_user.id)
    plan_code = state.get("awaiting_receipt_plan")
    if not plan_code:
        return

    state.pop("awaiting_receipt_plan", None)
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "Клиент"

    plan_info = {
        "1m": {"label": "💎 Тариф 1 Месяц ($50)", "usd": 50.0, "days": 30},
        "3m": {"label": "🚀 Тариф 3 Месяца ($140)", "usd": 140.0, "days": 90},
        "1y": {"label": "👑 Тариф 1 Год ($500)", "usd": 500.0, "days": 365},
    }
    info = plan_info.get(plan_code, plan_info["1m"])

    if config.ADMIN_ID:
        try:
            from bot.keyboards import admin_cheque_keyboard
            un_text = f"@{html.escape(username)}" if username else f"ID: <code>{user_id}</code>"
            safe_fn = html.escape(str(first_name or ""))
            admin_caption = (
                f"📸 <b>НОВЫЙ ЧЕК НА ОПЛАТУ!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Клиент: <b>{safe_fn}</b> ({un_text})\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"📦 Выбран тариф: <b>{info['label']}</b>\n"
                f"💵 Сумма: <b>${info['usd']:.2f}</b> ({info['days']} дн.)\n\n"
                f"Подтвердить оплату и активировать подписку клиенту? 👇"
            )
            await message.bot.send_photo(
                chat_id=config.ADMIN_ID,
                photo=message.photo[-1].file_id,
                caption=admin_caption,
                reply_markup=admin_cheque_keyboard(user_id, plan_code),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error("Failed to forward receipt photo to admin: %s", e)

    await message.answer(
        "✅ <b>Чек успешно получен и передан администратору!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Администратор проверяет платёж. Как только оплата будет подтверждена, ваш доступ активируется моментально! 🔔",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.document)
async def handle_payment_receipt_document(message: Message):
    """Приём чека об оплате в виде документа/файла (0 байт на диске VPS)."""
    state = get_user_state(message.from_user.id)
    plan_code = state.get("awaiting_receipt_plan")
    if not plan_code:
        return

    state.pop("awaiting_receipt_plan", None)
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "Клиент"

    plan_info = {
        "1m": {"label": "💎 Тариф 1 Месяц ($50)", "usd": 50.0, "days": 30},
        "3m": {"label": "🚀 Тариф 3 Месяца ($140)", "usd": 140.0, "days": 90},
        "1y": {"label": "👑 Тариф 1 Год ($500)", "usd": 500.0, "days": 365},
    }
    info = plan_info.get(plan_code, plan_info["1m"])

    if config.ADMIN_ID:
        try:
            from bot.keyboards import admin_cheque_keyboard
            un_text = f"@{html.escape(username)}" if username else f"ID: <code>{user_id}</code>"
            safe_fn = html.escape(str(first_name or ""))
            admin_caption = (
                f"📄 <b>НОВЫЙ ЧЕК/ДОКУМЕНТ НА ОПЛАТУ!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Клиент: <b>{safe_fn}</b> ({un_text})\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"📦 Выбран тариф: <b>{info['label']}</b>\n"
                f"💵 Сумма: <b>${info['usd']:.2f}</b> ({info['days']} дн.)\n\n"
                f"Подтвердить оплату и активировать подписку клиенту? 👇"
            )
            await message.bot.send_document(
                chat_id=config.ADMIN_ID,
                document=message.document.file_id,
                caption=admin_caption,
                reply_markup=admin_cheque_keyboard(user_id, plan_code),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error("Failed to forward receipt doc to admin: %s", e)

    await message.answer(
        "✅ <b>Файл чека получен и передан администратору!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Администратор проверяет платёж. Как только оплата будет подтверждена, ваш доступ активируется моментально! 🔔",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("admin_approve_pay:"))
async def cb_admin_approve_pay(callback: CallbackQuery):
    """Админ подтверждает оплату по чеку и активирует подписку."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    parts = callback.data.split(":")
    target_id = int(parts[1])
    plan_code = parts[2] if len(parts) > 2 else "1m"

    plan_info = {
        "1m": {"label": "PRO (1 мес)", "usd": 50.0, "days": 30},
        "3m": {"label": "PRO (3 мес)", "usd": 140.0, "days": 90},
        "1y": {"label": "PRO (1 год)", "usd": 500.0, "days": 365},
    }
    info = plan_info.get(plan_code, plan_info["1m"])

    from db.users import approve_user, record_payment
    await approve_user(target_id, days=info["days"], tariff=info["label"])
    await record_payment(
        telegram_id=target_id,
        amount_usd=info["usd"],
        days_added=info["days"],
        tariff=info["label"],
        payment_method="Чек / USDT",
        comment="Оплата подтверждена по чеку",
        created_by=config.ADMIN_ID
    )

    confirm_suffix = f"\n\n✅ ОПЛАТА ПОДТВЕРЖДЕНА (+${info['usd']:.2f} в кассу CRM, {info['days']} дней)"
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + confirm_suffix, parse_mode=None)
        elif callback.message.text:
            await callback.message.edit_text(text=callback.message.text + confirm_suffix, parse_mode=None)
    except Exception:
        pass

    try:
        await callback.bot.send_message(
            target_id,
            f"🎉 <b>ВАША ОПЛАТА УСПЕШНО ПОДТВЕРЖДЕНА!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Вам активирован тариф <b>{info['label']}</b> на <b>{info['days']} дней</b>! 🚀\n\n"
            f"Все институциональные сигналы Smart Money / ICT открыты.\n"
            f"Отправьте /start чтобы открыть терминал и начать работу!",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_approve_trial:"))
async def cb_admin_approve_trial(callback: CallbackQuery):
    """Админ одобряет заявку на бесплатный 3-дневный тест-драйв."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import activate_trial
    await activate_trial(target_id, days=3)

    await callback.message.edit_text(
        callback.message.text + "\n\n🎁 ОДОБРЕНО (Активирован бесплатный Тест-драйв на 3 дня)",
        parse_mode=None
    )

    try:
        await callback.bot.send_message(
            target_id,
            "🎁 <b>Вам активирован бесплатный Тест-драйв на 3 дня!</b>\n\n"
            "Вам открыт полный доступ ко всем институциональным сигналам Smart Money / ICT. 🚀\n\n"
            "Отправьте /start чтобы открыть торговый терминал и начать работу!",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_approve_30:"))
@router.callback_query(F.data.startswith("admin_approve:"))
async def cb_admin_approve_30(callback: CallbackQuery):
    """Админ одобряет тариф на 1 месяц (30 дней)."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import approve_user, record_payment
    await approve_user(target_id, days=30, tariff="PRO (1 мес)")
    await record_payment(
        telegram_id=target_id,
        amount_usd=50.0,
        days_added=30,
        tariff="PRO (1 мес)",
        payment_method="Админ CRM",
        comment="Одобрено в CRM",
        created_by=config.ADMIN_ID
    )

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ ОДОБРЕНО (Тариф PRO активирован на 30 дней, $50)",
        parse_mode=None
    )

    try:
        await callback.bot.send_message(
            target_id,
            "✅ <b>Ваша подписка успешно активирована!</b>\n\n"
            "Вам подключен тариф <b>PRO на 1 месяц (30 дней)</b>.\n"
            "Добро пожаловать в Smart Trader Bot! 🤖\n"
            "Отправьте /start чтобы открыть терминал.",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_approve_90:"))
async def cb_admin_approve_90(callback: CallbackQuery):
    """Админ одобряет тариф на 3 месяца (90 дней)."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import approve_user, record_payment
    await approve_user(target_id, days=90, tariff="PRO (3 мес)")
    await record_payment(
        telegram_id=target_id,
        amount_usd=140.0,
        days_added=90,
        tariff="PRO (3 мес)",
        payment_method="Админ CRM",
        comment="Одобрено в CRM",
        created_by=config.ADMIN_ID
    )

    await callback.message.edit_text(
        callback.message.text + "\n\n🚀 ОДОБРЕНО (Тариф PRO активирован на 90 дней, $140)",
        parse_mode=None
    )

    try:
        await callback.bot.send_message(
            target_id,
            "🚀 <b>Ваша подписка успешно активирована!</b>\n\n"
            "Вам подключен тариф <b>PRO на 3 месяца (90 дней)</b>!\n"
            "Отправьте /start чтобы открыть терминал.",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_approve_365:"))
async def cb_admin_approve_365(callback: CallbackQuery):
    """Админ одобряет годовой тариф (365 дней)."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import approve_user, record_payment
    await approve_user(target_id, days=365, tariff="PRO (1 год)")
    await record_payment(
        telegram_id=target_id,
        amount_usd=500.0,
        days_added=365,
        tariff="PRO (1 год)",
        payment_method="Админ CRM",
        comment="Одобрено в CRM",
        created_by=config.ADMIN_ID
    )

    await callback.message.edit_text(
        callback.message.text + "\n\n👑 ОДОБРЕНО (Тариф PRO активирован на 1 год, $500)",
        parse_mode=None
    )

    try:
        await callback.bot.send_message(
            target_id,
            "👑 <b>Ваша подписка успешно активирована!</b>\n\n"
            "Вам подключен тариф <b>PRO на 1 год (365 дней)</b>!\n"
            "Максимальный приоритет и полная аналитика.\n"
            "Отправьте /start чтобы открыть терминал.",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_reject:"))
async def cb_admin_reject(callback: CallbackQuery):
    """Админ отклоняет заявку."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import reject_user
    await reject_user(target_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n❌ ОТКЛОНЕНО",
        parse_mode=None
    )

    try:
        await callback.bot.send_message(
            target_id,
            "❌ К сожалению, ваша заявка была отклонена администратором.\n"
            "Свяжитесь с поддержкой для уточнения.",
            parse_mode=None
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# CRM & УПРАВЛЕНИЕ КЛИЕНТАМИ (ТОЛЬКО АДМИНИСТРАТОР)
# ═══════════════════════════════════════════════════════════

@router.message(Command("users"))
@router.message(Command("crm"))
async def cmd_users(message: Message):
    """Панель CRM для администратора с вкладками и пагинацией."""
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Только для администратора.", parse_mode=None)
        return

    text, kb = await get_crm_view(tab="all", page=1)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("crm:"))
async def cb_crm_actions(callback: CallbackQuery):
    """Интерактивное управление клиентами в CRM: вкладки, пагинация, продление, бан."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Доступно только администратору!", show_alert=True)
        return

    parts = callback.data.split(":")
    action = parts[1]

    if action == "tab":
        new_tab = parts[2] if len(parts) > 2 else "all"
        text, kb = await get_crm_view(tab=new_tab, page=1)
        await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")

    elif action == "page":
        cur_tab = parts[2] if len(parts) > 2 else "all"
        page_num = int(parts[3]) if len(parts) > 3 else 1
        text, kb = await get_crm_view(tab=cur_tab, page=page_num)
        await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")

    elif action == "page_noop":
        await callback.answer()
        return

    elif action == "search_prompt":
        state = get_user_state(callback.from_user.id)
        state["awaiting_crm_search"] = True
        from bot.keyboards import cancel_crm_search_keyboard
        prompt = (
            "🔍 <b>ПОИСК КЛИЕНТА В CRM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Введите <b>@username</b>, <b>Имя</b> или <b>Telegram ID</b> клиента сообщением в чат:\n\n"
            "<i>Примеры: <code>@m_bakhtiyor</code>, <code>2122425599</code>, <code>Bakhtiyor</code></i>\n\n"
            "<i>Для отмены нажмите кнопку ниже 👇</i>"
        )
        await safe_edit(callback, prompt, reply_markup=cancel_crm_search_keyboard(), parse_mode="HTML")
        return

    elif action == "finance_stats":
        from db.users import get_crm_finance_summary
        from bot.keyboards import crm_finance_keyboard
        fin = await get_crm_finance_summary()
        text = (
            "💵 <b>ФИНАНСОВАЯ КАССА И ВЫРУЧКА CRM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Общая выручка за всё время:</b> <b>${fin['total_usd']:.2f}</b>\n"
            f"📅 <b>Выручка за текущий месяц ({fin['month_name']}):</b> <b>${fin['month_usd']:.2f}</b>\n\n"
            "📈 <b>Метрики продаж:</b>\n"
            f"• Всего успешных оплат: <b>{fin['total_tx']}</b>\n"
            f"• Уникальных платящих клиентов: <b>{fin['unique_clients']}</b>\n"
            f"• Средний чек (ARPU): <b>${fin['avg_check']:.2f}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Вы можете скачать полный список клиентов с историей оплат в формате CSV 👇</i>"
        )
        await safe_edit(callback, text, reply_markup=crm_finance_keyboard(), parse_mode="HTML")
        return

    elif action == "export_csv":
        from db.users import export_users_to_csv
        from aiogram.types import BufferedInputFile
        csv_text = await export_users_to_csv()
        if not csv_text:
            await callback.answer("⚠️ База клиентов пуста или произошла ошибка.", show_alert=True)
            return
        await callback.answer("📥 Генерирую CSV файл...")
        today_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_bytes = csv_text.encode("utf-8-sig")
        doc = BufferedInputFile(file_bytes, filename=f"smart_trader_clients_{today_tag}.csv")
        await callback.bot.send_document(
            callback.from_user.id,
            document=doc,
            caption=f"📁 <b>Экспорт базы клиентов CRM ({today_tag})</b>\nФайл готов для открытия в Excel и Google Таблицах.",
            parse_mode="HTML"
        )
        return

    elif action == "dm_prompt":
        target_id = int(parts[2])
        from db.users import get_user
        from bot.keyboards import cancel_crm_action_keyboard
        user = await get_user(target_id)
        un = f"@{user['username']}" if user and user.get("username") else f"ID {target_id}"
        state = get_user_state(callback.from_user.id)
        state["awaiting_crm_dm"] = target_id
        prompt = (
            f"✉️ <b>ПРЯМОЕ СООБЩЕНИЕ КЛИЕНТУ ({un})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Напишите текст сообщения в чат. Бот доставит его клиенту от лица сервиса:\n\n"
            "<i>Для отмены нажмите кнопку ниже 👇</i>"
        )
        await safe_edit(callback, prompt, reply_markup=cancel_crm_action_keyboard(target_id), parse_mode="HTML")
        return

    elif action == "notes_prompt":
        target_id = int(parts[2])
        from db.users import get_user
        from bot.keyboards import cancel_crm_action_keyboard
        user = await get_user(target_id)
        cur_note = user.get("admin_notes") or "(заметки нет)"
        state = get_user_state(callback.from_user.id)
        state["awaiting_crm_notes"] = target_id
        prompt = (
            f"📝 <b>ЗАМЕТКА АДМИНИСТРАТОРА (ID {target_id})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Текущая заметка: <i>«{cur_note}»</i>\n\n"
            "Отправьте новый текст заметки сообщением в чат:\n"
            "<i>(Например: 'Оплатил USDT TRC20, контакт в Telegram, скидка 10%')</i>\n\n"
            "<i>Для отмены нажмите кнопку ниже 👇</i>"
        )
        await safe_edit(callback, prompt, reply_markup=cancel_crm_action_keyboard(target_id), parse_mode="HTML")
        return

    elif action == "custom_days_prompt":
        target_id = int(parts[2])
        from bot.keyboards import cancel_crm_action_keyboard
        state = get_user_state(callback.from_user.id)
        state["awaiting_crm_custom_days"] = target_id
        prompt = (
            f"✍️ <b>ПРОДЛЕНИЕ НА СВОЙ СРОК (ID {target_id})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Введите число дней для продления подписки (например: <code>7</code>, <code>14</code>, <code>45</code>, <code>60</code>):\n\n"
            "<i>Для отмены нажмите кнопку ниже 👇</i>"
        )
        await safe_edit(callback, prompt, reply_markup=cancel_crm_action_keyboard(target_id), parse_mode="HTML")
        return

    elif action == "payments_history":
        target_id = int(parts[2])
        from db.users import get_user, get_user_payments, get_user_ltv
        from bot.keyboards import crm_payments_history_keyboard
        user = await get_user(target_id)
        payments = await get_user_payments(target_id)
        ltv_usd, ltv_cnt = await get_user_ltv(target_id)
        un = f"@{user['username']}" if user and user.get("username") else f"ID {target_id}"

        lines = [
            f"💳 <b>ИСТОРИЯ ОПЛАТ КЛИЕНТА: {un}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Всего оплачено (LTV):</b> <b>${ltv_usd:.2f}</b> (<code>{ltv_cnt}</code> оплат)",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        if not payments:
            lines.append("<i>Платежей в базе пока нет (выдавался триал или ручной доступ).</i>")
        else:
            for p in payments:
                dt_str = (p.get("created_at") or "")[:10]
                amt = float(p.get("amount_usd") or 0.0)
                p_tariff = p.get("tariff") or ""
                p_days = p.get("days_added") or 0
                lines.append(f"• 📅 <code>{dt_str}</code>: <b>+${amt:.2f}</b> ({p_tariff}, +{p_days} дн.)")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await safe_edit(callback, "\n".join(lines), reply_markup=crm_payments_history_keyboard(target_id), parse_mode="HTML")
        return

    elif action == "user":
        target_id = int(parts[2])
        from db.users import get_user
        from bot.keyboards import admin_user_card_keyboard
        user = await get_user(target_id)
        if not user:
            await callback.answer("⚠️ Пользователь не найден!", show_alert=True)
            return

        is_active = (user.get("status") == "approved")
        is_life = bool(user.get("is_lifetime", 0))
        from db.users import get_user_ltv
        ltv_usd, ltv_cnt = await get_user_ltv(target_id)
        text = format_crm_user_card(user, ltv_usd=ltv_usd, payments_cnt=ltv_cnt)
        await safe_edit(callback, text, reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life), parse_mode="HTML")

    elif action == "revoke":
        target_id = int(parts[2])
        from db.users import revoke_user, get_user
        from bot.keyboards import admin_user_card_keyboard
        ok = await revoke_user(target_id)
        if ok:
            await callback.answer("🔴 Доступ пользователю отключен!", show_alert=True)
            try:
                await callback.bot.send_message(
                    target_id,
                    "🔒 <b>Ваш доступ к институциональным сигналам был приостановлен администратором.</b>\n\n"
                    "Для продления тарифа свяжитесь с поддержкой.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            await callback.answer("❌ Ошибка при отключении пользователя.", show_alert=True)

        user = await get_user(target_id)
        if user:
            is_life = bool(user.get("is_lifetime", 0))
            text = format_crm_user_card(user)
            await safe_edit(callback, text, reply_markup=admin_user_card_keyboard(target_id, is_active=False, is_lifetime=is_life), parse_mode="HTML")

    elif action == "restore":
        target_id = int(parts[2])
        from db.users import restore_user, get_user
        from bot.keyboards import admin_user_card_keyboard
        ok = await restore_user(target_id, days=30)
        if ok:
            await callback.answer("🟢 Доступ восстановлен на 30 дней!", show_alert=True)
            try:
                await callback.bot.send_message(
                    target_id,
                    "🎉 <b>Администратор активировал ваш доступ к Smart Trader Bot на 30 дней!</b>\n\n"
                    "Отправьте /start чтобы открыть меню.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            await callback.answer("❌ Ошибка при восстановлении доступа.", show_alert=True)

        user = await get_user(target_id)
        if user:
            is_life = bool(user.get("is_lifetime", 0))
            text = format_crm_user_card(user)
            await safe_edit(callback, text, reply_markup=admin_user_card_keyboard(target_id, is_active=True, is_lifetime=is_life), parse_mode="HTML")

    elif action in ("extend", "extend_days"):
        target_id = int(parts[2])
        days = int(parts[3]) if len(parts) > 3 else 30
        from db.users import extend_subscription, get_user
        from bot.keyboards import admin_user_card_keyboard
        ok, new_exp = await extend_subscription(target_id, days=days)
        if ok:
            exp_date_str = new_exp[:10] if new_exp else "успешно"
            await callback.answer(f"➕ Доступ продлен на {days} дн. (до {exp_date_str})!", show_alert=True)
            try:
                await callback.bot.send_message(
                    target_id,
                    f"💎 <b>Администратор продлил ваш доступ на {days} дней!</b>\n\n"
                    f"Подписка активна до: <code>{exp_date_str}</code> 🚀\n"
                    f"Отправьте /start чтобы открыть терминал.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            await callback.answer("❌ Ошибка при продлении подписки.", show_alert=True)

        user = await get_user(target_id)
        if user:
            is_active = (user.get("status") == "approved")
            is_life = bool(user.get("is_lifetime", 0))
            text = format_crm_user_card(user)
            await safe_edit(callback, text, reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=is_life), parse_mode="HTML")

    elif action == "lifetime":
        target_id = int(parts[2])
        from db.users import get_user, set_lifetime_subscription
        from bot.keyboards import admin_user_card_keyboard
        user = await get_user(target_id)
        if not user:
            await callback.answer("⚠️ Пользователь не найден!", show_alert=True)
            return

        current_life = bool(user.get("is_lifetime", 0))
        new_life = not current_life
        await set_lifetime_subscription(target_id, enable=new_life)

        if new_life:
            await callback.answer("👑 Бессрочный VIP-доступ активирован!", show_alert=True)
            try:
                await callback.bot.send_message(
                    target_id,
                    "👑 <b>Вам предоставлен БЕССРОЧНЫЙ VIP-ДОСТУП к Smart Trader Bot!</b>\n\n"
                    "Все институциональные сигналы теперь доступны вам навсегда без ограничений по времени! 🚀",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            await callback.answer("VIP-статус снят.", show_alert=True)

        user = await get_user(target_id)
        if user:
            is_active = (user.get("status") == "approved")
            text = format_crm_user_card(user)
            await safe_edit(callback, text, reply_markup=admin_user_card_keyboard(target_id, is_active=is_active, is_lifetime=new_life), parse_mode="HTML")

    elif action == "delete":
        target_id = int(parts[2])
        from db.users import delete_user
        ok = await delete_user(target_id)
        if ok:
            await callback.answer("🗑️ Пользователь удален из базы.", show_alert=True)
        text, kb = await get_crm_view(tab="all", page=1)
        await safe_edit(callback, text, reply_markup=kb, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
# МАССОВАЯ РАССЫЛКА КЛИЕНТАМ (ТОЛЬКО АДМИНИСТРАТОР)
# ═══════════════════════════════════════════════════════════

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    """Рассылка объявления всем активным подписчикам."""
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Доступно только администратору.", parse_mode=None)
        return

    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer(
            "📢 <b>МАССОВАЯ РАССЫЛКА КЛИЕНТАМ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Использование:\n"
            "<code>/broadcast Ваш текст объявления здесь...</code>\n\n"
            "Пример:\n"
            "<code>/broadcast Завтра в 15:30 UTC выход новостей по NFP. Рекомендуем снизить риски!</code>",
            parse_mode="HTML"
        )
        return

    from db.users import get_approved_user_ids
    user_ids = await get_approved_user_ids()
    target_ids = [uid for uid in user_ids if uid != config.ADMIN_ID]

    if not target_ids:
        await message.answer("⚠️ Нет активных клиентов для рассылки.", parse_mode=None)
        return

    broadcast_msg = (
        "📢 <b>ОБЪЯВЛЕНИЕ ОТ АДМИНИСТРАЦИИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏛 <i>Smart Trader Bot Official</i>"
    )

    success_cnt = 0
    fail_cnt = 0
    for uid in target_ids:
        try:
            await message.bot.send_message(uid, broadcast_msg, parse_mode="HTML")
            success_cnt += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning("Failed broadcast to %d: %s", uid, e)
            fail_cnt += 1

    report = (
        "✅ <b>РАССЫЛКА УСПЕШНО ЗАВЕРШЕНА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• 👥 Получателей: <b>{len(target_ids)}</b>\n"
        f"• 🟢 Успешно доставлено: <b>{success_cnt}</b>\n"
        f"• 🔴 Ошибок доставки: <b>{fail_cnt}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(report, parse_mode="HTML")


@router.callback_query(F.data == "broadcast:cancel")
async def cb_broadcast_cancel(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return
    await safe_edit(callback, "❌ Рассылка отменена.", reply_markup=back_keyboard(), parse_mode=None)


@router.message(Command("reset_drawdown"))
async def cmd_reset_drawdown(message: Message):
    """Ручной сброс блокировки просадки."""
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Только для администратора.", parse_mode=None)
        return
    try:
        from db.database import set_drawdown_reset_now
        ok = await set_drawdown_reset_now()
        if ok:
            await message.answer(
                "✅ <b>Защита от просадки успешно сброшена!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🚀 Авто-сканер сигналов разблокирован и готов находить новые сетапы!",
                parse_mode="HTML",
                reply_markup=back_keyboard()
            )
        else:
            await message.answer("❌ Ошибка при сбросе просадки.", parse_mode=None)
    except Exception as e:
        logger.error("cmd_reset_drawdown error: %s", e)
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)
