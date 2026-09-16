from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
import logging
logger = logging.getLogger(__name__)

from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies import ALL_STRATEGIES, STRATEGY_MAP
from sessions.trading_sessions import TradingSessions
from bot.guide import get_guide_step, get_total_steps, GUIDE_STEPS
from bot.keyboards import (
    main_menu_keyboard, symbols_keyboard, category_pairs_keyboard,
    back_keyboard, guide_keyboard, admin_approve_keyboard,
    analysis_result_keyboard, terminal_dashboard_keyboard, panic_confirm_keyboard
)
from utils.formatters import (
    format_indicators, format_strategy, format_multi_tf_analysis,
    format_notification, format_signals_summary, format_news_alert,
    format_welcome, format_help,
    format_stats, format_history
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
    except Exception:
        try:
            return await msg.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.error("safe_edit message send failed: %s", e)
            return None

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
    
    # Звезды уверенности: ТРЕБУЕТСЯ сонаправленность минимум 2 таймфреймов (tf_agree >= 2) и R:R >= 2.0
    if overall_dir != 'NEUTRAL' and rr1 and rr1 >= 1.5 and tf_agree >= 1:
        # Graduated star system: 1 TF + low R:R = 3 stars, up to 5 stars for best setups
        if tf_agree >= 3 and rr1 >= 2.5:
            overall_stars = 5
        elif tf_agree >= 3 and rr1 >= 2.0:
            overall_stars = 5
        elif tf_agree >= 2 and rr1 >= 2.0:
            overall_stars = 4
        elif tf_agree >= 2 and rr1 >= 1.5:
            overall_stars = 3
        elif tf_agree >= 1 and rr1 >= 2.0:
            overall_stars = 3
        else:
            overall_stars = 0
            overall_dir = "NEUTRAL"
            entry = sl = tp1 = tp2 = rr1 = None
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
    from bot.keyboards import main_menu_keyboard

    # Админ — всегда пропускаем в Главное Меню
    if user_id == config.ADMIN_ID:
        await message.answer(format_welcome(), reply_markup=main_menu_keyboard(), parse_mode="HTML")
        return

    from db.users import get_user_status
    status = await get_user_status(user_id)

    if status == "approved":
        await message.answer(format_welcome(), reply_markup=main_menu_keyboard(), parse_mode="HTML")
    elif status == "pending":
        await message.answer(
            "⏳ <b>Ваша заявка на рассмотрении.</b>\n"
            "Администратор скоро проверит доступ.\n\n"
            "Ожидайте уведомления! 🔔",
            parse_mode="HTML"
        )
    elif status == "rejected":
        await message.answer(
            "❌ <b>Ваша заявка была отклонена.</b>\n"
            "Свяжитесь с администратором.",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "🏛 <b>ДОБРО ПОЖАЛОВАТЬ В SMART TRADER TERMINAL</b>\n\n"
            "🔒 <b>Доступ к институциональному терминалу закрыт.</b>\n"
            "Отправьте команду <code>/request</code>, чтобы подать заявку на доступ.\n\n"
            "Администратор рассмотрит вашу кандидатуру.",
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
    parts = message.text.split()
    if len(parts) > 1:
        try:
            val = float(parts[1].replace(',', '.'))
            from trading.execution_bridge import bridge_manager
            bridge_manager.set_risk(val)
            await message.answer(f"✅ Риск на сделку установлен: <b>{bridge_manager.default_risk}%</b>", parse_mode="HTML")
            return
        except ValueError:
            pass
    await message.answer("Использование: <code>/risk 1.0</code> (процент риска от 0.1% до 5.0%)", parse_mode="HTML")

@router.message(Command("lot"))
async def cmd_lot(message: Message):
    parts = message.text.split()
    if len(parts) > 1:
        try:
            val = float(parts[1].replace(',', '.'))
            from trading.execution_bridge import bridge_manager
            bridge_manager.set_lot(val)
            await message.answer(f"✅ Фиксированный лот установлен: <b>{bridge_manager.default_lot}</b>", parse_mode="HTML")
            return
        except ValueError:
            pass
    await message.answer("Использование: <code>/lot 0.02</code> (размер лота от 0.01 до 10.0)", parse_mode="HTML")

@router.message(Command("terminal"))
@router.message(Command("account"))
async def cmd_terminal(message: Message):
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import terminal_dashboard_keyboard
    text = bridge_manager.format_terminal_dashboard()
    await message.answer(text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    await safe_edit(callback, "🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")

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
        from trading.execution_bridge import bridge_manager
        from bot.keyboards import terminal_dashboard_keyboard
        text = bridge_manager.format_terminal_dashboard()
        await safe_edit(callback, text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")
    elif action == "help":
        from bot.keyboards import help_menu_keyboard
        await safe_edit(callback, format_help(), reply_markup=help_menu_keyboard(), parse_mode="HTML")
    elif action == "autotrade":
        from trading.execution_bridge import bridge_manager
        from bot.keyboards import autotrade_keyboard
        status_emoji = "🟢 ВКЛЮЧЕН (АКТИВЕН)" if bridge_manager.enabled else "🔴 ПРИОСТАНОВЛЕН (ПАУЗА)"
        text = (
            f"⚙️ <b>НАСТРОЙКИ АВТОПИЛОТА (MT5 BRIDGE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 <b>Статус авто-торговли:</b> {status_emoji}\n"
            f"📊 <b>Рабочий лот:</b> <code>{bridge_manager.default_lot}</code>\n"
            f"⚖️ <b>Риск на сделку:</b> <code>{bridge_manager.default_risk}%</code>\n"
            f"💱 <b>Инструментов в пуле:</b> <code>17 пар (Форекс + Золото)</code>\n"
            f"🛡 <b>Auto-Breakeven:</b> <code>Включён (в безубыток +0.50$ на 50% TP1)</code>\n\n"
            f"Используйте кнопки ниже для быстрого управления 👇\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await safe_edit(callback, text, reply_markup=autotrade_keyboard(bridge_manager.enabled), parse_mode="HTML")
    elif action == "main":
        await safe_edit(callback, format_welcome(), reply_markup=main_menu_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "terminal:refresh")
async def cb_terminal_refresh(callback: CallbackQuery):
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import terminal_dashboard_keyboard
    await callback.answer("🔄 Данные из MT5 обновлены!")
    text = bridge_manager.format_terminal_dashboard()
    await safe_edit(callback, text, reply_markup=terminal_dashboard_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "terminal:panic_confirm")
async def cb_panic_confirm(callback: CallbackQuery):
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


@router.callback_query(F.data == "terminal:screenshot")
async def cb_terminal_screenshot(callback: CallbackQuery):
    from utils.screenshot import capture_mt5_screenshot
    from aiogram.types import BufferedInputFile
    from bot.keyboards import back_keyboard

    await callback.answer("📸 Захватываю экран MT5...")
    png_bytes = await capture_mt5_screenshot()
    if not png_bytes:
        await callback.message.answer(
            "⚠️ Не удалось сделать снимок экрана MT5.\nВозможно, виртуальный дисплей перезагружается.",
            reply_markup=back_keyboard("menu:terminal")
        )
        return

    photo = BufferedInputFile(png_bytes, filename="mt5_live.png")
    await callback.message.answer_photo(
        photo=photo,
        caption=(
            "📸 <b>РЕАЛЬНЫЙ ЭКРАН ТЕРМИНАЛА METATRADER 5</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🖥 <i>Прямой снимок из графической сессии VPS (MetaTrader 5 x64).</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard("menu:terminal")
    )


@router.message(Command("screenshot"))
async def cmd_screenshot(message: Message):
    from utils.screenshot import capture_mt5_screenshot
    from aiogram.types import BufferedInputFile
    from bot.keyboards import back_keyboard

    msg_wait = await message.answer("📸 <i>Захватываю экран терминала MT5...</i>", parse_mode="HTML")
    png_bytes = await capture_mt5_screenshot()
    if not png_bytes:
        await msg_wait.edit_text(
            "⚠️ Не удалось сделать снимок экрана MT5.\nПроверьте статус Xvfb на сервере.",
            reply_markup=back_keyboard("menu:terminal")
        )
        return

    photo = BufferedInputFile(png_bytes, filename="mt5_live.png")
    try:
        await msg_wait.delete()
    except Exception:
        pass

    await message.answer_photo(
        photo=photo,
        caption=(
            "📸 <b>РЕАЛЬНЫЙ ЭКРАН ТЕРМИНАЛА METATRADER 5</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🖥 <i>Прямой снимок из графической сессии VPS (MetaTrader 5 x64).</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard("menu:terminal")
    )


@router.callback_query(F.data.startswith("autotrade:"))
async def cb_autotrade_actions(callback: CallbackQuery):
    from trading.execution_bridge import bridge_manager
    from bot.keyboards import autotrade_keyboard
    parts = callback.data.split(":")
    action = parts[1]
    if action == "on":
        bridge_manager.set_enabled(True)
        await callback.answer("🟢 Автопилот включен!")
    elif action == "off":
        bridge_manager.set_enabled(False)
        await callback.answer("🔴 Автопилот приостановлен!")
    elif action == "lot":
        val = float(parts[2])
        bridge_manager.set_lot(val)
        await callback.answer(f"🔹 Лот установлен: {val}")
    elif action == "risk":
        val = float(parts[2])
        bridge_manager.set_risk(val)
        await callback.answer(f"⚖️ Риск установлен: {val}%")

    status_emoji = "🟢 ВКЛЮЧЕН (АКТИВЕН)" if bridge_manager.enabled else "🔴 ПРИОСТАНОВЛЕН (ПАУЗА)"
    text = (
        f"⚙️ <b>НАСТРОЙКИ АВТОПИЛОТА (MT5 BRIDGE)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📡 <b>Статус авто-торговли:</b> {status_emoji}\n"
        f"📊 <b>Рабочий лот:</b> <code>{bridge_manager.default_lot}</code>\n"
        f"⚖️ <b>Риск на сделку:</b> <code>{bridge_manager.default_risk}%</code>\n"
        f"💱 <b>Инструментов в пуле:</b> <code>17 пар (Форекс + Золото)</code>\n"
        f"🛡 <b>Auto-Breakeven:</b> <code>Включён (в безубыток +0.50$ на 50% TP1)</code>\n\n"
        f"Используйте кнопки ниже для быстрого управления 👇\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await safe_edit(callback, text, reply_markup=autotrade_keyboard(bridge_manager.enabled), parse_mode="HTML")




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
        for i in range(0, len(text), 4000):
            if i == 0:
                await safe_edit(callback, text[i:i+4000], reply_markup=kb, parse_mode="HTML")
            else:
                await callback.message.answer(text[i:i+4000], parse_mode="HTML")
    else:
        await safe_edit(callback, "⚠️ Ошибка получения котировок.", reply_markup=back_keyboard(), parse_mode="HTML")


@router.callback_query(F.data.startswith("exec_mt5:"))
async def cb_exec_mt5(callback: CallbackQuery):
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
    """Подача заявки на доступ к боту."""
    from db.users import request_access, get_user_status
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""

    # Админ не нуждается в заявке
    if user_id == config.ADMIN_ID:
        await message.answer("👑 Вы администратор. Доступ уже предоставлен!", parse_mode=None)
        return

    status = await get_user_status(user_id)
    if status == "approved":
        await message.answer("✅ У вас уже есть доступ! Отправьте /start", parse_mode=None)
        return
    if status == "pending":
        await message.answer("⏳ Ваша заявка уже на рассмотрении. Ожидайте!", parse_mode=None)
        return
    if status == "rejected":
        await message.answer("❌ Ваша заявка была отклонена ранее.", parse_mode=None)
        return

    # Новая заявка
    is_new = await request_access(user_id, username, first_name)
    if is_new:
        await message.answer(
            "📩 Заявка отправлена!\n\n"
            "Администратор получил уведомление.\n"
            "Ожидайте одобрения. 🔔",
            parse_mode=None
        )
        # Уведомляем админа
        try:
            un_text = f"@{username}" if username else "не указан"
            admin_text = (
                f"📩 НОВАЯ ЗАЯВКА НА ДОСТУП\n"
                f"{'━' * 28}\n\n"
                f"👤 Имя: {first_name}\n"
                f"📛 Username: {un_text}\n"
                f"🆔 ID: {user_id}\n\n"
                f"Одобрить или отклонить?"
            )
            await message.bot.send_message(
                config.ADMIN_ID,
                admin_text,
                reply_markup=admin_approve_keyboard(user_id),
                parse_mode=None
            )
        except Exception as e:
            logging.error("Failed to notify admin: %s", e)
    else:
        await message.answer("Заявка уже существует.", parse_mode=None)


@router.callback_query(F.data.startswith("admin_approve:"))
async def cb_admin_approve(callback: CallbackQuery):
    """Админ одобряет заявку пользователя."""
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("❌ Только администратор!", show_alert=True)
        return

    target_id = int(callback.data.split(":")[1])
    from db.users import approve_user
    await approve_user(target_id)

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ ОДОБРЕНО",
        parse_mode=None
    )

    # Уведомляем пользователя
    try:
        await callback.bot.send_message(
            target_id,
            "✅ Ваша заявка одобрена!\n\n"
            "Добро пожаловать в Smart Trader Bot! 🤖\n"
            "Отправьте /start чтобы начать.",
            parse_mode=None
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
            "❌ К сожалению, ваша заявка отклонена.\n"
            "Свяжитесь с администратором.",
            parse_mode=None
        )
    except Exception:
        pass


@router.message(Command("users"))
async def cmd_users(message: Message):
    """Управление пользователями (только для админа)."""
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("❌ Только для администратора.", parse_mode=None)
        return

    from db.users import get_pending_users, get_approved_user_ids
    pending = await get_pending_users()
    approved_ids = await get_approved_user_ids()

    text = (
        f"👥 УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ\n"
        f"{'━' * 28}\n\n"
        f"✅ Одобрено: {len(approved_ids)}\n"
        f"⏳ Ожидают: {len(pending)}\n\n"
    )

    if pending:
        text += "📋 Ожидающие заявки:\n"
        for u in pending:
            un = f"@{u['username']}" if u.get('username') else 'нет'
            text += f"   • {u.get('first_name', '?')} ({un}) — ID: {u['telegram_id']}\n"
    else:
        text += "📋 Нет ожидающих заявок."

    await message.answer(text, reply_markup=back_keyboard(), parse_mode=None)




@router.message(Command("reset_drawdown"))
async def cmd_reset_drawdown(message: Message):
    """Ручной сброс блокировки просадки."""
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
