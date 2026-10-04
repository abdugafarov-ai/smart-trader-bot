"""
Smart Trader Bot — Formatters.
Стиль: 🏛 «Wall Street / Bloomberg Terminal» (Институциональный / Премиальный финансовый терминал).
Используется HTML форматирование с моноширинными блоками для идеального выравнивания котировок.
"""

import html
from datetime import datetime, timezone
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

def format_notification(result: MultiTFResult, is_admin: bool = False) -> str:
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
    
    rr1_val = result.risk_reward_1 if result.risk_reward_1 is not None else 0.0
    rr2_val = result.risk_reward_2 if result.risk_reward_2 is not None else 0.0

    # ── ИНСТИТУЦИОНАЛЬНЫЙ ЧЕК-ЛИСТ ICT/SMC (7/7) ──
    poi_name = "FVG / Discount"
    struct_detail = "MSS / CHoCH"
    liq_detail = "Session / Swing Liquidity"
    
    if result.tf_analyses:
        for t in result.tf_analyses:
            if t.direction == result.overall_direction:
                for s in t.strategies:
                    if s.signal and s.signal.details:
                        for d in s.signal.details:
                            if "FVG" in d:
                                poi_name = "Fair Value Gap (FVG)"
                            elif "Order Block" in d:
                                poi_name = "Order Block (OB)"
                            elif "OTE" in d:
                                poi_name = "OTE (0.618-0.786)"
                            if "CHoCH" in d:
                                struct_detail = "CHoCH (Смена характера)"
                            elif "BOS" in d:
                                struct_detail = "BOS (Пробой структуры)"
                            if "Kill Zone" in d:
                                liq_detail = "Kill Zone Sweep"
                            elif "Снятие" in d or "Sweep" in d:
                                liq_detail = "Liquidity Pool Sweep"

    checklist_text = (
        f"🛡 <b>ИНСТИТУЦИОНАЛЬНЫЙ ЧЕК-ЛИСТ (7/7):</b>\n"
        f"├ 1. <b>HTF Тренд:</b> ✅ Подтверждён ({result.tf_agreement}/{result.total_tfs} TF)\n"
        f"├ 2. <b>Ликвидность:</b> ✅ {liq_detail}\n"
        f"├ 3. <b>Слом структуры:</b> ✅ {struct_detail}\n"
        f"├ 4. <b>Импульс:</b> ✅ Displacement (Тело свечи > ATR)\n"
        f"├ 5. <b>Точка входа (POI):</b> ✅ Ретест {poi_name}\n"
        f"├ 6. <b>Риск/Прибыль:</b> ✅ 1:{rr1_val:.1f} (Фильтр ≥ 1:1.8 пройден)\n"
        f"└ 7. <b>Макро/Сессия:</b> ✅ Активная зона (Без красных новостей)"
    )

    header = "🏛 <b>SMART TERMINAL | СИСТЕМНЫЙ СИГНАЛ MT5</b>" if is_admin else "🎯 <b>СИГНАЛ НА ВХОД В РЫНОК</b>"
    if is_admin:
        action_note = (
            f"⚡ <b>Авто-исполнение:</b> {'Лимитный ордер передан в ваш советник MT5.' if 'LIMIT' in order_type_clean.upper() else 'Рыночный ордер передан в советник MT5.'}\n"
            f"💼 <i>Управление рисками контролируется сервером.</i>"
        )
    else:
        action_note = (
            f"📋 <b>Инструкция:</b> Откройте <b>{order_type_clean}</b> в своём терминале (MT4/MT5/TradingView) по указанной цене входа со стоп-лоссом.\n"
            f"💼 <i>Рекомендуемый риск: 1.0% депозита. Не завышайте объём!</i>"
        )

    return (
        f"{header} | <b>{order_type_clean}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>SYMBOL:</b> <code>{result.symbol}</code> [ Маркер: {tag} ]\n"
        f"<b>ACTION:</b> {overall_emoji} <b>{order_type_clean}</b>\n"
        f"<b>MARKET:</b> <code>{format_price(result.current_price, result.symbol)}</code>\n\n"
        f"┌── <b>ТОРГОВЫЕ УРОВНИ</b> ─────────────────\n"
        f"│ 📍 <b>ENTRY:</b>  <code>{format_price(result.entry, result.symbol)}</code>\n"
        f"│ 🛑 <b>STOP:</b>   <code>{format_price(result.stop_loss, result.symbol)}</code>{pips_sl_s}\n"
        f"│ 🎯 <b>TP 1:</b>   <code>{format_price(result.take_profit_1, result.symbol)}</code>{pips_tp1_s}{rr1_s}\n"
        f"│ 🎯 <b>TP 2:</b>   <code>{format_price(result.take_profit_2, result.symbol)}</code>{pips_tp2_s}{rr2_s}\n"
        f"└── <b>R:R:</b>    <code>1:{rr1_val:.1f} / 1:{rr2_val:.1f}</code> ────────\n\n"
        f"⏱ <b>СТРУКТУРА ТФ:</b> {tf_summary}\n\n"
        f"{checklist_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action_note}"
    )


# ── 5. ЭТАП 2: Активация Входа ───────────────────────────────

def format_order_activated(signal: dict, is_admin: bool = False) -> str:
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

    if is_admin:
        header = "⚡ <b>TERMINAL ALERT: ОРДЕР АКТИВИРОВАН В MT5</b>"
        note = "💼 <i>Сделка открыта роботом в терминале MetaTrader 5 и сопровождается 24/7.</i>"
    else:
        header = "⚡ <b>СИГНАЛ: ВХОД АКТИВИРОВАН (ЦЕНА В ЗОНЕ ENTRY)</b>"
        note = "👀 <i>Цена вошла в зону входа. Если ордер выставлен, позиция в рынке. Сопровождайте сделку до TP 1 и TP 2.</i>"

    return (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>MARKER:</b> [ {tag} ]\n"
        f"<b>ASSET:</b>  <code>{symbol}</code> | {dir_emoji} <b>{order_type}</b>\n"
        f"<b>TOUCH:</b>  <code>{entry}</code> (Цена вошла в зону OTE)\n\n"
        f"┌── <b>ПОЗИЦИЯ В РЫНКЕ</b> ─────────────────\n"
        f"│ 🛑 <b>STOP LOSS:</b> <code>{sl}</code>\n"
        f"│ 🎯 <b>TARGET 1:</b>  <code>{tp1}</code>\n"
        f"│ 🎯 <b>TARGET 2:</b>  <code>{tp2}</code>\n"
        f"└──────────────────────────────────────\n"
        f"{note}"
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
            f"└──────────────────────────────────────\n\n"
            f"🛑 <b>РАЗБОР СТОПА:</b>\n"
            f"• Импульсный пробой уровня / снятие ликвидности рынком.\n"
            f"• Риск строго ограничен 1.0% депозита. Капитал защищен.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
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


def format_manual_open(symbol: str, order_type: str, volume: float, price: float) -> str:
    """Уведомление о ручном открытии сделки пользователем в MT5."""
    clean_type = order_type.upper()
    emoji = "🟢" if "BUY" in clean_type else "🔴"
    price_str = format_price(price, symbol)
    return (
        f"👑 <b>Мой повелитель открыл сделку</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Пара: <b>{symbol}</b> ({emoji} <b>{clean_type}</b>)\n"
        f"• Объем: <code>{volume:.2f} лот</code>\n"
        f"• Цена входа: <code>{price_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <i>Позиция зафиксирована терминалом и взята на контроль.</i>"
    )


def format_manual_close(symbol: str, profit: float, pnl_pips: float, price: float) -> str:
    """Уведомление о досрочном ручном закрытии сделки пользователем в MT5."""
    profit_sign = "+" if profit >= 0 else ""
    pips_sign = "+" if pnl_pips >= 0 else ""
    price_str = format_price(price, symbol)
    profit_emoji = "💵" if profit > 0 else ("🛡" if profit == 0 else "📉")
    return (
        f"👑 <b>Мой повелитель решил закрыть сделку</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Пара: <b>{symbol}</b>\n"
        f"• Итог: {profit_emoji} <b>{profit_sign}{profit:.2f} USD</b> ({pips_sign}{pnl_pips:.1f} pips)\n"
        f"• Цена закрытия: <code>{price_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Сделка закрыта вручную до достижения TP/SL.</i>"
    )


# ── 7. Сводка сигналов (/signals) ────────────────────────────

def format_signals_summary(results: list[MultiTFResult]) -> str:
    """Сводка активных сигналов в стиле терминального радара."""
    strong = [r for r in results if r.overall_stars >= 4 and r.overall_direction != 'NEUTRAL']
    
    if not strong:
        return (
            "🏛 <b>WALL STREET TERMINAL | СИГНАЛЬНЫЙ РАДАР</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚪ <i>В данный момент нет активных институциональных сетапов (R:R >= 1:2.5).\n"
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
    """Форматирует 100% честную статистику эффективности по реальным сделкам брокера MT5."""
    if not stats or (stats.get("total", 0) == 0 and stats.get("open", 0) == 0 and stats.get("balance", 0.0) == 0):
        return (
            "📊 <b>METATRADER 5 | БРОКЕРСКАЯ СТАТИСТИКА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Журнал сделок пуст. Ожидайте первой активности робота.</i>"
        )

    total = stats.get("total", 0)
    open_cnt = stats.get("open", 0)
    closed = stats.get("closed", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    breakevens = stats.get("breakevens", 0)
    expired = stats.get("expired", 0)
    win_rate = stats.get("win_rate", 0.0)
    total_profit_usd = stats.get("total_profit_usd", 0.0)
    avg_rr = stats.get("avg_rr", 2.1)

    profit_sign = "+" if total_profit_usd >= 0 else ""
    wr_bar_filled = int(win_rate // 10)
    wr_bar = "■" * wr_bar_filled + "□" * (10 - wr_bar_filled)

    broker = stats.get("broker", "MetaTrader 5")
    account = stats.get("account", "—")
    balance = stats.get("balance", 0.0)
    equity = stats.get("equity", 0.0)
    mt5_online = stats.get("mt5_online", False)
    status_dot = "🟢" if mt5_online else "🟡"

    text = (
        f"📊 <b>METATRADER 5 | БРОКЕРСКАЯ СТАТИСТИКА</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_dot} <b>Терминал:</b> <code>{broker} ({account})</code>\n"
    )

    if balance > 0 or equity > 0:
        text += (
            f"💼 <b>Баланс:</b> <code>${balance:.2f}</code> | <b>Equity:</b> <code>${equity:.2f}</code>\n"
        )

    expectancy = stats.get("expectancy", 0.0)
    pf = stats.get("profit_factor", 0.0)
    exp_sign = "+" if expectancy >= 0 else ""

    text += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┌── <b>ПОРТФЕЛЬ РОБОТА</b> ───────────────────\n"
        f"│ 📈 <b>Всего сделок:</b>   <code>{total}</code>\n"
        f"│ 🔵 <b>В рынке:</b>       <code>{open_cnt}</code>\n"
        f"│ ✅ <b>Закрыто:</b>        <code>{closed}</code>\n"
        f"└──────────────────────────────────────\n\n"
        f"🏆 <b>WIN RATE:</b> <code>{win_rate:.1f}%</code>\n"
        f"<code>[{wr_bar}]</code>\n\n"
        f"┌── <b>РЕАЛЬНЫЕ РЕЗУЛЬТАТЫ (MT5)</b> ─────────\n"
        f"│ ✅ <b>Победы (TP):</b>     <code>{wins}</code>\n"
        f"│ ❌ <b>Убытки (SL):</b>     <code>{losses}</code>\n"
        f"│ 🛡 <b>Безубыток (BE):</b>   <code>{breakevens}</code>\n"
        f"├──────────────────────────────────────\n"
        f"│ 💵 <b>ЧИСТЫЙ PnL:</b>     <b>{profit_sign}{total_profit_usd:.2f} USD</b>\n"
        f"│ 📐 <b>Средний R:R:</b>     <code>1:{avg_rr:.1f}</code>\n"
        f"│ 📊 <b>Profit Factor:</b>   <code>{pf:.2f}</code>\n"
        f"│ 🎯 <b>Expectancy:</b>      <b>{exp_sign}{expectancy:.2f} USD</b>/сделка\n"
        f"└──────────────────────────────────────\n"
    )

    # Открытые позиции в рынке
    open_pos = stats.get("open_positions") or []
    if open_pos:
        text += f"\n🚀 <b>АКТИВНЫЕ ПОЗИЦИИ В РЫНКЕ:</b>\n"
        for p in open_pos:
            sym = p.get("symbol", "")
            p_type = p.get("type", "BUY")
            p_lot = p.get("lot", 0.01)
            p_price = p.get("price", 0.0)
            p_pnl = p.get("profit", 0.0)
            p_sign = "+" if p_pnl >= 0 else ""
            d_em = "🟢" if "BUY" in str(p_type).upper() else "🔴"
            text += f"│ {d_em} <b>{sym}</b> {p_type} <code>{p_lot} @ {p_price:.5f}</code> ── <b>{p_sign}{p_pnl:.2f} USD</b>\n"

    # Отложенные ордера
    pend_ord = stats.get("pending_orders") or []
    if pend_ord:
        text += f"\n⏳ <b>ОТЛОЖЕННЫЕ ЛИМИТНЫЕ ОРДЕРА:</b>\n"
        for o in pend_ord:
            sym = o.get("symbol", "")
            o_type = o.get("type", "LIMIT")
            o_lot = o.get("lot", 0.01)
            o_price = o.get("price", 0.0)
            d_em = "🟢" if "BUY" in str(o_type).upper() else "🔴"
            text += f"│ {d_em} <b>{sym}</b> <code>{o_type} {o_lot} @ {o_price:.5f}</code>\n"

    by_sym = stats.get("by_symbol", {})
    if by_sym:
        text += f"\n🏅 <b>ПРИБЫЛЬ ПО ИНСТРУМЕНТАМ:</b>\n"
        for sym, data in by_sym.items():
            t = data.get("total", 0)
            w = data.get("wins", 0)
            pnl = data.get("profit_usd", 0.0)
            sign = "+" if pnl >= 0 else ""
            text += f"│ <b>{sym:6}</b> ── <code>{t:2} сделок</code> | <b>{sign}{pnl:.2f} USD</b>\n"

    text += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Данные поступают напрямую из брокерского терминала MT5.</i>"
    )
    return text


# ── 10. Журнал сделок (/history) ────────────────────────────

def format_history(signals: list[dict]) -> str:
    """Форматирует журнал последних реальных сделок и ордеров MT5."""
    if not signals:
        return (
            "📜 <b>METATRADER 5 | ЖУРНАЛ СДЕЛОК</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Журнал пуст. Сделки появятся при исполнении ордеров советником.</i>"
        )

    text = (
        f"📜 <b>METATRADER 5 | ЖУРНАЛ ПОСЛЕДНИХ {len(signals)} СДЕЛОК</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for s in signals:
        status = s.get("status", "PENDING")
        symbol = s.get("symbol", "???")
        direction = s.get("direction", "BUY").replace("LONG", "BUY").replace("SHORT", "SELL")
        lot = s.get("lot", 0.01)
        ticket = s.get("ticket") or ""
        ticket_str = f"#{ticket} " if ticket else ""
        entry_p = s.get("entry_price", 0.0)
        pnl = s.get("profit_usd", 0.0)
        pnl_sign = "+" if pnl > 0 else ""

        if status in ("TP1_HIT", "TP2_HIT") or (pnl > 0 and status not in ("ACTIVE", "PENDING")):
            st_icon = "✅"
            res_text = f"<b>{pnl_sign}{pnl:.2f} USD</b> (TP)"
        elif status == "SL_HIT" or (pnl < 0 and status not in ("ACTIVE", "PENDING")):
            st_icon = "🛑"
            res_text = f"<b>{pnl:.2f} USD</b> (SL)"
        elif status == "BREAKEVEN" or (pnl == 0 and status not in ("ACTIVE", "PENDING")):
            st_icon = "🛡"
            res_text = "<b>0.00 USD</b> (BE)"
        elif status == "ACTIVE":
            st_icon = "🚀"
            res_text = f"<b>{pnl_sign}{pnl:.2f} USD</b> (В РЫНКЕ)" if pnl != 0 else "<b>В РЫНКЕ</b>"
        elif status == "PENDING":
            st_icon = "⏳"
            res_text = "<i>ОЖИДАЕТ ВХОДА</i>"
        else:
            st_icon = "⏰"
            res_text = "<i>ИСТЁК</i>"

        d_em = "🟢" if "BUY" in direction else "🔴"
        text += f"{st_icon} <code>{ticket_str}</code><b>{symbol}</b> {d_em} <code>{direction} {lot} @ {entry_p:.5f}</code> ── {res_text}\n"

    text += (
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <i>Официальный журнал брокерских сделок MetaTrader 5.</i>"
    )
    return text


# ── 11. Приветствие и Справка ────────────────────────────────

def format_welcome(is_admin: bool = False) -> str:
    """Приветственное сообщение терминала с адаптацией под роль пользователя."""
    if is_admin:
        return (
            "🏛 <b>SMART TRADER TERMINAL | ПУЛЬТ АДМИНИСТРАТОРА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🤖 <b>Автономный торговый комплекс с интеграцией MetaTrader 5</b>\n\n"
            "<b>ВАШИ АДМИНИСТРАТИВНЫЕ ВОЗМОЖНОСТИ:</b>\n"
            "• <b>Центральный пульт MT5:</b> Мониторинг баланса, эквити, позиций и кнопка экстренной Паники.\n"
            "• <b>Управление Автопилотом:</b> Настройка лота, процента риска и режима счёта («Микро» / «Институционал»).\n"
            "• <b>CRM & База клиентов:</b> Управление доступом, тарифами, сроками и мгновенное отключение (Kick).\n"
            "• <b>Аналитика & Отчёты:</b> 17 инструментов, Win-Rate, журнал закрытых сделок и макро-календарь.\n\n"
            "<i>Выберите необходимый раздел управления ниже 👇</i>"
        )
    return (
        "🏛 <b>SMART TRADER | ИНСТИТУЦИОНАЛЬНЫЕ СИГНАЛЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 <b>Добро пожаловать в закрытый аналитический терминал!</b>\n\n"
        "Вам предоставлен доступ к эксклюзивным сигналам высшего качества:\n"
        "• <b>17 инструментов:</b> Forex Majors, Crosses и Золото (XAUUSD).\n"
        "• <b>Методология ICT / Smart Money:</b> Анализ ликвидности крупного капитала (OB, FVG, OTE).\n"
        "• <b>Математическое преимущество:</b> Строгий Risk:Reward от 1:2.0 до 1:4.0.\n"
        "• <b>Макроэкономическая защита:</b> Фильтрация перед выходом ключевых новостей.\n"
        "• <b>Прозрачная статистика:</b> Честный учёт результатов по всем закрытым сделкам.\n\n"
        "<i>Используйте кнопки меню ниже для перехода к разделам 👇</i>"
    )


def format_crm_user_card(user: dict, ltv_usd: float = 0.0, payments_cnt: int = 0) -> str:
    """Форматирует детальную карточку пользователя в CRM для администратора с LTV и заметками."""
    uid = user.get("telegram_id")
    first_name = user.get("first_name") or "—"
    username = f"@{user['username']}" if user.get("username") else "отсутствует"
    status = user.get("status") or "pending"
    tariff = user.get("tariff") or "PRO"
    requested_at = (user.get("requested_at") or "—")[:16].replace("T", " ")
    approved_at = (user.get("approved_at") or "—")[:16].replace("T", " ")
    last_seen = (user.get("last_seen") or "—")[:19].replace("T", " ")
    activity_cnt = user.get("activity_count") or 0
    expires_at = user.get("expires_at")
    admin_notes = user.get("admin_notes") or ""
    
    now = datetime.now(timezone.utc)
    if status == "approved":
        is_exp = False
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at)
                is_exp = exp_dt < now
            except Exception:
                pass
        status_text = "⚠️ <b>ИСТЁК</b> (требуется продление)" if is_exp else "🟢 <b>АКТИВЕН</b>"
    elif status == "revoked":
        status_text = "🔴 <b>ОТКЛЮЧЕН (REVOKED)</b>"
    elif status == "expired":
        status_text = "⏳ <b>ПОДПИСКА ИСТЕКЛА</b>"
    elif status == "pending":
        status_text = "⏳ <b>ОЖИДАЕТ ОДОБРЕНИЯ</b>"
    elif status == "rejected":
        status_text = "❌ <b>ОТКЛОНЁН</b>"
    else:
        status_text = f"⚪ <b>{status.upper()}</b>"

    exp_text = "Бессрочно (VIP)"
    days_left_text = "—"
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            days = (exp_dt - now).days
            exp_text = exp_dt.strftime("%d.%m.%Y")
            days_left_text = f"{days} дн." if days >= 0 else "0 дн. (истек)"
        except Exception:
            exp_text = str(expires_at)[:10]

    notes_text = f"<i>«{admin_notes}»</i>" if admin_notes else "<i>(нет заметки — нажмите «📝 Заметка админа»)</i>"

    return (
        "👤 <b>КАРТОЧКА КЛИЕНТА | CRM ПАНЕЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"👤 <b>Имя:</b> <code>{first_name}</code>\n"
        f"📛 <b>Username:</b> {username}\n"
        f"📡 <b>Статус доступа:</b> {status_text}\n"
        f"💎 <b>Тарифный план:</b> <code>{tariff}</code>\n"
        f"📅 <b>Подписка до:</b> <code>{exp_text}</code> (осталось: <b>{days_left_text}</b>)\n\n"
        f"💰 <b>ФИНАНСЫ (LTV):</b> <b>${ltv_usd:.2f}</b> (<code>{payments_cnt}</code> оплат)\n"
        f"📝 <b>ЗАМЕТКА АДМИНА:</b> {notes_text}\n\n"
        "⏱ <b>ИСТОРИЯ РЕГИСТРАЦИИ:</b>\n"
        f"• Подача заявки: <code>{requested_at} UTC</code>\n"
        f"• Дата активации: <code>{approved_at} UTC</code>\n\n"
        "🕵️‍♂️ <b>СКРЫТАЯ АКТИВНОСТЬ:</b>\n"
        f"• Последний визит: <code>{last_seen} UTC</code>\n"
        f"• Всего действий в боте: <b>{activity_cnt}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Используйте кнопки ниже для управления тарифом или отправки сообщения 👇</i>"
    )


def format_my_subscription(user: dict) -> str:
    """Форматирует карточку подписки и официальную тарифную сетку для клиента."""
    tariff = user.get("tariff") or "PRO"
    expires_at = user.get("expires_at")
    is_lifetime = user.get("is_lifetime") or False

    now = datetime.now(timezone.utc)
    status_str = "🟢 Активна"
    days_left_str = "—"
    exp_formatted = "Бессрочно (VIP)" if is_lifetime or not expires_at else "—"

    if not is_lifetime and expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            days = (exp_dt - now).days
            exp_formatted = exp_dt.strftime("%d.%m.%Y")
            if exp_dt < now:
                status_str = "🔴 Истекла"
                days_left_str = "0 дн. (требуется продление)"
            else:
                days_left_str = f"{max(0, days)} дн."
        except Exception:
            exp_formatted = str(expires_at)[:10]

    return (
        "💎 <b>МОЯ ПОДПИСКА И ТАРИФНЫЕ ПЛАНЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 <b>Текущий тариф:</b> <code>{tariff}</code>\n"
        f"📡 <b>Статус доступа:</b> <b>{status_str}</b>\n"
        f"📅 <b>Срок действия:</b> до <code>{exp_formatted}</code>\n"
        f"⏳ <b>Осталось дней:</b> <code>{days_left_str}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💰 <b>ОФИЦИАЛЬНАЯ ТАРИФНАЯ СЕТКА:</b>\n\n"
        "🔹 <b>Тариф «1 МЕСЯЦ»</b> — <code>$50</code>\n"
        "• Мгновенные сигналы SMC / ICT по 17 инструментам\n"
        "• Чёткие уровни Entry, Stop Loss, Take Profit 1 и 2\n"
        "• Контроль Risk:Reward (от 1:2.0) и макроэкономический фильтр\n\n"
        "🔥 <b>Тариф «3 МЕСЯЦА»</b> — <code>$140</code> <i>(выгода $10)</i>\n"
        "• Спокойная торговля на квартал без риска пропустить сигнал\n"
        "• Полная статистика, история сделок и приоритетная поддержка\n\n"
        "👑 <b>Тариф «ГОДОВОЙ (12 МЕСЯЦЕВ)»</b> — <code>$500</code> <i>(выгода $100!)</i>\n"
        "• Самый выгодный выбор для стабильного профита\n"
        "• Доступ ко всем обновлениям алгоритма на 1 год\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💳 <i>Для оплаты или продления тарифа нажмите кнопку ниже 👇</i>"
    )


def format_help(is_admin: bool = False) -> str:
    """Полное руководство пользователя с разделением для Администратора и Клиента."""
    if is_admin:
        return (
            "📖 <b>РУКОВОДСТВО АДМИНИСТРАТОРА SMART TRADER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🤖 <b>О БОТЕ И ПРИНЦИПЕ РАБОТЫ:</b>\n"
            "Smart Trader Bot — это автономный торговый комплекс, синхронизированный "
            "с терминалом MetaTrader 5 на VPS сервере. Робот 24/7 сканирует 17 торговых инструментов "
            "(Forex + Золото), находит точки входа крупного капитала по методологии Smart Money / ICT "
            "и автоматически выставляет ордера со строгим Stop Loss и Take Profit в MT5.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>РАЗДЕЛЫ УПРАВЛЕНИЯ АДМИНИСТРАТОРА:</b>\n\n"
            "🖥 <b>[Мой Терминал MT5]</b> — Баланс, эквити, маржа, позиции и кнопка экстренной Паники.\n"
            "⚙️ <b>[Настройки Автопилота]</b> — Пауза/старт, лот, риск %, режимы «Микро» и «Институционал».\n"
            "👥 <b>[CRM Клиенты]</b> — Управление подписчиками, триалы (3 дня), продление (30, 90, 365 дн., Бессрочно для братьев/друзей), кнопка Kick.\n"
            "📢 <b>[Рассылка /broadcast]</b> — Мгновенные объявления всем активным клиентам.\n"
            "📊 <b>[Статистика & Win-Rate]</b> — Финансовый отчёт по сделкам с брокерского счёта.\n"
            "📜 <b>[Журнал Сделок]</b> — История закрытых сделок MT5.\n"
            "⏰ <b>[Сессии & Календарь]</b> — Торговые сессии и макроэкономика.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    return (
        "📖 <b>РУКОВОДСТВО ТРЕЙДЕРА: КАК РАБОТАТЬ ПО СИГНАЛАМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 <b>1. ПРИНЦИП РАБОТЫ СИГНАЛОВ:</b>\n"
        "Бот ищет институциональные следы крупного капитала (методология ICT / Smart Money Concepts). "
        "Сигналы приходят заранее — до того, как цена дойдет до точки входа, что даёт время спокойно открыть сделку.\n\n"
        "📋 <b>2. ПАРАМЕТРЫ В КАРТОЧКЕ СИГНАЛА:</b>\n"
        "• <b>ENTRY (Вход):</b> Рекомендуемая цена входа. Выставляйте лимитный ордер (Buy Limit / Sell Limit) или входите по рынку при касании уровня.\n"
        "• <b>STOP LOSS (Стоп-лосс):</b> ЖЕЛЕЗНЫЙ уровень отмены сетапа. Всегда сразу выставляйте стоп-лосс в терминале для защиты счёта!\n"
        "• <b>TAKE PROFIT 1:</b> Первый уровень фиксации прибыли (R:R от 1:2.0). Рекомендуется закрыть 50% объёма сделки и перевести стоп в безубыток.\n"
        "• <b>TAKE PROFIT 2:</b> Финальная цель движения крупного капитала (R:R от 1:3.0 до 1:4.0).\n\n"
        "⚖️ <b>3. ПРАВИЛО УПРАВЛЕНИЯ РИСКОМ (MONEY MANAGEMENT):</b>\n"
        "• <b>Никогда не рискуйте более 1–2% депозита на одну сделку!</b>\n"
        "• Рассчитывайте размер лота так, чтобы при срабатывании Stop Loss потеря составляла не более 1–2% вашего депозита.\n"
        "• Не открывайте сделки на весь депозит (Overleveraging).\n\n"
        "📰 <b>4. МАКРОЭКОНОМИЧЕСКИЕ НОВОСТИ:</b>\n"
        "• Во время выхода ключевых новостей США (High Impact) рынок сильно штормит. "
        "Следите за разделом «Макро Календарь» и избегайте входов перед красными новостями.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💬 <i>По всем вопросам и подключению обращайтесь в раздел «Поддержка».</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

