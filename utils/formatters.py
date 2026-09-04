"""
Smart Trader Bot — Formatters.
Стиль: 🏛 «Wall Street / Bloomberg Terminal» (Институциональный / Премиальный финансовый терминал).
Используется HTML форматирование с моноширинными блоками для идеального выравнивания котировок.
"""

import html
from strategies.base import IndicatorResult, StrategyResult, MultiTFResult, EconomicEvent
from market.data_fetcher import DataFetcher


def format_price(price: float | None, symbol: str) -> str:
    """Форматирует цену в моноширинный формат в зависимости от инструмента."""
    if price is None:
        return "—"
    if 'XAU' in symbol:
        return f"{price:.2f}"
    if 'JPY' in symbol:
        return f"{price:.3f}"
    return f"{price:.5f}"


def escape_html(text: str) -> str:
    """Экранирует спецсимволы для HTML."""
    return html.escape(str(text))


# ── 1. Индикаторы (Терминальный вид) ─────────────────────────

def format_indicators(result: IndicatorResult, symbol: str, timeframe: str) -> str:
    """Форматирует вывод технических индикаторов в стиле терминала."""
    trend_emoji = "📈" if result.trend == "BULLISH" else "📉" if result.trend == "BEARISH" else "⚪"
    rsi_emoji = "⚠️" if result.rsi_state in ["перекуплен", "перепродан"] else "✅"
    vol_emoji = "💥" if result.volume_state in ["очень высокий", "повышенный"] else "📊"
    
    price_str = format_price(result.current_price, symbol)
    
    return (
        f"🏛 <b>TERMINAL | ТЕХНИЧЕСКИЙ АНАЛИЗ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ИНСТРУМЕНТ:</b> <code>{symbol}</code> | <b>TF:</b> <code>{timeframe}</code>\n"
        f"<b>ТЕКУЩАЯ ЦЕНА:</b> <code>{price_str}</code>\n\n"
        f"🧭 <b>ТРЕНДОВЫЙ КОМПЛЕКС {trend_emoji}</b>\n"
        f"┌ <b>Тренд:</b> <code>{result.trend}</code>\n"
        f"├ <b>EMA 21:</b> <code>{format_price(result.ema_21, symbol)}</code>\n"
        f"├ <b>EMA 50:</b> <code>{format_price(result.ema_50, symbol)}</code>\n"
        f"└ <b>EMA 200:</b> <code>{format_price(result.ema_200, symbol)}</code>\n\n"
        f"⚡ <b>МОМЕНТУМ &amp; ОСЦИЛЛЯТОРЫ {rsi_emoji}</b>\n"
        f"┌ <b>RSI (14):</b> <code>{result.rsi:.2f}</code> ({result.rsi_state})\n"
        f"└ <b>StochRSI K/D:</b> <code>{result.stoch_rsi_k:.1f} / {result.stoch_rsi_d:.1f}</code>\n\n"
        f"🌪 <b>ВОЛАТИЛЬНОСТЬ &amp; ОБЪЕМЫ {vol_emoji}</b>\n"
        f"┌ <b>ATR (14):</b> <code>{format_price(result.atr, symbol)}</code> ({result.atr_percent:.2f}%)\n"
        f"├ <b>Позиция BB:</b> <code>{result.bb_position}</code>\n"
        f"├ <b>Объем:</b> <code>{result.volume_state} (x{result.volume_ratio:.2f})</code>\n"
        f"└ <b>Позиция к VWAP:</b> <code>{result.price_vs_vwap}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Wall Street Institutional Data Engine</i>"
    )


# ── 2. Одиночная стратегия ───────────────────────────────────

def format_strategy(result: StrategyResult) -> str:
    """Форматирует вывод отдельной торговой стратегии."""
    details = f"\n<pre>{escape_html(result.details_text)}</pre>" if result.details_text else ""
    return (
        f"🏛 <b>МОДЕЛЬ: {result.name.upper()}</b> {result.emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Вердикт:</b> {escape_html(result.summary)}\n"
        f"{details}"
    )


# ── 3. Мульти-ТФ Анализ (/analyze) ──────────────────────────

def format_multi_tf_analysis(result: MultiTFResult) -> str:
    """Форматирует глубокий мульти-таймфрейм анализ в стиле Bloomberg."""
    if not result.tf_analyses:
        return f"⚠️ <b>TERMINAL:</b> Нет данных для анализа <code>{result.symbol}</code>."
        
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    stars = "★" * result.overall_stars + "☆" * (5 - result.overall_stars) if result.overall_stars > 0 else "—"
    tag = result.tag_emoji or "🔥"
    order_type_clean = result.order_type.replace('_', ' ')
    
    text = ""
    if DataFetcher.is_weekend():
        text += "⚠️ <i>Рынки Forex и металлов закрыты на выходные. Данные закрытия пятницы.</i>\n\n"
        
    text += (
        f"🏛 <b>WALL STREET TERMINAL | SMC REPORT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ASSET:</b> <code>{result.symbol}</code> [ Маркер: {tag} ]\n"
        f"<b>ИНСТИТУЦИОНАЛЬНЫЙ ВЕРДИКТ:</b> {overall_emoji} <b>{result.overall_direction}</b>\n"
        f"<b>СИЛА СИГНАЛА:</b> <code>{stars}</code> ({result.overall_stars}/5)\n\n"
        f"⏱ <b>СТРУКТУРА МУЛЬТИ-ТАЙМФРЕЙМОВ:</b>\n"
    )
    
    for tf_res in result.tf_analyses:
        tf_dir_em = direction_emoji.get(tf_res.direction, "⚪")
        tf_stars = "★" * tf_res.confidence if tf_res.confidence > 0 else "—"
        text += f"│ <b>{tf_res.timeframe:4}</b> ── {tf_dir_em} <code>{tf_res.direction:7}</code> [{tf_stars}]\n"
        
    text += f"└ <b>Консенсус:</b> <code>{result.tf_agreement}/{result.total_tfs} TF</code> подтверждают вход\n\n"
    
    if result.overall_direction != "NEUTRAL":
        text += (
            f"┌── <b>ПАРАМЕТРЫ СДЕЛКИ</b> ─────────────────\n"
            f"│ 📥 <b>ТИП ОРДЕРА:</b>  <b>{order_type_clean}</b>\n"
        )
        if result.current_price:
            text += f"│ 💵 <b>MARKET:</b>      <code>{format_price(result.current_price, result.symbol)}</code>\n"
        if result.entry:
            text += f"│ 📍 <b>ENTRY:</b>       <code>{format_price(result.entry, result.symbol)}</code>\n"
        if result.stop_loss:
            pips_s = f" (-{result.pips_sl:.1f} п.)" if result.pips_sl else ""
            text += f"│ 🛑 <b>STOP LOSS:</b>   <code>{format_price(result.stop_loss, result.symbol)}</code>{pips_s}\n"
        if result.take_profit_1:
            pips_t1 = f" (+{result.pips_tp1:.1f} п.)" if result.pips_tp1 else ""
            rr1_s = f" [1:{result.risk_reward_1:.1f}]" if result.risk_reward_1 else ""
            text += f"│ 🎯 <b>TARGET 1:</b>    <code>{format_price(result.take_profit_1, result.symbol)}</code>{pips_t1}{rr1_s}\n"
        if result.take_profit_2:
            pips_t2 = f" (+{result.pips_tp2:.1f} п.)" if result.pips_tp2 else ""
            rr2_s = f" [1:{result.risk_reward_2:.1f}]" if result.risk_reward_2 else ""
            text += f"│ 🎯 <b>TARGET 2:</b>    <code>{format_price(result.take_profit_2, result.symbol)}</code>{pips_t2}{rr2_s}\n"
            
        text += f"└──────────────────────────────────────\n\n"
    else:
        text += "⚪ <i>Институциональный сетап отсутствует (R:R &lt; 1:2.5). Ожидаем формирования OTE.</i>\n\n"
        
    if result.session_text:
        text += f"{result.session_text}\n"
        
    text += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Smart Money Management: Риск 1.0% депозита.</i>"
    )
    return text


format_full_analysis = format_multi_tf_analysis


# ── 4. ЭТАП 1: Выход Сигнала ─────────────────────────────────

def format_notification(result: MultiTFResult) -> str:
    """
    ЭТАП 1: Выход сигнала (Bloomberg Terminal Style).
    """
    direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
    overall_emoji = direction_emoji.get(result.overall_direction, "⚪")
    tag = result.tag_emoji or "🔥"
    order_type_clean = result.order_type.replace('_', ' ')
    
    tf_summary = " | ".join([
        f"<b>{t.timeframe}</b> {direction_emoji.get(t.direction, '⚪')}"
        for t in result.tf_analyses if t.direction != 'NEUTRAL'
    ])
    
    pips_sl_s = f" (-{result.pips_sl:.1f} п.)" if result.pips_sl else ""
    pips_tp1_s = f" (+{result.pips_tp1:.1f} п.)" if result.pips_tp1 else ""
    pips_tp2_s = f" (+{result.pips_tp2:.1f} п.)" if result.pips_tp2 else ""
    rr1_s = f" [1:{result.risk_reward_1:.1f}]" if result.risk_reward_1 else ""
    rr2_s = f" [1:{result.risk_reward_2:.1f}]" if result.risk_reward_2 else ""
    
    action_hint = (
        "🚀 <b>ВХОД ПРЯМО СЕЙЧАС ПО РЫНКУ!</b>"
        if "MARKET" in result.order_type
        else f"⏳ <i>Установите отложенный ордер {order_type_clean} в терминале.</i>"
    )

    return (
        f"🏛 <b>SMART TERMINAL</b> | <b>{order_type_clean}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>SYMBOL:</b> <code>{result.symbol}</code> [ Маркер: {tag} ]\n"
        f"<b>ACTION:</b> {overall_emoji} <b>{order_type_clean}</b>\n"
        f"<b>MARKET:</b> <code>{format_price(result.current_price, result.symbol)}</code>\n\n"
        f"┌── <b>ТОРГОВЫЕ УРОВНИ</b> ─────────────────\n"
        f"│ 📍 <b>ENTRY:</b>  <code>{format_price(result.entry, result.symbol)}</code>\n"
        f"│ 🛑 <b>STOP:</b>   <code>{format_price(result.stop_loss, result.symbol)}</code>{pips_sl_s}\n"
        f"│ 🎯 <b>TP 1:</b>   <code>{format_price(result.take_profit_1, result.symbol)}</code>{pips_tp1_s}{rr1_s}\n"
        f"│ 🎯 <b>TP 2:</b>   <code>{format_price(result.take_profit_2, result.symbol)}</code>{pips_tp2_s}{rr2_s}\n"
        f"└── <b>R:R:</b>    <code>1:{result.risk_reward_1:.1f} / 1:{result.risk_reward_2:.1f}</code> ────────\n\n"
        f"⏱ <b>СТРУКТУРА ТФ:</b> {tf_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action_hint}\n"
        f"💼 <i>Рекомендуемый риск: 1.0% депозита.</i>"
    )


# ── 5. ЭТАП 2: Активация Входа ───────────────────────────────

def format_order_activated(signal: dict) -> str:
    """
    ЭТАП 2: Активация входа (Bloomberg Terminal Style).
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

    return (
        f"⚡ <b>TERMINAL ALERT: ОРДЕР АКТИВИРОВАН</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>MARKER:</b> [ {tag} ]\n"
        f"<b>ASSET:</b>  <code>{symbol}</code> | {dir_emoji} <b>{order_type}</b>\n"
        f"<b>TOUCH:</b>  <code>{entry}</code> (Цена вошла в зону OTE)\n\n"
        f"┌── <b>ПОЗИЦИЯ В РЫНКЕ</b> ─────────────────\n"
        f"│ 🛑 <b>STOP LOSS:</b> <code>{sl}</code>\n"
        f"│ 🎯 <b>TARGET 1:</b>  <code>{tp1}</code>\n"
        f"│ 🎯 <b>TARGET 2:</b>  <code>{tp2}</code>\n"
        f"└──────────────────────────────────────\n"
        f"👀 <i>Позиция сопровождается терминалом 24/7.</i>"
    )


# ── 6. ЭТАП 3: Результат Сделки (TP / SL / EXP) ─────────────

def format_signal_result(signal: dict, status: str, close_price: float, pnl_pips: float) -> str:
    """
    ЭТАП 3: Результат сделки (Bloomberg Terminal Style).
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
        target_name = "TARGET 1" if status == 'TP1_HIT' else "TARGET 2"
        return (
            f"🎉 <b>TERMINAL REPORT: TAKE PROFIT</b> 🎯\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>MARKER:</b> [ {tag} ]\n"
            f"<b>ASSET:</b>  <code>{symbol}</code> | {dir_emoji} <b>{order_type}</b>\n"
            f"<b>STATUS:</b> 🏆 <b>{target_name} REACHED</b>\n\n"
            f"┌── <b>ФИНАНСОВЫЙ РЕЗУЛЬТАТ</b> ───────────\n"
            f"│ 📍 <b>ENTRY:</b>  <code>{entry}</code>\n"
            f"│ 🎯 <b>EXIT:</b>   <code>{close_str}</code>\n"
            f"│ 💰 <b>PNL:</b>    <code>+{abs(pnl_pips):.1f} pips</code>\n"
            f"│ 📐 <b>R:R:</b>    <code>1:{rr:.1f} ✅</code>\n"
            f"└──────────────────────────────────────\n"
            f"💸 <i>Зафиксируйте прибыль или переведите стоп в безубыток.</i>"
        )
    elif status == 'SL_HIT':
        return (
            f"🛑 <b>TERMINAL REPORT: STOP LOSS</b> ❌\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>MARKER:</b> [ {tag} ]\n"
            f"<b>ASSET:</b>  <code>{symbol}</code> | {dir_emoji} <b>{order_type}</b>\n"
            f"<b>STATUS:</b> ⚠️ <b>STOP LOSS EXECUTED</b>\n\n"
            f"┌── <b>ФИКСАЦИЯ УБЫТКА</b> ─────────────────\n"
            f"│ 📍 <b>ENTRY:</b>  <code>{entry}</code>\n"
            f"│ 🛑 <b>EXIT:</b>   <code>{close_str}</code>\n"
            f"│ 📉 <b>PNL:</b>    <code>-{abs(pnl_pips):.1f} pips</code>\n"
            f"└──────────────────────────────────────\n"
            f"💼 <i>Убыток строго ограничен 1.0% депозита. Дисциплина сохраняет капитал.</i>"
        )
    else:  # EXPIRED / CANCELLED
        return (
            f"⏰ <b>TERMINAL REPORT: ORDER EXPIRED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>MARKER:</b> [ {tag} ]\n"
            f"<b>ASSET:</b>  <code>{symbol}</code> | {dir_emoji} <b>{order_type}</b>\n"
            f"<b>STATUS:</b> ⏳ <b>ТАЙМАУТ ВХОДА (24H)</b>\n\n"
            f"┌──────────────────────────────────────\n"
            f"│ Цена не дошла до зоны OTE за 24 часа.\n"
            f"└──────────────────────────────────────\n"
            f"💡 <i>Удалите неактивный отложенный ордер из терминала.</i>"
        )


# ── 7. Сводка сигналов (/signals) ────────────────────────────

def format_signals_summary(results: list[MultiTFResult]) -> str:
    """Сводка активных сигналов в стиле терминального радара."""
    strong = [r for r in results if r.overall_stars >= 4 and r.overall_direction != 'NEUTRAL']
    
    if not strong:
        return (
            "🏛 <b>WALL STREET TERMINAL | СИГНАЛЬНЫЙ РАДАР</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚪ <i>В данный момент нет активных институциональных сетапов (R:R &ge; 1:2.5).\n"
            "Сканер непрерывно отслеживает 17 инструментов.</i>"
        )
        
    text = (
        "🏛 <b>WALL STREET TERMINAL | СИГНАЛЬНЫЙ РАДАР</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for r in strong:
        emoji = "🟢" if r.overall_direction == "LONG" else "🔴"
        tag = r.tag_emoji or "🔥"
        entry_s = format_price(r.entry, r.symbol)
        order_s = r.order_type.replace('_', ' ')
        text += f"┌ <b>{r.symbol}</b> [ {tag} ] ── {emoji} <b>{order_s}</b>\n"
        text += f"└ <b>Вход:</b> <code>{entry_s}</code> | <b>R:R:</b> <code>1:{r.risk_reward_1:.1f}</code> | ★ <code>{r.overall_stars}/5</code>\n\n"
        
    text += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Для детального разбора используйте /analyze [Символ]</i>"
    )
    return text


# ── 8. Экономические новости (/news) ─────────────────────────

def format_news_alert(event: EconomicEvent) -> str:
    """Оповещение о важных макроэкономических новостях."""
    impact_emoji = "🔴" if event.impact.lower() == "high" else ("🟠" if event.impact.lower() == "medium" else "🟡")
    
    affected = f"<code>{', '.join(event.affected_pairs)}</code>" if event.affected_pairs else "Все мажоры"
    
    return (
        f"⚠️ <b>MACRO ALERT: ВАЖНАЯ НОВОСТЬ ЧЕРЕЗ {event.minutes_until} МИН</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📰 <b>СОБЫТИЕ:</b> <b>{escape_html(event.title)}</b>\n"
        f"🕐 <b>ВРЕМЯ:</b>    <code>{event.time_str} (UTC+5)</code>\n"
        f"🏳️ <b>СТРАНА:</b>   <code>{event.country}</code>\n"
        f"💥 <b>ИМПАКТ:</b>   {impact_emoji} <b>{event.impact.upper()}</b>\n\n"
        f"<b>ЗАТРОНУТЫЕ ПАРЫ:</b> {affected}\n\n"
        f"┌── <b>ПРАВИЛА ИНСТИТУЦИОНАЛЬНОГО РИСКА</b> ─\n"
        f"│ • Не открывать новые сделки за 30 мин до релиза\n"
        f"│ • Защитить открытые позиции безубытком\n"
        f"│ • Ожидать импульсный всплеск волатильности\n"
        f"└──────────────────────────────────────"
    )


# ── 9. Статистика Win-Rate (/stats) ──────────────────────────

def format_stats(stats: dict) -> str:
    """Форматирует статистику портфеля в стиле отчета фонда."""
    if not stats or stats.get("total", 0) == 0:
        return (
            "📊 <b>BLOOMBERG TERMINAL | PERFORMANCE STATS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>База данных формируется. Ожидайте первых закрытых сетапов.</i>"
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
    wr_bar_filled = int(win_rate // 10)
    wr_bar = "■" * wr_bar_filled + "□" * (10 - wr_bar_filled)

    text = (
        f"📊 <b>WALL STREET TERMINAL | PERFORMANCE STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┌── <b>ОБЩИЙ ПОРТФЕЛЬ</b> ───────────────────\n"
        f"│ 📈 <b>Всего сетапов:</b>   <code>{total}</code>\n"
        f"│ 🔵 <b>В работе:</b>       <code>{open_cnt}</code>\n"
        f"│ ✅ <b>Закрыто:</b>        <code>{closed}</code>\n"
        f"└──────────────────────────────────────\n\n"
        f"🏆 <b>WIN RATE:</b> <code>{win_rate:.1f}%</code>\n"
        f"<code>[{wr_bar}]</code>\n\n"
        f"┌── <b>ИТОГИ ЗАКРЫТЫХ СДЕЛОК</b> ──────────\n"
        f"│ ✅ <b>Take Profit (TP):</b> <code>{wins}</code>\n"
        f"│ ❌ <b>Stop Loss (SL):</b>   <code>{losses}</code>\n"
        f"│ ⏰ <b>Истекло (24h):</b>    <code>{expired}</code>\n"
        f"├──────────────────────────────────────\n"
        f"│ 💰 <b>Чистый PnL:</b>       <code>{pips_sign}{total_pips:.1f} pips</code>\n"
        f"│ 📐 <b>Средний R:R:</b>     <code>1:{avg_rr:.1f}</code>\n"
        f"└──────────────────────────────────────\n"
    )

    by_dir = stats.get("by_direction", {})
    if by_dir:
        text += f"\n📊 <b>ПО НАПРАВЛЕНИЯМ:</b>\n"
        for d, data in by_dir.items():
            d_em = "🟢" if d == "LONG" else "🔴"
            t = data.get("total", 0)
            w = data.get("wins", 0)
            wr = (w / t * 100) if t > 0 else 0.0
            text += f"│ {d_em} <b>{d:5}</b> ── <code>{t:2} сделок</code> ({wr:.0f}% win)\n"

    by_sym = stats.get("by_symbol", {})
    if by_sym:
        text += f"\n🏅 <b>ТОП АКТИВОВ:</b>\n"
        for sym, data in by_sym.items():
            t = data.get("total", 0)
            w = data.get("wins", 0)
            wr = (w / t * 100) if t > 0 else 0.0
            text += f"│ <b>{sym:6}</b> ── <code>{t:2} сделок</code> ({wr:.0f}% win)\n"

    text += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Дисциплина и институциональный риск-менеджмент.</i>"
    )
    return text


# ── 10. Журнал сделок (/history) ────────────────────────────

def format_history(signals: list[dict]) -> str:
    """Форматирует журнал последних сделок."""
    if not signals:
        return (
            "📜 <b>TERMINAL | ЖУРНАЛ СДЕЛОК</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Журнал пуст. Ожидайте генерации институциональных сигналов.</i>"
        )

    text = (
        f"📜 <b>TERMINAL | ЖУРНАЛ ПОСЛЕДНИХ {len(signals)} СДЕЛОК</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
            res_text = f"<code>TP HIT {pnl_sign}{pnl:.1f}p</code>"
        elif status == "SL_HIT":
            st_icon = "❌"
            res_text = f"<code>SL HIT {pnl:.1f}p</code>"
        elif status == "ACTIVE":
            st_icon = "🚀"
            res_text = "<b>В РЫНКЕ</b>"
        elif status == "PENDING":
            st_icon = "⏳"
            res_text = "<i>ОЖИДАЕТ ВХОДА</i>"
        else:
            st_icon = "⏰"
            res_text = "<i>ИСТЁК (24H)</i>"

        d_em = "🟢" if direction == "LONG" else "🔴"
        entry_s = format_price(s.get('entry_price'), symbol)
        text += f"{st_icon} [ {tag} ] <b>{symbol}</b> {d_em} <code>{order_type} @ {entry_s}</code> ── {res_text}\n"

    text += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Wall Street Trading Journal</i>"
    )
    return text


# ── 11. Приветствие и Справка ────────────────────────────────

def format_welcome() -> str:
    """Приветственное сообщение терминала."""
    return (
        "👋 <b>ДОБРО ПОЖАЛОВАТЬ В SMART TRADER TERMINAL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏛 <b>Институциональный ИИ-аналитический комплекс ICT / SMC.</b>\n\n"
        "📊 <b>17 активов Forex &amp; Gold в режиме реального времени.</b>\n"
        "🎯 <b>Снайперские точки входа OTE с Risk/Reward &ge; 1:2.5.</b>\n"
        "⚡ <b>3-этапное ведение сделки с персональными маркерами.</b>\n\n"
        "<i>Используйте меню ниже для навигации по терминалу 👇</i>"
    )


def format_help() -> str:
    """Справка по терминалу."""
    return (
        "📖 <b>СПРАВОЧНИК SMART TRADER TERMINAL (PRO)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>ОСНОВНЫЕ КОМАНДЫ:</b>\n"
        "├ <code>/analyze [Пара]</code> ── Полный разбор структуры ICT/SMC\n"
        "├ <code>/signals</code> ────────── Радар текущих институциональных сетапов\n"
        "├ <code>/stats</code> ──────────── Финансовая статистика и Win-Rate\n"
        "├ <code>/history</code> ────────── Журнал последних позиций\n"
        "├ <code>/sessions</code> ───────── Торговые сессии (Лондон/Нью-Йорк)\n"
        "├ <code>/news</code> ───────────── Экономический макро-календарь\n"
        "└ <code>/request</code> ────────── Запрос на полный доступ\n\n"
        "🏛 <b>ИНСТИТУЦИОНАЛЬНАЯ МЕТОДОЛОГИЯ:</b>\n"
        "• Анализ снятия ликвидности и слома структуры (BOS / CHoCH).\n"
        "• Отложенные ордера строго в зоне OTE (0.618 - 0.705) и Order Block.\n"
        "• Фильтр R:R &ge; 1:2.5 (цели 1:3 - 1:5).\n"
        "• 3-этапная модель: <code>Сигнал ➡️ Активация ➡️ Результат</code>."
    )
