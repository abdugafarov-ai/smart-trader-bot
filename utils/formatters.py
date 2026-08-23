from strategies.base import IndicatorResult, StrategyResult, FullAnalysisResult, MultiTFResult, EconomicEvent
from market.data_fetcher import DataFetcher

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
    text += f"Текущая цена: {result.current_price:.5f}\n\n"
    
    text += f"🧭 Тренд {trend_emoji}\n"
    text += f"Состояние: {result.trend}\n"
    text += f"EMA 21: {result.ema_21:.5f} | EMA 50: {result.ema_50:.5f}\n"
    text += f"EMA 200: {result.ema_200:.5f}\n"
    text += f"Резюме: {result.price_vs_ema}\n\n"
    
    text += f"⚡ Моментум {rsi_emoji}\n"
    text += f"RSI (14): {result.rsi:.2f} ({result.rsi_state})\n"
    text += f"StochRSI K/D: {result.stoch_rsi_k:.2f} / {result.stoch_rsi_d:.2f} ({result.stoch_rsi_state})\n\n"
    
    text += f"🌪 Волатильность (Bollinger & ATR)\n"
    text += f"Позиция BB: {result.bb_position}\n"
    text += f"ATR: {result.atr:.5f} ({result.atr_percent:.2f}%)\n\n"
    
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
    """Новый форматтер для мульти-таймфрейм анализа."""
    if not result.tf_analyses:
        return f"Нет данных для анализа {result.symbol}."
        
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    stars = "⭐" * result.overall_stars if result.overall_stars > 0 else "—"
    
    text = ""
    if DataFetcher.is_weekend():
        text += DataFetcher.get_weekend_note() + "\n"
        
    text += f"🔎 АНАЛИЗ: {result.symbol}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    text += f"⏱ Мульти-таймфрейм:\n"
    for tf_res in result.tf_analyses:
        tf_dir_em = direction_emoji.get(tf_res.direction, "⚪")
        tf_stars = "⭐" * tf_res.confidence if tf_res.confidence > 0 else "—"
        text += f"   {tf_res.timeframe}:  {tf_dir_em} {tf_res.direction}  {tf_stars}\n"
        
    text += f"   ✅ Совпадение {result.tf_agreement}/{result.total_tfs} TF\n\n"
    
    best_tf = max(
        [t for t in result.tf_analyses if t.direction == result.overall_direction],
        key=lambda t: t.confidence,
        default=result.tf_analyses[0]
    ) if result.overall_direction != 'NEUTRAL' else result.tf_analyses[0]
    
    text += f"📊 Стратегии (лучший TF — {best_tf.timeframe}):\n"
    for emoji, name, verdict in result.strategy_verdicts:
        text += f"   {verdict[0]} {emoji} {name} — {verdict[2:]}\n"
        
    text += "\n"
    if result.overall_direction != "NEUTRAL":
        if result.entry:
            text += f"📍 Вход: {result.entry:.5f}\n"
        if result.stop_loss:
            pips_text = f" ({'-' if result.overall_direction == 'LONG' else '+'}{result.pips_sl:.1f} пипсов)" if result.pips_sl else ""
            text += f"🛑 Стоп: {result.stop_loss:.5f}{pips_text}\n"
        if result.take_profit_1:
            pips_text = f" (+{result.pips_tp1:.1f} пипсов)" if result.pips_tp1 else ""
            text += f"🎯 Цель 1: {result.take_profit_1:.5f}{pips_text}\n"
        if result.take_profit_2:
            pips_text = f" (+{result.pips_tp2:.1f} пипсов)" if result.pips_tp2 else ""
            text += f"🎯 Цель 2: {result.take_profit_2:.5f}{pips_text}\n"
        if result.risk_reward_1 or result.risk_reward_2:
            rr1 = f"1:{result.risk_reward_1:.1f}" if result.risk_reward_1 else ""
            rr2 = f" / 1:{result.risk_reward_2:.1f}" if result.risk_reward_2 else ""
            text += f"📐 R:R = {rr1}{rr2}\n\n"
    else:
        text += f"Сигналов для входа нет.\n\n"
        
    if result.session_text:
        text += f"🕒 Сессии: {result.session_text}\n\n"
        
    text += f"🎯 ВЕРДИКТ: {overall_emoji} {result.overall_direction} {stars}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"💡 Торгуй с умом. Риск 1-2% депозита."
    
    return text

def format_notification(result: MultiTFResult) -> str:
    """Clean notification format."""
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    stars = "⭐" * result.overall_stars
    
    text = f"🔔 УВЕРЕННЫЙ СИГНАЛ | {result.symbol} | {stars}\n\n"
    
    dir_ru = "Покупка" if result.overall_direction == "LONG" else ("Продажа" if result.overall_direction == "SHORT" else "Нейтрально")
    text += f"{overall_emoji} {result.overall_direction} ({dir_ru})\n\n"
    
    text += f"📊 Стратегии: {result.strategy_agreement}/{result.total_strategies} совпадают\n"
    strat_agreed = [f"✅ {name.split(' ')[0]}" for emoji, name, verdict in result.strategy_verdicts if "✅" in verdict]
    text += f"   {' | '.join(strat_agreed)}\n\n"
    
    text += f"⏱ Таймфреймы: "
    tf_texts = []
    for tf_res in result.tf_analyses:
        if tf_res.direction != 'NEUTRAL':
            tf_texts.append(f"{tf_res.timeframe} {direction_emoji.get(tf_res.direction)}")
    text += " | ".join(tf_texts) + "\n\n"
    
    if result.entry:
        text += f"📍 Вход: {result.entry:.5f}\n"
    if result.stop_loss:
        pips_text = f" ({'-' if result.overall_direction == 'LONG' else '+'}{result.pips_sl:.1f} пипсов)" if result.pips_sl else ""
        text += f"🛑 Стоп: {result.stop_loss:.5f}{pips_text}\n"
    if result.take_profit_1:
        pips_text = f" (+{result.pips_tp1:.1f} пипсов)" if result.pips_tp1 else ""
        text += f"🎯 Цель 1: {result.take_profit_1:.5f}{pips_text}\n"
    if result.risk_reward_1:
        text += f"📐 R:R = 1:{result.risk_reward_1:.1f}\n\n"
        
    text += f"💡 Торгуй с умом. Риск 1-2% депозита."
    return text

def format_signals_summary(results: list[MultiTFResult]) -> str:
    """Summary across all 28 pairs (only show pairs with signals >=4 stars)."""
    strong_signals = [r for r in results if r.overall_stars >= 4 and r.overall_direction != 'NEUTRAL']
    
    if not strong_signals:
        return "Нет сильных сигналов (⭐⭐⭐⭐ или ⭐⭐⭐⭐⭐) на данный момент."
        
    text = "📋 Сводка Сильных Сигналов\n\n"
    
    for r in strong_signals:
        emoji = "🟢" if r.overall_direction == "LONG" else "🔴"
        stars = "⭐" * r.overall_stars
        entry = f"{r.entry:.5f}" if r.entry else "—"
        text += f"🔹 {r.symbol} | {emoji} {r.overall_direction} | {stars} | Вход: {entry}\n"
        
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

def format_welcome() -> str:
    """Short welcome (guide handles the rest)."""
    text = "👋 Добро пожаловать в Smart Trader Bot!\n"
    text += "Я ваш личный аналитический помощник."
    return text

def format_help() -> str:
    """Updated help with all commands including /news."""
    text = "📖 Справка по Smart Trader Bot\n\n"
    text += "📈 Команды:\n"
    text += "/start - Перезапуск бота и показ гайда\n"
    text += "/analyze [Символ] - Комплексный анализ мульти-таймфрейм\n"
    text += "/indicators [Символ] [ТФ] - Подробная сводка технических индикаторов\n"
    text += "/signals - Просмотр текущих сильных сигналов по всем парам\n"
    text += "/sessions - Текущие торговые сессии\n"
    text += "/news - Экономический календарь и новости\n"
    text += "/stats - Статистика сигналов и win-rate\n"
    text += "/history - История последних закрытых и открытых сигналов\n\n"
    text += "🧠 Используемые стратегии:\n"
    text += "• ICT / SMC (Smart Money Concepts) - поиск ликвидности, FVG, Market Structure Shift.\n"
    text += "• Supply & Demand - поиск сильных зон спроса и предложения.\n"
    text += "• Wyckoff - анализ фаз аккумуляции/дистрибуции.\n"
    text += "• Breakout + Retest - торговля пробоев уровней с подтверждением.\n"
    text += "• Scalping - краткосрочные импульсные входы.\n"
    text += "• Volume Analysis - анализ распределения объемов и VWAP.\n"
    return text

def format_full_analysis(result: FullAnalysisResult) -> str:
    """Полный комплексный анализ по всем стратегиям и индикаторам."""
    direction_emoji = {
        "LONG": "🟢",
        "SHORT": "🔴",
        "NEUTRAL": "⚪",
    }
    emoji = direction_emoji.get(result.overall_direction, "⚪")
    
    text = f"🔎 Комплексный Анализ: {result.symbol} ({result.timeframe})\n\n"
    
    text += f"━━━ 📊 Индикаторы ━━━\n"
    if result.indicators:
        text += f"Тренд: {result.indicators.trend}\n"
        text += f"RSI: {result.indicators.rsi:.2f} ({result.indicators.rsi_state})\n"
        text += f"Объем: {result.indicators.volume_state}\n"
    else:
        text += "Нет данных по индикаторам.\n"
        
    text += f"\n━━━ 🧠 Стратегии ━━━\n"
    for st in result.strategies:
        text += format_strategy(st) + "\n"
        
    text += f"━━━ 🎯 Общий Вердикт ━━━\n"
    text += f"Направление: {emoji} {result.overall_direction}\n"
    text += f"Согласие стратегий: {result.strategies_agreeing} из {result.total_strategies}\n"
    
    confidence_stars = "⭐" * result.overall_confidence if result.overall_confidence > 0 else "—"
    text += f"Уверенность: {confidence_stars}\n"
    
    if result.overall_direction != "NEUTRAL":
        text += f"\n💡 Торговая Идея:\n"
        if result.entry_suggestion is not None:
            text += f"Вход: {result.entry_suggestion:.5f}\n"
        if result.stop_suggestion is not None:
            text += f"Stop-Loss: {result.stop_suggestion:.5f}\n"
        if result.tp1_suggestion is not None:
            text += f"Take-Profit 1: {result.tp1_suggestion:.5f}\n"
        if result.tp2_suggestion is not None:
            text += f"Take-Profit 2: {result.tp2_suggestion:.5f}\n"
        if result.rr_suggestion is not None:
            text += f"Risk/Reward: {result.rr_suggestion:.2f}\n"
            
    if result.session_text:
        text += f"\n🕒 Сессия: {result.session_text}\n"
        
    return text

def format_stats(stats: dict) -> str:
    if not stats or stats.get("total", 0) == 0:
        return "Нет данных для статистики."

    total = stats.get("total", 0)
    open_count = stats.get("open", 0)
    closed = stats.get("closed", 0)
    win_rate = stats.get("win_rate", 0.0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    expired = stats.get("expired", 0)
    total_pips = stats.get("total_pips", 0.0)
    avg_rr = stats.get("avg_rr", 0.0)
    by_direction = stats.get("by_direction", {})
    by_symbol = stats.get("by_symbol", {})

    text = "📊 СТАТИСТИКА СИГНАЛОВ\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📈 Всего сигналов: {total}\n"
    text += f"🟢 Открытые: {open_count}\n"
    text += f"✅ Закрытые: {closed}\n\n"
    text += f"🏆 Win Rate: {win_rate:.1f}%\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"✅ Победы (TP): {wins}\n"
    text += f"❌ Проигрыши (SL): {losses}\n"
    text += f"⏰ Истекшие: {expired}\n\n"
    
    pips_sign = "+" if total_pips > 0 else ""
    text += f"💰 Общий PnL: {pips_sign}{total_pips:.1f} пипсов\n"
    text += f"📐 Средний R:R: 1:{avg_rr:.1f}\n\n"
    
    if by_direction:
        text += "📊 По направлениям:\n"
        long_stats = by_direction.get("LONG", {"total": 0, "wins": 0})
        long_win_rate = (long_stats["wins"] / long_stats["total"] * 100) if long_stats["total"] > 0 else 0
        text += f"   🟢 LONG:  {long_stats['total']} сигналов ({long_win_rate:.0f}% win)\n"
        
        short_stats = by_direction.get("SHORT", {"total": 0, "wins": 0})
        short_win_rate = (short_stats["wins"] / short_stats["total"] * 100) if short_stats["total"] > 0 else 0
        text += f"   🔴 SHORT: {short_stats['total']} сигналов ({short_win_rate:.0f}% win)\n\n"
        
    if by_symbol:
        text += "🏅 Топ пары:\n"
        for sym, d in by_symbol.items():
            sym_total = d["total"]
            sym_wins = d["wins"]
            sym_win_rate = (sym_wins / sym_total * 100) if sym_total > 0 else 0
            text += f"   {sym}:  {sym_total} сигналов ({sym_win_rate:.0f}% win)\n"
            
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "💡 Торгуй с умом. Качество > Количество."
    return text

def format_history(signals: list[dict]) -> str:
    if not signals:
        return "Нет данных по истории."
        
    text = f"📜 ИСТОРИЯ СИГНАЛОВ (последние {len(signals)})\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for sig in signals:
        symbol = sig.get('symbol', 'UNKNOWN')
        direction = sig.get('direction', 'NEUTRAL')
        stars = sig.get('stars', 0)
        status = sig.get('status', 'OPEN')
        pnl = sig.get('pnl_pips', 0.0)
        
        dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
        star_str = "⭐" * stars
        
        pnl_str = ""
        if status in ['TP1_HIT', 'TP2_HIT']:
            status_em = "✅"
            pnl_str = f" +{pnl:.1f} пипс"
        elif status == 'SL_HIT':
            status_em = "❌"
            pnl_str = f" {pnl:.1f} пипс"
        elif status == 'EXPIRED':
            status_em = "⏰"
            pips_sign = "+" if pnl > 0 else ""
            pnl_str = f" {pips_sign}{pnl:.1f} пипс"
        else:
            status_em = "🔵"
            
        status_text = {
            'OPEN': 'ОТКРЫТ',
            'TP1_HIT': 'TP1 HIT',
            'TP2_HIT': 'TP2 HIT',
            'SL_HIT': 'SL HIT',
            'EXPIRED': 'ИСТЁК'
        }.get(status, status)
            
        text += f"{status_em} {symbol} | {dir_emoji} | {star_str} | {status_text}{pnl_str}\n"
        
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    return text
