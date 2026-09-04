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
            profit_height = tp1 - entry
            rect_tp = Rectangle(
                (x_start, entry), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_tp)

            # Красная зона снизу (SL -> Entry)
            risk_height = entry - stop_loss
            rect_sl = Rectangle(
                (x_start, stop_loss), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_sl)

            tp_text_y = entry + profit_height * 0.5
            sl_text_y = stop_loss + risk_height * 0.5

        else:  # SHORT
            # Красная зона сверху (Entry -> SL)
            risk_height = stop_loss - entry
            rect_sl = Rectangle(
                (x_start, entry), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_sl)

            # Зеленая зона снизу (TP1 -> Entry)
            profit_height = entry - tp1
            rect_tp = Rectangle(
                (x_start, tp1), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_color, alpha=0.28, linewidth=1.2, zorder=4
            )
            ax.add_patch(rect_tp)

            tp_text_y = tp1 + profit_height * 0.5
            sl_text_y = entry + risk_height * 0.5

        # Линии уровней внутри бокса позиции
        ax.plot([x_start, x_end], [entry, entry], color=t["entry_line"], linewidth=1.6, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [stop_loss, stop_loss], color=sl_color, linewidth=1.2, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [tp1, tp1], color=tp_color, linewidth=1.2, linestyle='-', zorder=5)

        # Текстовые плашки Target и Stop внутри бокса позиции
        box_center_x = x_start + box_width / 2
        ax.text(
            box_center_x, tp_text_y, f"Target: +{reward_pips:.1f} pips\nR:R = 1:{rr:.1f}",
            color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=tp_color, alpha=0.75, edgecolor='none')
        )
        ax.text(
            box_center_x, sl_text_y, f"Stop: -{risk_pips:.1f} pips",
            color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
            bbox=dict(boxstyle='round,pad=0.25', facecolor=sl_color, alpha=0.75, edgecolor='none')
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
