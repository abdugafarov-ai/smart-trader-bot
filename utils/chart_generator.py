"""
Генератор институциональных графиков в стиле TradingView.
Точное соответствие внешнему виду TradingView:
- Свечи занимают левые ~65-70% графика и НЕ доходят до правого края
- Справа в пустом пространстве строится интерактивный Position Tool (Long/Short Box)
- Красная зона риска (SL) и зеленая зона профита (TP) проецируются ВПЕРЕД во времени
- На правой ценовой шкале отображаются четкие цветные плашки: SL, Entry, TP, Market Price
- Текущая цена подсвечена пунктирной линией
- Поддержка темной (TradingView Dark #131722) и светлой тем
"""

import io
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Headless rendering
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

logger = logging.getLogger(__name__)

# ── Цветовые палитры TradingView ─────────────────────────
TV_THEMES = {
    "dark": {
        "bg_color": "#131722",
        "grid_color": "#1e222d",
        "axis_color": "#2a2e39",
        "text_color": "#d1d4dc",
        "subtext_color": "#787b86",
        "up_candle": "#089981",
        "down_candle": "#f23645",
        "sl_box": "#f23645",
        "tp_box": "#089981",
        "entry_line": "#d1d4dc",
        "current_price_line": "#2962ff",
        "watermark": "#2a2e39",
    },
    "light": {
        "bg_color": "#ffffff",
        "grid_color": "#f0f3fa",
        "axis_color": "#e0e3eb",
        "text_color": "#131722",
        "subtext_color": "#787b86",
        "up_candle": "#089981",
        "down_candle": "#f23645",
        "sl_box": "#f23645",
        "tp_box": "#089981",
        "entry_line": "#434651",
        "current_price_line": "#2962ff",
        "watermark": "#f0f3fa",
    }
}


def generate_signal_chart(
    df: pd.DataFrame,
    symbol: str,
    direction: str,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: Optional[float] = None,
    tp3: Optional[float] = None,
    current_price: Optional[float] = None,
    order_type: str = "BUY_LIMIT",
    stars: int = 4,
    theme: str = "dark",
    last_n_candles: int = 45,
    future_padding_bars: int = 18,
    timeframe: str = "1h",
) -> Optional[bytes]:
    """
    Генерирует свечной график в стиле TradingView с визуальным инструментом Long/Short Position.
    Возвращает PNG-изображение в байтах.
    """
    try:
        if df is None or df.empty or len(df) < 10:
            logger.warning("Not enough data to generate chart for %s", symbol)
            return None

        # Берем последние N свечей
        df_chart = df.tail(last_n_candles).copy().reset_index(drop=True)
        n_candles = len(df_chart)

        if current_price is None:
            current_price = float(df_chart['close'].iloc[-1])

        # Выбираем тему
        t = TV_THEMES.get(theme, TV_THEMES["dark"])

        # Форматирование цен и множитель пипсов
        if 'JPY' in symbol:
            p_fmt = "{:.3f}"
            pip_mult = 100.0
        elif 'XAU' in symbol:
            p_fmt = "{:.2f}"
            pip_mult = 10.0
        else:
            p_fmt = "{:.5f}"
            pip_mult = 10000.0

        risk_pips = abs(entry - stop_loss) * pip_mult
        reward_pips = abs(tp1 - entry) * pip_mult
        rr = reward_pips / risk_pips if risk_pips > 0 else 2.5

        # Создаем фигуру TradingView
        fig, ax = plt.subplots(figsize=(13, 6.8), dpi=140)
        fig.patch.set_facecolor(t["bg_color"])
        ax.set_facecolor(t["bg_color"])

        # Настройка сетки
        ax.grid(True, color=t["grid_color"], linestyle='-', linewidth=0.8, alpha=0.7)
        ax.set_axisbelow(True)

        # ── 1. Отрисовка японских свечей ──
        candle_width = 0.58
        wick_width = 1.0

        for i in range(n_candles):
            row = df_chart.iloc[i]
            o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            is_up = c >= o
            c_color = t["up_candle"] if is_up else t["down_candle"]

            # Тень (фитиль)
            ax.plot([i, i], [l, h], color=c_color, linewidth=wick_width, zorder=2)

            # Тело свечи
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.01)

            rect = Rectangle(
                (i - candle_width / 2, body_bottom),
                candle_width, body_height,
                facecolor=c_color,
                edgecolor=c_color,
                linewidth=0.8,
                zorder=3
            )
            ax.add_patch(rect)

        # ── 2. TradingView Position Tool Box (R:R Box) ──
        # Начинается от текущей последней свечи и уходит вправо в пустое будущее пространство
        x_start = n_candles - 0.5
        box_width = future_padding_bars - 2
        x_end = x_start + box_width

        sl_color = t["sl_box"]
        tp_color = t["tp_box"]

        if direction == "LONG":
            # Зеленая зона сверху (Entry -> TP1)
            profit_height = max(0.00001, tp1 - entry)
            rect_tp = Rectangle(
                (x_start, entry), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_tp)

            # Красная зона снизу (SL -> Entry)
            risk_height = max(0.00001, entry - stop_loss)
            rect_sl = Rectangle(
                (x_start, stop_loss), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_sl)

            tp_text_y = entry + profit_height * 0.5
            sl_text_y = stop_loss + risk_height * 0.5

        else:  # SHORT
            # Красная зона сверху (Entry -> SL)
            risk_height = max(0.00001, stop_loss - entry)
            rect_sl = Rectangle(
                (x_start, entry), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_sl)

            # Зеленая зона снизу (TP1 -> Entry)
            profit_height = max(0.00001, entry - tp1)
            rect_tp = Rectangle(
                (x_start, tp1), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_tp)

            tp_text_y = tp1 + profit_height * 0.5
            sl_text_y = entry + risk_height * 0.5

        # Линии уровней внутри бокса позиции
        ax.plot([x_start, x_end], [entry, entry], color='#ffffff', linewidth=1.4, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [stop_loss, stop_loss], color=sl_color, linewidth=1.3, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [tp1, tp1], color=tp_color, linewidth=1.3, linestyle='-', zorder=5)

        # Тонкие пунктирные проекции вправо к шкале цен
        total_x_span = n_candles + future_padding_bars
        ax.plot([x_end, total_x_span], [entry, entry], color='#5d606b', linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)
        ax.plot([x_end, total_x_span], [stop_loss, stop_loss], color=sl_color, linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)
        ax.plot([x_end, total_x_span], [tp1, tp1], color=tp_color, linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)

        # ── Аутентичные синие ручки TradingView [■] (Handle markers) ──
        handles_x = [x_start, x_end, x_start, x_end, x_start, x_end]
        handles_y = [tp1, tp1, stop_loss, stop_loss, entry, entry]
        ax.scatter(handles_x, handles_y, color='#2962ff', s=26, marker='s', edgecolors='#ffffff', linewidth=1.0, zorder=6)

        # ── Информационные плашки TradingView (как на рис. 2) ──
        box_center_x = x_start + box_width / 2
        ax.text(
            box_center_x, entry,
            f"Open PnL: 0.00 ({order_type.replace('_', ' ')})\nRisk/reward ratio: {rr:.2f}",
            color='#ffffff', fontsize=8.2, fontweight='bold', ha='center', va='center', zorder=7,
            bbox=dict(boxstyle='round,pad=0.35', facecolor='#00897b', edgecolor='#ffffff', linewidth=0.8, alpha=0.95)
        )

        ax.text(
            box_center_x, tp1,
            f" Target: +{reward_pips:.1f} pips ",
            color='#ffffff', fontsize=8.0, fontweight='bold', ha='center',
            va='bottom' if direction == "LONG" else 'top', zorder=7,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=tp_color, edgecolor='none', alpha=0.92)
        )
        ax.text(
            box_center_x, stop_loss,
            f" Stop: -{risk_pips:.1f} pips ",
            color='#ffffff', fontsize=8.0, fontweight='bold', ha='center',
            va='top' if direction == "LONG" else 'bottom', zorder=7,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=sl_color, edgecolor='none', alpha=0.92)
        )

        # ── 3. Линия текущей рыночной цены (Market Price) ──
        ax.axhline(current_price, color=t["current_price_line"], linestyle='--', linewidth=1.0, alpha=0.8, zorder=3)

        # ── 4. Границы осей X и Y ──
        total_x_span = n_candles + future_padding_bars
        ax.set_xlim(-1, total_x_span)

        all_y = list(df_chart['low']) + list(df_chart['high']) + [entry, stop_loss, tp1, current_price]
        if tp2:
            all_y.append(tp2)
        y_min, y_max = min(all_y), max(all_y)
        y_padding = (y_max - y_min) * 0.08
        ax.set_ylim(y_min - y_padding, y_max + y_padding)

        # ── 5. Настройка осей и рамок ──
        ax.spines['top'].set_visible(False)
        ax.spines['bottom'].set_color(t["axis_color"])
        ax.spines['left'].set_visible(False)
        ax.spines['right'].set_color(t["axis_color"])

        # Ось цен строго справа (как в TradingView)
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.tick_params(axis='y', colors=t["subtext_color"], labelsize=8.5, length=3)
        ax.tick_params(axis='x', colors=t["subtext_color"], labelsize=8, length=3)

        # ── 6. Цветные плашки цен на правой шкале (Price Badges) ──
        x_badge = total_x_span

        def add_price_badge(y_val, text, bg_color, text_color='#ffffff'):
            ax.text(
                x_badge, y_val, f" {text} ",
                color=text_color, fontsize=8.5, fontweight='bold',
                va='center', ha='left',
                bbox=dict(boxstyle='square,pad=0.25', facecolor=bg_color, edgecolor='none'),
                clip_on=False, zorder=10
            )

        add_price_badge(stop_loss, p_fmt.format(stop_loss), sl_color)
        add_price_badge(entry, p_fmt.format(entry), '#5d606b')
        add_price_badge(tp1, p_fmt.format(tp1), tp_color)
        add_price_badge(current_price, p_fmt.format(current_price), t["current_price_line"])

        # ── 7. Заголовок и метаданные TradingView ──
        last_row = df_chart.iloc[-1]
        title_text = (
            f"{symbol} · {timeframe.upper()} · SMART TRADER BOT    "
            f"O {p_fmt.format(last_row['open'])}  "
            f"H {p_fmt.format(last_row['high'])}  "
            f"L {p_fmt.format(last_row['low'])}  "
            f"C {p_fmt.format(last_row['close'])}"
        )
        ax.text(
            0.015, 0.965, title_text, transform=ax.transAxes,
            color=t["text_color"], fontsize=9.5, fontweight='bold', va='top', ha='left'
        )

        dir_label = "LONG POSITION" if direction == "LONG" else "SHORT POSITION"
        sub_text = f"[{dir_label}] | {order_type.replace('_', ' ')} | R:R 1:{rr:.1f} | {'*' * stars}"
        ax.text(
            0.015, 0.915, sub_text, transform=ax.transAxes,
            color=t["subtext_color"], fontsize=8.5, va='top', ha='left'
        )

        # Водяной знак TradingView
        ax.text(
            0.015, 0.03, "17 TradingView", transform=ax.transAxes,
            color=t["watermark"], fontsize=12, fontweight='bold', va='bottom', ha='left'
        )

        # ── 8. Временные метки по оси X ──
        if 'timestamp' in df_chart.columns:
            step = max(1, n_candles // 6)
            x_ticks = list(range(0, n_candles, step))
            x_labels = []
            for x_idx in x_ticks:
                ts = df_chart['timestamp'].iloc[x_idx]
                if isinstance(ts, str):
                    ts = pd.to_datetime(ts)
                x_labels.append(ts.strftime('%d %b %H:%M'))
            ax.set_xticks(x_ticks)
            ax.set_xticklabels(x_labels, rotation=0, ha='center', fontsize=7.5, color=t["subtext_color"])
        else:
            ax.set_xticks([])

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=140, bbox_inches='tight', facecolor=t["bg_color"])
        plt.close(fig)
        buf.seek(0)

        logger.info("TradingView-style chart generated for %s (%d candles)", symbol, n_candles)
        return buf.getvalue()

    except Exception as e:
        logger.error("Failed to generate TradingView chart for %s: %s", symbol, e, exc_info=True)
        plt.close('all')
        return None


def generate_outcome_chart(
    df: pd.DataFrame,
    symbol: str,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    close_price: float,
    status: str = "TP1_HIT",
    profit_usd: float = 0.0,
    theme: str = "dark",
    last_n_candles: int = 55,
    timeframe: str = "1h",
) -> Optional[bytes]:
    """
    Генерирует свечной график результата закрытой сделки (TP, SL или ручное закрытие).
    Отображает:
    - Реальные свечи, прошедшие от момента входа до закрытия
    - Уровни Entry, Stop Loss и Take Profit с заливкой зон
    - Метку точки выхода (Exit Price) с бейджем финансового результата
    - Правые плашки цен в стиле TradingView
    """
    try:
        if df is None or df.empty or len(df) < 5:
            logger.warning("Not enough data to generate outcome chart for %s", symbol)
            return None

        df_chart = df.tail(last_n_candles).copy().reset_index(drop=True)
        n_candles = len(df_chart)

        t = TV_THEMES.get(theme, TV_THEMES["dark"])

        if 'JPY' in symbol:
            p_fmt = "{:.3f}"
            pip_mult = 100.0
        elif 'XAU' in symbol:
            p_fmt = "{:.2f}"
            pip_mult = 10.0
        else:
            p_fmt = "{:.5f}"
            pip_mult = 10000.0

        if entry is None or entry <= 0:
            entry = float(df_chart['close'].iloc[0])
        if stop_loss is None or stop_loss <= 0:
            diff = abs(close_price - entry) if abs(close_price - entry) > 0 else (entry * 0.003)
            stop_loss = entry - diff if direction == "LONG" else entry + diff
        if take_profit is None or take_profit <= 0:
            diff = abs(entry - stop_loss) * 2.0
            take_profit = entry + diff if direction == "LONG" else entry - diff

        pnl_pips = (close_price - entry if direction == "LONG" else entry - close_price) * pip_mult
        profit_sign = "+" if profit_usd >= 0 else ""

        is_manual = status in ("MANUAL_CLOSE", "MANUAL_CLIENT_CLOSE")
        is_tp = not is_manual and (status in ("TP", "TP1_HIT", "TP2_HIT") or profit_usd > 0)
        is_sl = not is_manual and (status in ("SL", "SL_HIT") or profit_usd < 0)

        fig, ax = plt.subplots(figsize=(13, 6.8), dpi=140)
        fig.patch.set_facecolor(t["bg_color"])
        ax.set_facecolor(t["bg_color"])

        ax.grid(True, color=t["grid_color"], linestyle='-', linewidth=0.8, alpha=0.7)
        ax.set_axisbelow(True)

        # ── 1. Отрисовка японских свечей (на всём графике) ──
        candle_width = 0.58
        wick_width = 1.0

        for i in range(n_candles):
            row = df_chart.iloc[i]
            o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            is_up = c >= o
            c_color = t["up_candle"] if is_up else t["down_candle"]

            ax.plot([i, i], [l, h], color=c_color, linewidth=wick_width, zorder=2)

            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.01)

            rect = Rectangle(
                (i - candle_width / 2, body_bottom),
                candle_width, body_height,
                facecolor=c_color,
                edgecolor=c_color,
                linewidth=0.8,
                zorder=3
            )
            ax.add_patch(rect)

        # ── 2. TradingView Position Tool Box (ТОЛЬКО В ДИАПАЗОНЕ СДЕЛКИ!) ──
        # Ищем индекс свечи входа (entry_idx) назад от точки выхода
        last_x = n_candles - 1
        entry_idx = None
        search_window = min(35, last_x)
        for i in range(last_x - 1, max(-1, last_x - search_window - 1), -1):
            row_l = float(df_chart['low'].iloc[i])
            row_h = float(df_chart['high'].iloc[i])
            if row_l <= entry <= row_h:
                entry_idx = i
                break

        if entry_idx is None:
            recent_diffs = [abs(float(df_chart['close'].iloc[i]) - entry) for i in range(max(0, last_x - search_window), last_x)]
            if recent_diffs:
                entry_idx = max(0, last_x - search_window) + int(np.argmin(recent_diffs))
            else:
                entry_idx = max(0, last_x - 12)

        # Ограничиваем ширину бокса: минимум 6 свечей для читаемости текста, максимум 28
        if (last_x - entry_idx) < 5:
            entry_idx = max(0, last_x - 6)
        elif (last_x - entry_idx) > 28:
            entry_idx = last_x - 28

        x_box_start = entry_idx - 0.35
        x_box_end = last_x + 1.2
        box_w = x_box_end - x_box_start

        sl_color = t["sl_box"]
        tp_color = t["tp_box"]

        if direction == "LONG":
            profit_height = max(0.00001, take_profit - entry)
            risk_height = max(0.00001, entry - stop_loss)
            rect_tp = Rectangle(
                (x_box_start, entry), box_w, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.22, linewidth=1.2, zorder=4
            )
            rect_sl = Rectangle(
                (x_box_start, stop_loss), box_w, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.22, linewidth=1.2, zorder=4
            )
        else:
            risk_height = max(0.00001, stop_loss - entry)
            profit_height = max(0.00001, entry - take_profit)
            rect_sl = Rectangle(
                (x_box_start, entry), box_w, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.22, linewidth=1.2, zorder=4
            )
            rect_tp = Rectangle(
                (x_box_start, take_profit), box_w, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.22, linewidth=1.2, zorder=4
            )

        ax.add_patch(rect_tp)
        ax.add_patch(rect_sl)

        total_x_span = n_candles + 5

        # Линии уровней ТОЛЬКО ВНУТРИ БОКСА ПОЗИЦИИ
        ax.plot([x_box_start, x_box_end], [entry, entry], color='#ffffff', linewidth=1.4, linestyle='-', zorder=5)
        ax.plot([x_box_start, x_box_end], [stop_loss, stop_loss], color=sl_color, linewidth=1.3, linestyle='-', zorder=5)
        ax.plot([x_box_start, x_box_end], [take_profit, take_profit], color=tp_color, linewidth=1.3, linestyle='-', zorder=5)

        # Тонкие пунктирные проекции от правого края бокса к шкале цен
        ax.plot([x_box_end, total_x_span], [entry, entry], color='#5d606b', linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)
        ax.plot([x_box_end, total_x_span], [stop_loss, stop_loss], color=sl_color, linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)
        ax.plot([x_box_end, total_x_span], [take_profit, take_profit], color=tp_color, linewidth=0.9, linestyle=':', alpha=0.6, zorder=1)

        # ── 3. Аутентичные синие ручки TradingView [■] (Handle markers) ──
        handles_x = [x_box_start, x_box_end, x_box_start, x_box_end, x_box_start, x_box_end]
        handles_y = [take_profit, take_profit, stop_loss, stop_loss, entry, entry]
        ax.scatter(handles_x, handles_y, color='#2962ff', s=26, marker='s', edgecolors='#ffffff', linewidth=1.0, zorder=6)

        # ── 4. Информационные плашки TradingView ──
        box_center_x = (x_box_start + x_box_end) / 2
        rr = profit_height / risk_height if risk_height > 0 else 2.0

        # Центральный бейдж TradingView (Closed PnL + R:R)
        mid_badge_bg = "#00897b" if profit_usd >= 0 else "#b22834"
        ax.text(
            box_center_x, entry,
            f"Closed PnL: {profit_sign}{profit_usd:.2f} USD ({profit_sign}{pnl_pips:.1f} p)\nRisk/reward ratio: {rr:.2f}",
            color='#ffffff', fontsize=8.2, fontweight='bold', ha='center', va='center', zorder=7,
            bbox=dict(boxstyle='round,pad=0.35', facecolor=mid_badge_bg, edgecolor='#ffffff', linewidth=0.8, alpha=0.95)
        )

        # Бейджи Target и Stop
        reward_pips = abs(take_profit - entry) * pip_mult
        risk_pips = abs(entry - stop_loss) * pip_mult

        ax.text(
            box_center_x, take_profit,
            f" Target: +{reward_pips:.1f} pips ",
            color='#ffffff', fontsize=8.0, fontweight='bold', ha='center',
            va='bottom' if direction == "LONG" else 'top', zorder=7,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=tp_color, edgecolor='none', alpha=0.92)
        )
        ax.text(
            box_center_x, stop_loss,
            f" Stop: -{risk_pips:.1f} pips ",
            color='#ffffff', fontsize=8.0, fontweight='bold', ha='center',
            va='top' if direction == "LONG" else 'bottom', zorder=7,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=sl_color, edgecolor='none', alpha=0.92)
        )

        # ── 5. Пунктирная траектория сделки от входа к выходу ──
        ax.annotate(
            "",
            xy=(last_x, close_price),
            xytext=(entry_idx, entry),
            arrowprops=dict(arrowstyle="-|>", color="#e0e3eb", linestyle="--", linewidth=1.5, mutation_scale=12),
            zorder=8
        )
        exit_color = tp_color if profit_usd >= 0 else sl_color
        ax.scatter([last_x], [close_price], color=exit_color, s=85, edgecolors='#ffffff', linewidth=1.6, zorder=9)

        # ── 4. Границы осей X и Y ──
        total_x_span = n_candles + 4
        ax.set_xlim(-1, total_x_span)

        all_y = list(df_chart['low']) + list(df_chart['high']) + [entry, stop_loss, take_profit, close_price]
        y_min, y_max = min(all_y), max(all_y)
        y_padding = max(0.0005, (y_max - y_min) * 0.10)
        ax.set_ylim(y_min - y_padding, y_max + y_padding)

        ax.spines['top'].set_visible(False)
        ax.spines['bottom'].set_color(t["axis_color"])
        ax.spines['left'].set_visible(False)
        ax.spines['right'].set_color(t["axis_color"])

        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.tick_params(axis='y', colors=t["subtext_color"], labelsize=8.5, length=3)
        ax.tick_params(axis='x', colors=t["subtext_color"], labelsize=8, length=3)

        # ── 5. Плашки цен на правой шкале ──
        x_badge = total_x_span

        def add_badge(y_val, text, bg_color, text_color='#ffffff'):
            ax.text(
                x_badge, y_val, f" {text} ",
                color=text_color, fontsize=8.5, fontweight='bold',
                va='center', ha='left',
                bbox=dict(boxstyle='square,pad=0.25', facecolor=bg_color, edgecolor='none'),
                clip_on=False, zorder=10
            )

        add_badge(stop_loss, f"SL {p_fmt.format(stop_loss)}", sl_color)
        add_badge(entry, f"ENTRY {p_fmt.format(entry)}", '#5d606b')
        add_badge(take_profit, f"TP {p_fmt.format(take_profit)}", tp_color)
        add_badge(close_price, f"EXIT {p_fmt.format(close_price)}", exit_color)

        # ── 6. Заголовок и метаданные ──
        res_str = "TAKE PROFIT" if is_tp else ("STOP LOSS" if is_sl else "MANUAL CLOSE")
        title_text = f"{symbol} · {timeframe.upper()} · SMART TRADER BOT · [РЕЗУЛЬТАТ: {res_str}]"
        ax.text(
            0.015, 0.965, title_text, transform=ax.transAxes,
            color=t["text_color"], fontsize=9.5, fontweight='bold', va='top', ha='left'
        )

        dir_lbl = "LONG" if direction == "LONG" else "SHORT"
        sub_text = (
            f"Позиция: {dir_lbl} | Вход: {p_fmt.format(entry)} | Выход: {p_fmt.format(close_price)} | "
            f"PnL: {profit_sign}{profit_usd:.2f} USD ({profit_sign}{pnl_pips:.1f} pips)"
        )
        ax.text(
            0.015, 0.915, sub_text, transform=ax.transAxes,
            color=t["subtext_color"], fontsize=8.5, va='top', ha='left'
        )

        ax.text(
            0.015, 0.03, "TradingView Style", transform=ax.transAxes,
            color=t["watermark"], fontsize=12, fontweight='bold', va='bottom', ha='left'
        )

        # Временные метки X
        if 'timestamp' in df_chart.columns:
            step = max(1, n_candles // 6)
            x_ticks = list(range(0, n_candles, step))
            x_labels = []
            for x_idx in x_ticks:
                ts = df_chart['timestamp'].iloc[x_idx]
                if isinstance(ts, str):
                    ts = pd.to_datetime(ts)
                x_labels.append(ts.strftime('%d %b %H:%M'))
            ax.set_xticks(x_ticks)
            ax.set_xticklabels(x_labels, rotation=0, ha='center', fontsize=7.5, color=t["subtext_color"])
        else:
            ax.set_xticks([])

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=140, bbox_inches='tight', facecolor=t["bg_color"])
        plt.close(fig)
        buf.seek(0)

        logger.info("Outcome chart generated for %s (Status: %s, PnL: %s)", symbol, status, profit_usd)
        return buf.getvalue()

    except Exception as e:
        logger.error("Failed to generate outcome chart for %s: %s", symbol, e, exc_info=True)
        plt.close('all')
        return None


def save_chart_to_file(chart_bytes: bytes, filepath: str) -> bool:
    """Сохраняет график в файл (для отладки)."""
    try:
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(chart_bytes)
        return True
    except Exception as e:
        logger.error("Failed to save chart: %s", e)
        return False

