from strategies.base import IndicatorResult, StrategyResult, FullAnalysisResult, MultiTFResult, EconomicEvent
from market.data_fetcher import DataFetcher


def format_price(price: float | None, symbol: str) -> str:
    """Форматирует цену в зависимости от инструмента."""
    if price is None:
        return "—"
    if 'XAU' in symbol or 'JPY' in symbol:
        return f"{price:.2f}"
    return f"{price:.5f}"


def escape_md(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2 (if needed)."""
    special_chars = r'_*[]()~`>#+-=|{}.!'
    res = text
    for char in special_chars:
        res = res.replace(char, f'\\{char}')
    return res


def format_indicators(result: IndicatorResult, symbol: str, timeframe: str) -> str:
    """Форматирует вывод технических индикаторов."""
    trend_emoji = "📈" if result.trend == "BULLISH" else "📉" if result.trend == "BEARISH" else "⚪"
    rsi_emoji = "⚠️" if result.rsi_state in ["перекуплен", "перепродан"] else "✅"
    vol_emoji = "💥" if result.volume_state in ["очень высокий", "повышенный"] else "📊"
    
    text = f"📊 Технический Анализ: {symbol} ({timeframe})\n"
    text += f"Текущая цена: {format_price(result.current_price, symbol)}\n\n"
    
    text += f"🧭 Тренд {trend_emoji}\n"
    text += f"Состояние: {result.trend}\n"
    text += f"EMA 21: {format_price(result.ema_21, symbol)} | EMA 50: {format_price(result.ema_50, symbol)}\n"
    text += f"EMA 200: {format_price(result.ema_200, symbol)}\n"
    text += f"Резюме: {result.price_vs_ema}\n\n"
    
    text += f"⚡ Моментум {rsi_emoji}\n"
    text += f"RSI (14): {result.rsi:.2f} ({result.rsi_state})\n"
    text += f"StochRSI K/D: {result.stoch_rsi_k:.2f} / {result.stoch_rsi_d:.2f} ({result.stoch_rsi_state})\n\n"
    
    text += f"🌪 Волатильность (Bollinger & ATR)\n"
    text += f"Позиция BB: {result.bb_position}\n"
    text += f"ATR: {format_price(result.atr, symbol)} ({result.atr_percent:.2f}%)\n\n"
    
    text += f"📦 Объемы {vol_emoji}\n"
    text += f"Состояние: {result.volume_state} (x{result.volume_ratio:.2f})\n"
    text += f"Позиция к VWAP: {result.price_vs_vwap}\n"
    
    return text


def format_strategy(result: StrategyResult) -> str:
    """Форматирует вывод одной торговой стратегии."""
    text = f"{result.emoji} Стратегия: {result.name}\n"
    text += f"{result.summary}\n"
    
    if result.signal.entry:
        text += f"Вход: {result.signal.entry:.5f}\n"
    if result.signal.stop_loss:
        text += f"SL: {result.signal.stop_loss:.5f}\n"
    if result.signal.take_profit_1:
        text += f"TP1: {result.signal.take_profit_1:.5f}\n"
        
    if result.details_text:
        text += f"Детали:\n{result.details_text}\n"
        
    return text


def format_multi_tf_analysis(result: MultiTFResult) -> str:
    """Форматтер для ручного мульти-таймфрейм анализа."""
    if not result.tf_analyses:
        return f"Нет данных для анализа {result.symbol}."
        
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    stars = "⭐" * result.overall_stars if result.overall_stars > 0 else "—"
    
    text = ""
    if DataFetcher.is_weekend():
        text += DataFetcher.get_weekend_note() + "\n"
        
    text += f"{result.tag_emoji} АНАЛИЗ ICT / SMC: {result.symbol} [ Маркер: {result.tag_emoji} ]\n"
    text += f"{'━' * 28}\n\n"
    
    text += f"⏱ Мульти-таймфрейм структура:\n"
    for tf_res in result.tf_analyses:
        tf_dir_em = direction_emoji.get(tf_res.direction, "⚪")
        tf_stars = "⭐" * tf_res.confidence if tf_res.confidence > 0 else "—"
        text += f"   {tf_res.timeframe}:  {tf_dir_em} {tf_res.direction}  {tf_stars}\n"
        
    text += f"   ✅ Совпадение {result.tf_agreement}/{result.total_tfs} TF\n\n"
    
    if result.overall_direction != "NEUTRAL":
        order_type_clean = result.order_type.replace('_', ' ')
        text += f"🎯 ТИП ОРДЕРА: {overall_emoji} {order_type_clean}\n"
        if result.current_price:
            text += f"💵 Текущая цена: {format_price(result.current_price, result.symbol)}\n\n"
        
        if result.entry:
            text += f"📍 Точка входа: {format_price(result.entry, result.symbol)}\n"
        if result.stop_loss:
            pips_text = f" (-{result.pips_sl:.1f} пипсов)" if result.pips_sl else ""
            text += f"🛑 Стоп-лосс: {format_price(result.stop_loss, result.symbol)}{pips_text}\n"
        if result.take_profit_1:
            pips_text = f" (+{result.pips_tp1:.1f} пипсов)" if result.pips_tp1 else ""
            rr1_str = f" | 📐 R:R = 1:{result.risk_reward_1:.1f}" if result.risk_reward_1 else ""
            text += f"🎯 Тейк-профит 1: {format_price(result.take_profit_1, result.symbol)}{pips_text}{rr1_str}\n"
        if result.take_profit_2:
            pips_text = f" (+{result.pips_tp2:.1f} пипсов)" if result.pips_tp2 else ""
            rr2_str = f" | 📐 R:R = 1:{result.risk_reward_2:.1f}" if result.risk_reward_2 else ""
            text += f"🎯 Тейк-профит 2: {format_price(result.take_profit_2, result.symbol)}{pips_text}{rr2_str}\n\n"
    else:
        text += f"⚪ Четких институциональных сигналов для входа нет (R:R < 1:2.5).\n\n"
        
    if result.session_text:
        text += f"🕒 Сессия: {result.session_text}\n\n"
        
    text += f"🎯 ВЕРДИКТ: {overall_emoji} {result.overall_direction} {stars}\n"
    text += f"{'━' * 28}\n"
    text += f"💡 Торгуй с умом. Риск 1-2% депозита."
    
    return text


def format_notification(result: MultiTFResult) -> str:
    """
    ЭТАП 1: Выход сигнала.
    Отправляет полный институциональный сетап с уникальным эмодзи-маркером и типом ордера.
    """
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    tag = result.tag_emoji or "🔥"
    order_type_clean = result.order_type.replace('_', ' ')
    
    text = (
        f"{tag} СИГНАЛ ICT / SMC | {result.symbol} [ Маркер: {tag} ]\n"
        f"{'━' * 28}\n"
        f"{overall_emoji} ТИП ОРДЕРА: {order_type_clean} 📥\n"
    )
    
    if result.current_price:
        text += f"💵 Рыночная цена сейчас: {format_price(result.current_price, result.symbol)}\n\n"
    
    if result.entry:
        text += f"📍 ТОЧКА ВХОДА: {format_price(result.entry, result.symbol)}\n"
    if result.stop_loss:
        pips_text = f" (-{result.pips_sl:.1f} пипсов)" if result.pips_sl else ""
        text += f"🛑 СТОП-ЛОСС: {format_price(result.stop_loss, result.symbol)}{pips_text}\n"
    if result.take_profit_1:
        pips_text = f" (+{result.pips_tp1:.1f} пипсов)" if result.pips_tp1 else ""
        rr1_str = f" | 📐 R:R = 1:{result.risk_reward_1:.1f}" if result.risk_reward_1 else ""
        text += f"🎯 ТЕЙК-ПРОФИТ 1: {format_price(result.take_profit_1, result.symbol)}{pips_text}{rr1_str}\n"
    if result.take_profit_2:
        pips_text = f" (+{result.pips_tp2:.1f} пипсов)" if result.pips_tp2 else ""
        rr2_str = f" | 📐 R:R = 1:{result.risk_reward_2:.1f}" if result.risk_reward_2 else ""
        text += f"🎯 ТЕЙК-ПРОФИТ 2: {format_price(result.take_profit_2, result.symbol)}{pips_text}{rr2_str}\n\n"
        
    text += f"⏱ Таймфреймы: "
    tf_texts = []
    for tf_res in result.tf_analyses:
        if tf_res.direction != 'NEUTRAL':
            tf_texts.append(f"{tf_res.timeframe} {direction_emoji.get(tf_res.direction, '⚪')}")
    text += " | ".join(tf_texts) + "\n"
    
    text += (
        f"{'━' * 28}\n"
        f"⏳ Выставьте отложенный ордер {order_type_clean} на {format_price(result.entry, result.symbol)}.\n"
        f"💡 Риск не более 1-2% депозита."
    )
    return text


def format_order_activated(signal: dict) -> str:
    """
    ЭТАП 2: Активация входа (цена коснулась точки входа).
    """
    tag = signal.get('tag_emoji') or '🔥'
    symbol = signal.get('symbol', '')
    direction = signal.get('direction', 'LONG')
    order_type = (signal.get('order_type') or 'BUY_LIMIT').replace('_', ' ')
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    entry = format_price(signal.get('entry_price'), symbol)
    sl = format_price(signal.get('stop_loss'), symbol)
    tp1 = format_price(signal.get('take_profit_1'), symbol)
    tp2 = format_price(signal.get('take_profit_2'), symbol)

    text = (
        f"⚡️ ОРДЕР АКТИВИРОВАН! [ Маркер: {tag} ]\n"
        f"{'━' * 28}\n"
        f"📊 Пара: {symbol} | {dir_emoji} {order_type}\n"
        f"📍 Цена коснулась точки входа: {entry}\n\n"
        f"🚀 Сделка открыта и находится в рынке!\n"
        f"🛑 Стоп-лосс: {sl}\n"
        f"🎯 Цель 1: {tp1} | Цель 2: {tp2}\n\n"
        f"{'━' * 28}\n"
        f"👀 Бот ведет сделку и пришлет отчет о результате."
    )
    return text


def format_signal_result(signal: dict, status: str, close_price: float, pnl_pips: float) -> str:
    """
    ЭТАП 3: Результат сделки (TP или SL).
    """
    tag = signal.get('tag_emoji') or '🔥'
    symbol = signal.get('symbol', '')
    direction = signal.get('direction', 'LONG')
    order_type = (signal.get('order_type') or 'BUY_LIMIT').replace('_', ' ')
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    entry = format_price(signal.get('entry_price'), symbol)
    close_str = format_price(close_price, symbol)
    rr = signal.get('risk_reward') or 2.5

    if status in ('TP1_HIT', 'TP2_HIT'):
        target_name = "TAKE PROFIT 1" if status == 'TP1_HIT' else "TAKE PROFIT 2"
        text = (
            f"🎉 РЕЗУЛЬТАТ СДЕЛКИ [ Маркер: {tag} ]\n"
            f"{'━' * 28}\n"
            f"📊 Пара: {symbol} | {dir_emoji} {order_type}\n"
            f"🏆 ИТОГ: {target_name} ДОСТИГНУТ! 🎯\n\n"
            f"📍 Вход: {entry}\n"
            f"🎯 Фиксация: {close_str}\n"
            f"💰 Чистая прибыль: +{abs(pnl_pips):.1f} пипсов\n"
            f"📐 Итоговый R:R: 1:{rr:.1f} ✅\n\n"
            f"{'━' * 28}\n"
            f"💸 Зафиксируйте прибыль или переведите стоп в безубыток!"
        )
    elif status == 'SL_HIT':
        text = (
            f"🛑 РЕЗУЛЬТАТ СДЕЛКИ [ Маркер: {tag} ]\n"
            f"{'━' * 28}\n"
            f"📊 Пара: {symbol} | {dir_emoji} {order_type}\n"
            f"⚠️ ИТОГ: Сработал Stop Loss ❌\n\n"
            f"📍 Вход: {entry}\n"
            f"🛑 Выход: {close_str}\n"
            f"📉 Результат: -{abs(pnl_pips):.1f} пипсов\n\n"
            f"{'━' * 28}\n"
            f"💡 Убыток строго под контролем (1%). Дисциплина — залог профита. Ждем следующий сигнал!"
        )
    else:  # EXPIRED / CANCELLED
        text = (
            f"⏰ ОРДЕР ОТМЕНЕН [ Маркер: {tag} ]\n"
            f"{'━' * 28}\n"
            f"📊 Пара: {symbol} | {dir_emoji} {order_type}\n"
            f"⏳ Срок ожидания точки входа истек (ордер не был активирован).\n\n"
            f"{'━' * 28}\n"
            f"💡 Удалите отложенный ордер из торгового терминала."
        )
    return text


def format_signals_summary(results: list[MultiTFResult]) -> str:
    """Summary across pairs (only show pairs with signals >=4 stars)."""
    strong_signals = [r for r in results if r.overall_stars >= 4 and r.overall_direction != 'NEUTRAL']
    
    if not strong_signals:
        return "Нет сильных институциональных сигналов (R:R >= 1:2.5) на данный момент."
        
    text = "📋 Сводка Сигналов ICT / SMC\n\n"
    
    for r in strong_signals:
        emoji = "🟢" if r.overall_direction == "LONG" else "🔴"
        tag = r.tag_emoji or "🔥"
        stars = "⭐" * r.overall_stars
        entry = format_price(r.entry, r.symbol)
        text += f"{tag} {r.symbol} | {emoji} {r.order_type.replace('_', ' ')} | {stars} | Вход: {entry}\n"
        
    return text


def format_news_alert(event: EconomicEvent) -> str:
    """News warning."""
    impact_emoji = "🔴" if event.impact.lower() == "high" else ("🟠" if event.impact.lower() == "medium" else "🟡")
    
    text = f"⚠️ ВАЖНАЯ НОВОСТЬ через {event.minutes_until} минут!\n\n"
    text += f"📰 {event.title}\n"
    text += f"🕐 Время: {event.time_str} (UTC+5)\n"
    text += f"🏳️ Страна: {event.country}\n"
    text += f"💥 Импакт: {impact_emoji} {event.impact}\n\n"
    
    if event.affected_pairs:
        text += f"Затронутые пары: {', '.join(event.affected_pairs)}\n\n"
        
    text += f"⚡ Рекомендации:\n"
    text += f"• Рассмотрите закрытие открытых позиций\n"
    text += f"• Не открывайте новые сделки\n"
    text += f"• Ожидайте повышенную волатильность"
    
    return text


def format_stats(stats: dict) -> str:
    """Форматирует статистику сигналов."""
    if not stats or stats.get("total", 0) == 0:
        return (
            "📊 СТАТИСТИКА СИГНАЛОВ\n"
            f"{'━' * 28}\n\n"
            "Пока нет зарегистрированных сигналов.\n"
            "Бот мониторит рынок 24/7."
        )

    total = stats.get("total", 0)
    open_cnt = stats.get("open", 0)
    closed = stats.get("closed", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    expired = stats.get("expired", 0)
    win_rate = stats.get("win_rate", 0.0)
    total_pips = stats.get("total_pips", 0.0)
    avg_rr = stats.get("avg_rr", 0.0)

    pips_sign = "+" if total_pips >= 0 else ""
    pips_emoji = "📈" if total_pips >= 0 else "📉"
    wr_emoji = "🏆" if win_rate >= 60 else ("📊" if win_rate >= 40 else "⚠️")

    text = (
        f"📊 СТАТИСТИКА СИГНАЛОВ\n"
        f"{'━' * 28}\n\n"
        f"📈 Всего сигналов: {total}\n"
        f"🔵 В работе: {open_cnt}\n"
        f"✅ Закрытые: {closed}\n\n"
        f"{wr_emoji} Win Rate: {win_rate:.1f}%\n"
        f"{'━' * 28}\n"
        f"✅ Тейк-профит (TP): {wins}\n"
        f"❌ Стоп-лосс (SL): {losses}\n"
        f"⏰ Отменены/Истекли: {expired}\n\n"
        f"{pips_emoji} Общий PnL: {pips_sign}{total_pips:.1f} пипсов\n"
        f"📐 Средний R:R: 1:{avg_rr:.1f}\n"
    )

    by_dir = stats.get("by_direction", {})
    if by_dir:
        text += f"\n📊 По направлениям:\n"
        for d, data in by_dir.items():
            d_em = "🟢" if d == "LONG" else "🔴"
            t = data.get("total", 0)
            w = data.get("wins", 0)
            wr = (w / t * 100) if t > 0 else 0.0
            text += f"   {d_em} {d}: {t} сигн. ({wr:.0f}% win)\n"

    by_sym = stats.get("by_symbol", {})
    if by_sym:
        text += f"\n🏅 Топ пары:\n"
        for sym, data in by_sym.items():
            t = data.get("total", 0)
            w = data.get("wins", 0)
            wr = (w / t * 100) if t > 0 else 0.0
            text += f"   {sym}: {t} сигн. ({wr:.0f}% win)\n"

    text += (
        f"\n{'━' * 28}\n"
        f"💡 Торгуй с умом. Качество > Количество."
    )
    return text


def format_history(signals: list[dict]) -> str:
    """Форматирует историю последних сигналов."""
    if not signals:
        return (
            "📜 ИСТОРИЯ СИГНАЛОВ\n"
            f"{'━' * 28}\n\n"
            "История пока пуста. Ожидайте новых сигналов."
        )

    text = (
        f"📜 ИСТОРИЯ СИГНАЛОВ (последние {len(signals)})\n"
        f"{'━' * 28}\n\n"
    )

    for s in signals:
        status = s.get("status", "PENDING")
        symbol = s.get("symbol", "???")
        direction = s.get("direction", "LONG")
        order_type = (s.get("order_type") or "LIMIT").replace('_', ' ')
        tag = s.get("tag_emoji") or "🔥"
        pnl = s.get("pnl_pips", 0.0)
        pnl_sign = "+" if pnl > 0 else ""

        if status in ("TP1_HIT", "TP2_HIT"):
            st_icon = "✅"
            res_text = f"TP HIT {pnl_sign}{pnl:.1f} п."
        elif status == "SL_HIT":
            st_icon = "❌"
            res_text = f"SL HIT {pnl:.1f} п."
        elif status == "ACTIVE":
            st_icon = "🚀"
            res_text = "В РЫНКЕ"
        elif status == "PENDING":
            st_icon = "⏳"
            res_text = "ОЖИДАЕТ ВХОДА"
        else:
            st_icon = "⏰"
            res_text = "ИСТЁК"

        d_em = "🟢" if direction == "LONG" else "🔴"
        entry_s = format_price(s.get('entry_price'), symbol)
        text += f"{st_icon} {tag} {symbol} | {d_em} {order_type} @ {entry_s} | {res_text}\n"

    text += f"\n{'━' * 28}\n"
    return text


format_full_analysis = format_multi_tf_analysis

def format_welcome() -> str:
    """Short welcome (guide handles the rest)."""
    text = "👋 Добро пожаловать в Smart Trader Bot!\n"
    text += "Я ваш институциональный ИИ-аналитик по методологии ICT / SMC."
    return text


def format_help() -> str:
    """Updated help with all commands."""
    text = "📖 Справка по Smart Trader Bot (ICT / SMC Edition)\n\n"
    text += "📈 Команды:\n"
    text += "/start - Перезапуск бота и показ гайда\n"
    text += "/analyze [Символ] - Анализ структуры и сетапа ICT/SMC\n"
    text += "/signals - Просмотр текущих активных сигналов\n"
    text += "/stats - Статистика отработки и Win-Rate\n"
    text += "/history - История последних сигналов\n"
    text += "/sessions - Текущие торговые сессии\n"
    text += "/news - Экономический календарь и новости\n"
    text += "/request - Подать заявку на доступ к сигналам\n\n"
    text += "🧠 Торговая методология:\n"
    text += "• Институциональный ICT / Smart Money Concepts.\n"
    text += "• Входы только лимитными ордерами в зонах OTE (0.618-0.705) и Order Block.\n"
    text += "• Строгий фильтр соотношения Risk/Reward: минимум 1:2.5 (цели 1:3 - 1:5).\n"
    text += "• 3-этапное ведение сделки (Сигнал ➡️ Активация ➡️ Результат) с уникальными маркерами."
    return text
