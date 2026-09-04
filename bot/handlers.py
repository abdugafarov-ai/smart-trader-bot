from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
import logging

from market.data_fetcher import DataFetcher
from market.indicators import TechnicalIndicators
from strategies import ALL_STRATEGIES, STRATEGY_MAP
from sessions.trading_sessions import TradingSessions
from bot.guide import get_guide_step, get_total_steps, GUIDE_STEPS
from bot.keyboards import (
    main_menu_keyboard, symbols_keyboard, category_pairs_keyboard,
    timeframes_keyboard, strategies_keyboard, settings_keyboard,
    back_keyboard, guide_keyboard, admin_approve_keyboard
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
    
    # Звезды уверенности: ТРЕБУЕТСЯ сонаправленность минимум 2 таймфреймов (tf_agree >= 2) и R:R >= 2.4
    if overall_dir != 'NEUTRAL' and rr1 and rr1 >= 2.4 and tf_agree >= 2:
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
        total_strategies=len(ALL_STRATEGIES),
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

    # Админ — всегда пропускаем
    if user_id == config.ADMIN_ID:
        state = get_user_state(user_id)
        state["guide_step"] = 0
        await message.answer(format_welcome(), parse_mode="HTML")
        await message.answer(get_guide_step(0), reply_markup=guide_keyboard(0), parse_mode="HTML")
        return

    from db.users import get_user_status
    status = await get_user_status(user_id)

    if status == "approved":
        state = get_user_state(user_id)
        state["guide_step"] = 0
        await message.answer(format_welcome(), parse_mode="HTML")
        await message.answer(get_guide_step(0), reply_markup=guide_keyboard(0), parse_mode="HTML")
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

@router.message(Command("analyze"))
async def cmd_analyze(message: Message):
    parts = message.text.split()
    if len(parts) > 1:
        symbol = parts[1].upper()
        await message.answer(f"🏛 <i>Институциональный анализ {symbol}...</i>", parse_mode="HTML")
        res = await run_multi_tf_analysis(symbol)
        text = format_multi_tf_analysis(res)
        
        # Генерируем график если есть уровни
        if res and res.overall_direction != "NEUTRAL" and res.entry and res.stop_loss:
            try:
                from utils.chart_generator import generate_signal_chart
                from aiogram.types import BufferedInputFile
                
                state = get_user_state(message.from_user.id)
                chart_theme = state.get("chart_theme", "dark")
                
                df_chart = await fetcher.fetch_ohlcv(symbol, "H1", limit=80)
                if df_chart is not None and not df_chart.empty and len(df_chart) >= 20:
                    chart_bytes = generate_signal_chart(
                        df=df_chart, symbol=symbol,
                        direction=res.overall_direction,
                        entry=res.entry, stop_loss=res.stop_loss,
                        tp1=res.take_profit_1, tp2=res.take_profit_2,
                        current_price=res.current_price,
                        order_type=res.order_type,
                        stars=res.overall_stars,
                        theme=chart_theme,
                    )
                    if chart_bytes:
                        photo = BufferedInputFile(chart_bytes, filename=f"analysis_{symbol}.png")
                        # Telegram caption limit = 1024 chars, text may be longer
                        if len(text) <= 1024:
                            await message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=back_keyboard())
                        else:
                            await message.answer_photo(photo=photo, caption=f"📊 <b>{symbol}</b> | {res.overall_direction} | {'★' * res.overall_stars}", parse_mode="HTML")
                            for i in range(0, len(text), 4000):
                                await message.answer(text[i:i+4000], reply_markup=back_keyboard(), parse_mode="HTML")
                        return
            except Exception as e:
                logging.getLogger(__name__).error("Chart generation error: %s", e)
        
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i+4000], reply_markup=back_keyboard(), parse_mode="HTML")
    else:
        await message.answer("📊 <b>Выберите категорию активов для анализа:</b>", reply_markup=symbols_keyboard(), parse_mode="HTML")

@router.message(Command("indicators"))
async def cmd_indicators(message: Message):
    await message.answer("📈 <b>Выберите категорию для технического анализа:</b>", reply_markup=symbols_keyboard(), parse_mode="HTML")

@router.message(Command("sessions"))
async def cmd_sessions(message: Message):
    text = sessions.format_sessions_text()
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

@router.message(Command("signals"))
async def cmd_signals(message: Message):
    await message.answer(f"🔍 <i>Сканирую радар {len(config.ALL_PAIRS)} активов...</i>", parse_mode="HTML")
    results = []
    for sym in config.ALL_PAIRS:
        res = await run_multi_tf_analysis(sym)
        if res:
            results.append(res)
    if results:
        summary = format_signals_summary(results)
        await message.answer(summary, reply_markup=back_keyboard(), parse_mode="HTML")

@router.message(Command("news"))
async def cmd_news(message: Message):
    try:
        from news.economic_calendar import EconomicCalendar
        calendar = EconomicCalendar(config.TIMEZONE)
        events = await calendar.get_events_for_display()
        if not events:
            await message.answer("📰 <i>Нет предстоящих важных новостей в ближайшие 48 часов.</i>", reply_markup=back_keyboard(), parse_mode="HTML")
            return
        header = "📰 <b>ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ | ВАЖНЫЕ РЕЛИЗЫ:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        texts = [header]
        for e in events:
            texts.append(calendar.format_event(e) + "\n")
        full_text = "\n".join(texts)
        for i in range(0, len(full_text), 4000):
            await message.answer(full_text[i:i+4000], reply_markup=back_keyboard(), parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка загрузки календаря: {e}", reply_markup=back_keyboard(), parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(format_help(), reply_markup=back_keyboard(), parse_mode="HTML")

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

@router.message(Command("webapp"))
async def cmd_webapp(message: Message):
    web_url = config.WEBAPP_URL or f"http://194.87.130.137:{config.WEBAPP_PORT}"
    text = (
        "📱 <b>SMART TRADER WEB APP ТЕРМИНАЛ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Интерактивный графический терминал внутри Telegram:\n\n"
        "• 📈 Живые графики TradingView в реальном времени\n"
        "• 🎯 Интерактивный радар сигналов с расчетом R:R\n"
        "• 📊 PnL & Win-Rate статистика и эквити\n"
        "• 📰 Экономический календарь новостей\n"
        "• 🤖 MetaTrader 4/5 Execution Bridge (Автопилот)\n\n"
        f"🔗 <b>Открыть в браузере / Web App:</b>\n"
        f"<code>{web_url}</code>"
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton, WebAppInfo
    builder = InlineKeyboardBuilder()
    if config.WEBAPP_URL:
        builder.row(InlineKeyboardButton(text="📱 Открыть Web App", web_app=WebAppInfo(url=config.WEBAPP_URL)))
    builder.row(InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu"))
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

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

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    await callback.message.edit_text("🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("guide:"))
async def cb_guide(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    
    if action == "next":
        state["guide_step"] += 1
        step = state["guide_step"]
        if step < get_total_steps():
            await callback.message.edit_text(get_guide_step(step), reply_markup=guide_keyboard(step), parse_mode="HTML")
        else:
            await callback.message.edit_text("🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")
    elif action == "skip":
        await callback.message.edit_text("🏛 <b>ГЛАВНОЕ МЕНЮ ТЕРМИНАЛА:</b>", reply_markup=main_menu_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("menu:"))
async def cb_menu_actions(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    if action == "webapp_info":
        web_url = config.WEBAPP_URL or f"http://194.87.130.137:{config.WEBAPP_PORT}"
        text = (
            "📱 <b>SMART TRADER WEB APP ТЕРМИНАЛ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Интерактивный графический терминал прямо внутри Telegram:\n\n"
            "• 📈 Живые графики TradingView в реальном времени\n"
            "• 🎯 Интерактивный радар сигналов с расчетом R:R\n"
            "• 📊 PnL & Win-Rate статистика и кривая капитала\n"
            "• 📰 Экономический календарь макроновостей\n"
            "• 🤖 MetaTrader 4/5 Execution Bridge (Автопилот)\n\n"
            f"🔗 <b>Прямая ссылка на Web-терминал:</b>\n"
            f"<code>{web_url}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Откройте ссылку в браузере или используйте Web App кнопку в меню.</i>"
        )
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "analyze":
        await callback.message.edit_text("📊 <b>Выберите категорию активов для анализа:</b>", reply_markup=symbols_keyboard(), parse_mode="HTML")
    elif action == "indicators":
        await callback.message.edit_text("📈 <b>Выберите категорию для технического анализа:</b>", reply_markup=symbols_keyboard(), parse_mode="HTML")
    elif action == "strategy":
        await callback.message.edit_text("🧠 <b>Выберите аналитическую модель:</b>", reply_markup=strategies_keyboard(), parse_mode="HTML")
    elif action == "sessions":
        text = sessions.format_sessions_text()
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "signals":
        await callback.message.edit_text("📡 <i>Сканирую радар 17 активов...</i>", parse_mode="HTML")
        results = []
        for sym in config.ALL_PAIRS:
            res = await run_multi_tf_analysis(sym)
            if res:
                results.append(res)
        summary = format_signals_summary(results) if results else "Нет данных"
        await callback.message.edit_text(summary, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "news":
        try:
            from news.economic_calendar import EconomicCalendar
            calendar = EconomicCalendar(config.TIMEZONE)
            events = await calendar.get_events_for_display()
            if not events:
                await callback.message.edit_text("📰 <i>Нет предстоящих важных новостей в ближайшие 48 часов.</i>", reply_markup=back_keyboard(), parse_mode="HTML")
                return
            header = "📰 <b>ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ | ВАЖНЫЕ РЕЛИЗЫ:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            texts = [header]
            for e in events:
                texts.append(calendar.format_event(e) + "\n")
            full_text = "\n".join(texts)
            for i in range(0, len(full_text), 4000):
                if i == 0:
                    await callback.message.edit_text(full_text[i:i+4000], reply_markup=back_keyboard(), parse_mode="HTML")
                else:
                    await callback.message.answer(full_text[i:i+4000], parse_mode="HTML")
        except Exception as e:
            await callback.message.edit_text(f"⚠️ Ошибка загрузки календаря: {e}", reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "stats":
        from db.database import get_stats
        stats = await get_stats()
        text = format_stats(stats)
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "history":
        from db.database import get_recent_signals
        signals = await get_recent_signals(limit=15)
        text = format_history(signals)
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "equity":
        try:
            from db.database import get_recent_signals, get_stats
            from backtest.equity_chart import generate_equity_curve_chart
            from aiogram.types import BufferedInputFile

            signals = await get_recent_signals(limit=50)
            closed_signals = [s for s in reversed(signals) if s.get('status') in ['TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BREAKEVEN', 'EXPIRED']]

            if len(closed_signals) < 2:
                await callback.message.edit_text("📊 Пока недостаточно закрытых сделок для графика кривой капитала (нужно минимум 2).", reply_markup=back_keyboard(), parse_mode="HTML")
                return

            equity = [0.0]
            curr = 0.0
            for s in closed_signals:
                curr += (s.get('pnl_pips') or 0.0)
                equity.append(round(curr, 1))

            stats = await get_stats()
            chart_bytes = generate_equity_curve_chart(
                equity_points=equity,
                title="LIVE PORTFOLIO EQUITY CURVE",
                symbol="REAL SIGNALS",
                total_pnl=stats.get('total_pips', 0.0),
                win_rate=stats.get('win_rate', 0.0),
                profit_factor=1.5,
                max_dd=0.0
            )

            photo = BufferedInputFile(chart_bytes, filename="live_equity.png")
            cap = (
                f"📈 <b>LIVE EQUITY CURVE | КРИВАЯ КАПИТАЛА</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Общий PnL:</b> <code>{stats.get('total_pips', 0.0):+.1f} pips</code>\n"
                f"🏆 <b>Win Rate:</b> <code>{stats.get('win_rate', 0.0):.1f}%</code>\n"
                f"📊 <b>Всего закрыто:</b> <code>{stats.get('closed', 0)}</code> сделок\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await callback.message.delete()
            await callback.message.answer_photo(photo, caption=cap, parse_mode="HTML", reply_markup=back_keyboard())
        except Exception as e:
            await callback.message.edit_text(f"⚠️ Ошибка построения кривой: {e}", reply_markup=back_keyboard(), parse_mode="HTML")
    elif action == "help":
        await callback.message.edit_text(format_help(), reply_markup=back_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("sym_backtest:"))
async def cb_sym_backtest(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.message.edit_text(f"⏳ <i>Запуск бэктеста по {symbol} (H1, 300 баров)...</i>", parse_mode="HTML")
    try:
        from backtest.backtester import InstitutionalBacktester
        from backtest.equity_chart import generate_equity_curve_chart
        from aiogram.types import BufferedInputFile

        tester = InstitutionalBacktester()
        res = await tester.run_backtest(symbol=symbol, timeframe="H1", limit=300)

        report_text = (
            f"🔬 <b>WALL STREET | INSTITUTIONAL BACKTEST REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Актив:</b> <code>{res.symbol}</code> | <b>ТФ:</b> <code>{res.timeframe}</code>\n"
            f"┌ 📈 <b>Всего сделок:</b> <code>{res.total_trades}</code>\n"
            f"├ 🏆 <b>Win Rate:</b> <code>{res.win_rate}%</code>\n"
            f"├ ✅ <b>Тейк-профиты (TP):</b> <code>{res.wins}</code>\n"
            f"├ ❌ <b>Стоп-лоссы (SL):</b> <code>{res.losses}</code>\n"
            f"├ 🛡 <b>Безубытки (BE):</b> <code>{res.breakevens}</code>\n"
            f"├ 💰 <b>Итоговый PnL:</b> <code>{res.total_pips:+.1f} pips</code>\n"
            f"├ 📐 <b>Суммарный R:</b> <code>{res.total_r:+.2f}R</code>\n"
            f"├ ⚡ <b>Profit Factor:</b> <code>{res.profit_factor:.2f}</code>\n"
            f"└ 📉 <b>Max Drawdown:</b> <code>-{res.max_drawdown_pips:.1f} pips</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <i>Симуляция с Breakeven 1:1 и Partial Close 50%.</i>"
        )

        chart_bytes = generate_equity_curve_chart(
            equity_points=res.equity_curve,
            title="INSTITUTIONAL STRATEGY BACKTEST",
            symbol=f"{res.symbol} ({res.timeframe})",
            total_pnl=res.total_pips,
            win_rate=res.win_rate,
            profit_factor=res.profit_factor,
            max_dd=res.max_drawdown_pips
        )

        await callback.message.delete()
        photo = BufferedInputFile(chart_bytes, filename=f"backtest_{symbol}.png")
        await callback.message.answer_photo(photo, caption=report_text, parse_mode="HTML", reply_markup=back_keyboard())
    except Exception as e:
        logger.error("cb_sym_backtest error: %s", e, exc_info=True)
        await callback.message.edit_text(f"❌ Ошибка бэктеста: {e}", reply_markup=back_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    category = callback.data.split(":")[1]
    await callback.message.edit_text("📊 <b>Выберите торговый инструмент:</b>", reply_markup=category_pairs_keyboard(category), parse_mode="HTML")

@router.callback_query(F.data.startswith("sym:"))
async def cb_symbol(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    state["symbol"] = symbol
    await callback.message.edit_text(f"🏛 <i>Глубокий анализ {symbol} по модели ICT/SMC...</i>", parse_mode="HTML")
    res = await run_multi_tf_analysis(symbol)
    if res:
        text = format_multi_tf_analysis(res)
        for i in range(0, len(text), 4000):
            if i == 0:
                await callback.message.edit_text(text[i:i+4000], reply_markup=back_keyboard(), parse_mode="HTML")
            else:
                await callback.message.answer(text[i:i+4000], parse_mode="HTML")
    else:
        await callback.message.edit_text("⚠️ Ошибка получения котировок.", reply_markup=back_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("tf:"))
async def cb_timeframe(callback: CallbackQuery):
    tf = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    state["timeframe"] = tf
    await callback.message.edit_text(f"Таймфрейм изменен на {tf}", reply_markup=back_keyboard())

@router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    state = get_user_state(callback.from_user.id)
    chart_theme = state.get("chart_theme", "dark")
    theme_label = "🌙 Тёмная" if chart_theme == "dark" else "☀️ Светлая"
    text = (
        f"⚙️ Текущие настройки:\n\n"
        f"📊 Пара: {state['symbol']}\n"
        f"⏱ Таймфрейм: {state['timeframe']}\n"
        f"🎨 Тема графиков: {theme_label}\n"
    )
    await callback.message.edit_text(text, reply_markup=settings_keyboard())

@router.callback_query(F.data.startswith("settings:"))
async def cb_settings_action(callback: CallbackQuery):
    action = callback.data.split(":")[1]
    if action == "pairs":
        await callback.message.edit_text("Выберите категорию:", reply_markup=symbols_keyboard())
    elif action == "tf":
        await callback.message.edit_text("Выберите таймфрейм:", reply_markup=timeframes_keyboard())
    elif action == "chart_theme":
        state = get_user_state(callback.from_user.id)
        current = state.get("chart_theme", "dark")
        new_theme = "light" if current == "dark" else "dark"
        state["chart_theme"] = new_theme
        theme_label = "🌙 Тёмная (Wall Street)" if new_theme == "dark" else "☀️ Светлая (Classic)"
        await callback.message.edit_text(
            f"🎨 <b>Тема графиков изменена:</b> {theme_label}\n\n"
            f"Все новые графики будут генерироваться в выбранной теме.",
            reply_markup=back_keyboard(),
            parse_mode="HTML",
        )
    elif action == "notif":
        await callback.message.edit_text(
            "🔔 Для настройки уведомлений добавьте свой Telegram ID "
            "в переменную NOTIFY_USER_IDS в файле .env и перезапустите бота.\n\n"
            "Узнать свой ID: @userinfobot",
            reply_markup=back_keyboard()
        )

@router.callback_query(F.data.startswith("strat:"))
async def cb_strategy(callback: CallbackQuery):
    strat = callback.data.split(":")[1]
    state = get_user_state(callback.from_user.id)
    symbol = state["symbol"]
    tf = state["timeframe"]
    
    await callback.message.edit_text(f"Запуск стратегии {strat} для {symbol}...", parse_mode=None)
    
    df = await fetcher.fetch_ohlcv(symbol, tf)
    if df is None or df.empty:
        await callback.message.edit_text("Ошибка данных.", reply_markup=back_keyboard())
        return
        
    df = TechnicalIndicators.calculate_all(df)
    
    if strat == "all":
        text = f"<b>Все стратегии для {symbol} ({tf})</b>\n\n"
        for strategy in ALL_STRATEGIES:
            res = strategy.analyze(df, symbol, tf)
            text += f"{res.emoji} <b>{res.name}</b>: {res.summary}\n"
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode="HTML")
    else:
        strategy_class = STRATEGY_MAP.get(strat)
        if strategy_class:
            res = strategy_class.analyze(df, symbol, tf)
            text = format_strategy(res)
        else:
            text = "Стратегия не найдена."
        await callback.message.edit_text(text, reply_markup=back_keyboard(), parse_mode=None)


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


@router.message(Command("backtest"))
async def cmd_backtest(message: Message):
    """Запуск институционального бэктеста ICT/SMC."""
    parts = message.text.strip().split()
    symbol = parts[1].upper() if len(parts) > 1 else "EURUSD"
    tf = parts[2].upper() if len(parts) > 2 else "H1"

    status_msg = await message.answer(
        f"⏳ <i>Запуск институционального бэктеста по {symbol} ({tf}) на 300 свечах...</i>",
        parse_mode="HTML"
    )

    try:
        from backtest.backtester import InstitutionalBacktester
        from backtest.equity_chart import generate_equity_curve_chart
        from aiogram.types import BufferedInputFile

        tester = InstitutionalBacktester()
        res = await tester.run_backtest(symbol=symbol, timeframe=tf, limit=300)

        if res.total_trades == 0:
            await status_msg.edit_text(f"❌ Недостаточно данных для бэктеста {symbol}.", parse_mode=None)
            return

        report_text = (
            f"🔬 <b>WALL STREET | INSTITUTIONAL BACKTEST REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Актив:</b> <code>{res.symbol}</code> | <b>ТФ:</b> <code>{res.timeframe}</code>\n"
            f"┌ 📈 <b>Всего сделок:</b> <code>{res.total_trades}</code>\n"
            f"├ 🏆 <b>Win Rate:</b> <code>{res.win_rate}%</code>\n"
            f"├ ✅ <b>Тейк-профиты (TP):</b> <code>{res.wins}</code>\n"
            f"├ ❌ <b>Стоп-лоссы (SL):</b> <code>{res.losses}</code>\n"
            f"├ 🛡 <b>Безубытки (BE):</b> <code>{res.breakevens}</code>\n"
            f"├ 💰 <b>Итоговый PnL:</b> <code>{res.total_pips:+.1f} pips</code>\n"
            f"├ 📐 <b>Суммарный R:</b> <code>{res.total_r:+.2f}R</code>\n"
            f"├ ⚡ <b>Profit Factor:</b> <code>{res.profit_factor:.2f}</code>\n"
            f"└ 📉 <b>Max Drawdown:</b> <code>-{res.max_drawdown_pips:.1f} pips</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <i>Симуляция с Breakeven 1:1 и Partial Close 50%.</i>"
        )

        chart_bytes = generate_equity_curve_chart(
            equity_points=res.equity_curve,
            title="INSTITUTIONAL STRATEGY BACKTEST",
            symbol=f"{res.symbol} ({res.timeframe})",
            total_pnl=res.total_pips,
            win_rate=res.win_rate,
            profit_factor=res.profit_factor,
            max_dd=res.max_drawdown_pips
        )

        await status_msg.delete()
        photo = BufferedInputFile(chart_bytes, filename=f"backtest_{symbol}.png")
        await message.answer_photo(photo, caption=report_text, parse_mode="HTML", reply_markup=back_keyboard())

    except Exception as e:
        logger.error("Backtest error: %s", e, exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка бэктеста: {e}", parse_mode=None)


@router.message(Command("equity"))
async def cmd_equity(message: Message):
    """График кривой капитала на основе реальных закрытых сигналов."""
    try:
        from db.database import get_recent_signals, get_stats
        from backtest.equity_chart import generate_equity_curve_chart
        from aiogram.types import BufferedInputFile

        signals = await get_recent_signals(limit=50)
        closed_signals = [s for s in reversed(signals) if s.get('status') in ['TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BREAKEVEN', 'EXPIRED']]

        if len(closed_signals) < 2:
            await message.answer("📊 Пока недостаточно закрытых сделок для построения графика кривой капитала (нужно минимум 2 закрытых сделки).", reply_markup=back_keyboard(), parse_mode=None)
            return

        equity = [0.0]
        curr = 0.0
        for s in closed_signals:
            curr += (s.get('pnl_pips') or 0.0)
            equity.append(round(curr, 1))

        stats = await get_stats()
        chart_bytes = generate_equity_curve_chart(
            equity_points=equity,
            title="LIVE PORTFOLIO EQUITY CURVE",
            symbol="REAL SIGNALS",
            total_pnl=stats.get('total_pips', 0.0),
            win_rate=stats.get('win_rate', 0.0),
            profit_factor=1.5,
            max_dd=0.0
        )

        photo = BufferedInputFile(chart_bytes, filename="live_equity.png")
        cap = (
            f"📈 <b>LIVE EQUITY CURVE | КРИВАЯ КАПИТАЛА</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Общий PnL:</b> <code>{stats.get('total_pips', 0.0):+.1f} pips</code>\n"
            f"🏆 <b>Win Rate:</b> <code>{stats.get('win_rate', 0.0):.1f}%</code>\n"
            f"📊 <b>Всего закрыто:</b> <code>{stats.get('closed', 0)}</code> сделок\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await message.answer_photo(photo, caption=cap, parse_mode="HTML", reply_markup=back_keyboard())

    except Exception as e:
        logger.error("Equity command error: %s", e, exc_info=True)
        await message.answer(f"❌ Ошибка генерации графика: {e}", parse_mode=None)


