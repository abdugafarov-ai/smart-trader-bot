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
    
    # Звезды уверенности (только при R:R >= 2.4)
    if overall_dir != 'NEUTRAL' and rr1 and rr1 >= 2.4 and tf_agree >= 2:
        overall_stars = 5 if tf_agree >= 3 else 4
    elif overall_dir != 'NEUTRAL' and rr1 and rr1 >= 2.4:
        overall_stars = 4
    else:
        overall_stars = 0
        overall_dir = "NEUTRAL"

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
    if action == "analyze":
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
    elif action == "help":
        await callback.message.edit_text(format_help(), reply_markup=back_keyboard(), parse_mode="HTML")

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

