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
from typing import Optional, Union
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Headless rendering
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

logger = logging.getLogger(__name__)

# ── Цветовые палитры TradingView ─────────────────────────
TV_THEMES = {
    "ict": {
        "bg_color": "#ffffff",
        "grid_color": "#e6e6e6",
        "grid_style": ":",
        "axis_color": "#d0d0d0",
        "text_color": "#131722",
        "subtext_color": "#666666",
        "up_body": "#ea8c00",
        "up_edge": "#ea8c00",
        "up_wick": "#ea8c00",
        "down_body": "#000000",
        "down_edge": "#000000",
        "down_wick": "#000000",
        "up_candle": "#ea8c00",
        "down_candle": "#000000",
        "sl_box": "#888888",
        "tp_box": "#c8c8c8",
        "entry_line": "#333333",
        "current_price_line": "#222222",
        "watermark": "#e0e0e0",
    },
    "dark": {
        "bg_color": "#131722",
        "grid_color": "#1e222d",
        "grid_style": "-",
        "axis_color": "#2a2e39",
        "text_color": "#d1d4dc",
        "subtext_color": "#787b86",
        "up_body": "#089981",
        "up_edge": "#089981",
        "up_wick": "#089981",
        "down_body": "#f23645",
        "down_edge": "#f23645",
        "down_wick": "#f23645",
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
        "grid_style": "-",
        "axis_color": "#e0e3eb",
        "text_color": "#131722",
        "subtext_color": "#787b86",
        "up_body": "#089981",
        "up_edge": "#089981",
        "up_wick": "#089981",
        "down_body": "#f23645",
        "down_edge": "#f23645",
        "down_wick": "#f23645",
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
    theme: str = "ict",
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
        t = TV_THEMES.get(theme, TV_THEMES["ict"])

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
        ax.grid(True, color=t["grid_color"], linestyle=t.get("grid_style", "-"), linewidth=0.8, alpha=0.5 if theme == "ict" else 0.7)
        ax.set_axisbelow(True)

        # ── 1. Отрисовка японских свечей ──
        candle_width = 0.58
        wick_width = 1.1

        for i in range(n_candles):
            row = df_chart.iloc[i]
            o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            is_up = c >= o
            w_color = t.get("up_wick", t.get("up_candle")) if is_up else t.get("down_wick", t.get("down_candle"))
            f_color = t.get("up_body", t.get("up_candle")) if is_up else t.get("down_body", t.get("down_candle"))
            e_color = t.get("up_edge", t.get("up_candle")) if is_up else t.get("down_edge", t.get("down_candle"))

            # Тень (фитиль)
            ax.plot([i, i], [l, h], color=w_color, linewidth=wick_width, zorder=2)

            # Тело свечи
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.015)

            rect = Rectangle(
                (i - candle_width / 2, body_bottom),
                candle_width, body_height,
                facecolor=f_color,
                edgecolor=e_color,
                linewidth=1.1,
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
        is_ict = (theme == "ict")
        tp_alpha = 0.55 if is_ict else 0.28
        sl_alpha = 0.65 if is_ict else 0.28
        tp_edge = "#a8a8a8" if is_ict else tp_color
        sl_edge = "#666666" if is_ict else sl_color

        if direction == "LONG":
            # Зона профита сверху (Entry -> TP1)
            profit_height = max(0.00001, tp1 - entry)
            rect_tp = Rectangle(
                (x_start, entry), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_edge, alpha=tp_alpha, linewidth=1.0, zorder=4
            )
            ax.add_patch(rect_tp)

            # Зона риска снизу (SL -> Entry)
            risk_height = max(0.00001, entry - stop_loss)
            rect_sl = Rectangle(
                (x_start, stop_loss), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_edge, alpha=sl_alpha, linewidth=1.0, zorder=4
            )
            ax.add_patch(rect_sl)

            tp_text_y = entry + profit_height * 0.5
            sl_text_y = stop_loss + risk_height * 0.5

        else:  # SHORT
            # Зона риска сверху (Entry -> SL)
            risk_height = max(0.00001, stop_loss - entry)
            rect_sl = Rectangle(
                (x_start, entry), box_width, risk_height,
                facecolor=sl_color, edgecolor=sl_edge, alpha=sl_alpha, linewidth=1.0, zorder=4
            )
            ax.add_patch(rect_sl)

            # Зона профита снизу (TP1 -> Entry)
            profit_height = max(0.00001, entry - tp1)
            rect_tp = Rectangle(
                (x_start, tp1), box_width, profit_height,
                facecolor=tp_color, edgecolor=tp_edge, alpha=tp_alpha, linewidth=1.0, zorder=4
            )
            ax.add_patch(rect_tp)

            tp_text_y = tp1 + profit_height * 0.5
            sl_text_y = entry + risk_height * 0.5

        # Диагональная пунктирная линия инструмента позиции TradingView
        ax.plot([x_start, x_end], [entry, tp1], color="#777777", linestyle=":", linewidth=1.0, alpha=0.8, zorder=4)

        # Линии уровней внутри бокса позиции
        ax.plot([x_start, x_end], [entry, entry], color=t["entry_line"], linewidth=1.4, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [stop_loss, stop_loss], color="#555555" if is_ict else sl_color, linewidth=1.1, linestyle='-', zorder=5)
        ax.plot([x_start, x_end], [tp1, tp1], color="#888888" if is_ict else tp_color, linewidth=1.1, linestyle='-', zorder=5)

        # Текстовые плашки Target и Stop внутри бокса позиции
        box_center_x = x_start + box_width / 2
        if is_ict:
            ax.text(
                box_center_x, tp_text_y, f"Target: +{reward_pips:.1f} pips\nR:R = 1:{rr:.1f}",
                color='#222222', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
                bbox=dict(boxstyle='round,pad=0.25', facecolor='#ffffff', alpha=0.85, edgecolor='#a8a8a8', linewidth=0.6)
            )
            ax.text(
                box_center_x, sl_text_y, f"Stop: -{risk_pips:.1f} pips",
                color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
                bbox=dict(boxstyle='round,pad=0.25', facecolor='#555555', alpha=0.9, edgecolor='none')
            )
        else:
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
        ax.axhline(current_price, color=t["current_price_line"], linestyle=t.get("grid_style", "--"), linewidth=1.1, alpha=0.85, zorder=3)

        # ── 4. Границы осей X и Y ──
        total_x_span = n_candles + future_padding_bars
        ax.set_xlim(-1, total_x_span)

        all_y = list(df_chart['low']) + list(df_chart['high']) + [entry, stop_loss, tp1, current_price]
        if tp2:
            all_y.append(tp2)
        y_min, y_max = min(all_y), max(all_y)
        y_padding = max(0.0005, (y_max - y_min) * 0.12)
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

        add_price_badge(stop_loss, p_fmt.format(stop_loss), "#444444" if is_ict else sl_color)
        add_price_badge(entry, p_fmt.format(entry), '#131722' if is_ict else '#5d606b')
        add_price_badge(tp1, p_fmt.format(tp1), "#787878" if is_ict else tp_color)
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
    theme: str = "ict",
    last_n_candles: int = 55,
    timeframe: str = "1h",
    signal_time: Optional[Union[str, datetime]] = None,
) -> Optional[bytes]:
    """
    Генерирует свечной график результата закрытой сделки в стиле TradingView:
    - Исторические свечи слева от точки сигнала (до 45 свечей)
    - Позиционный бокс начинается точно на сигнальной свече (не растягивается на всю историю)
    - Реальные свечи за время жизни сделки прорисовываются внутри бокса
    - На закрывающей свече ставится маркер точки выхода (зеленый/красный/синий круг)
    - Четкие непрозрачные плашки Target / Stop внутри бокса
    - Правые ценовые бейджи TradingView (TP, EXIT, ENTRY, SL)
    """
    try:
        if df is None or df.empty or len(df) < 5:
            logger.warning("Not enough data to generate outcome chart for %s", symbol)
            return None

        t = TV_THEMES.get(theme, TV_THEMES["ict"])

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
            entry = float(df['close'].iloc[0])
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

        # Определение точки разделения на историю и свечи сделки
        sig_idx = None
        if signal_time is not None and 'timestamp' in df.columns:
            try:
                sig_dt = pd.to_datetime(signal_time)
                df_ts = pd.to_datetime(df['timestamp'])
                diffs = (df_ts - sig_dt).abs()
                min_diff_idx = diffs.idxmin()
                if min_diff_idx is not None and min_diff_idx < len(df):
                    sig_idx = int(min_diff_idx)
            except Exception as e:
                logger.debug("Failed to match signal_time %s: %s", signal_time, e)

        total_len = len(df)
        if sig_idx is None or sig_idx <= 0 or sig_idx >= total_len - 1:
            n_outcome_default = max(3, min(15, total_len // 3))
            sig_idx = total_len - n_outcome_default - 1

        hist_start = max(0, sig_idx - 44)
        df_hist = df.iloc[hist_start : sig_idx + 1].copy().reset_index(drop=True)
        df_outcome = df.iloc[sig_idx + 1:].copy().reset_index(drop=True)
        if df_outcome.empty:
            df_outcome = df.iloc[sig_idx : sig_idx + 1].copy().reset_index(drop=True)

        n_hist = len(df_hist)
        n_outcome = len(df_outcome)

        fig, ax = plt.subplots(figsize=(13, 6.8), dpi=140)
        fig.patch.set_facecolor(t["bg_color"])
        ax.set_facecolor(t["bg_color"])
        ax.grid(True, color=t["grid_color"], linestyle=t.get("grid_style", "-"), linewidth=0.8, alpha=0.5 if theme == "ict" else 0.7)
        ax.set_axisbelow(True)

        candle_width = 0.58
        wick_width = 1.1

        # ── 1. Отрисовка исторических свечей (слева от бокса) ──
        for i in range(n_hist):
            row = df_hist.iloc[i]
            o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            is_up = c >= o
            w_color = t.get("up_wick", t.get("up_candle")) if is_up else t.get("down_wick", t.get("down_candle"))
            f_color = t.get("up_body", t.get("up_candle")) if is_up else t.get("down_body", t.get("down_candle"))
            e_color = t.get("up_edge", t.get("up_candle")) if is_up else t.get("down_edge", t.get("down_candle"))
            ax.plot([i, i], [l, h], color=w_color, linewidth=wick_width, zorder=2)
            b_bot = min(o, c)
            b_h = max(abs(c - o), (h - l) * 0.015)
            rect = Rectangle((i - candle_width / 2, b_bot), candle_width, b_h, facecolor=f_color, edgecolor=e_color, linewidth=1.1, zorder=3)
            ax.add_patch(rect)

        # ── 2. Позиционный бокс (ровно от сигнальной свечи) ──
        future_padding_bars = max(n_outcome + 4, 15)
        box_width = future_padding_bars - 2
        x_box_start = n_hist - 0.5
        x_box_end = x_box_start + box_width
        total_x_span = n_hist + future_padding_bars

        reward_pips = abs(take_profit - entry) * pip_mult
        risk_pips = abs(entry - stop_loss) * pip_mult
        rr = (reward_pips / risk_pips) if risk_pips > 0 else 2.0

        is_ict = (theme == "ict")
        tp_alpha = 0.55 if is_ict else 0.22
        sl_alpha = 0.65 if is_ict else 0.22
        tp_edge = "#a8a8a8" if is_ict else t["tp_box"]
        sl_edge = "#666666" if is_ict else t["sl_box"]

        if direction == "LONG":
            profit_height = max(0.00001, take_profit - entry)
            risk_height = max(0.00001, entry - stop_loss)
            rect_tp = Rectangle(
                (x_box_start, entry), box_width, profit_height,
                facecolor=t["tp_box"], edgecolor=tp_edge, alpha=tp_alpha, linewidth=1.0, zorder=1
            )
            rect_sl = Rectangle(
                (x_box_start, stop_loss), box_width, risk_height,
                facecolor=t["sl_box"], edgecolor=sl_edge, alpha=sl_alpha, linewidth=1.0, zorder=1
            )
            tp_text_y = entry + profit_height * 0.5
            sl_text_y = stop_loss + risk_height * 0.5
        else:
            profit_height = max(0.00001, entry - take_profit)
            risk_height = max(0.00001, stop_loss - entry)
            rect_tp = Rectangle(
                (x_box_start, take_profit), box_width, profit_height,
                facecolor=t["tp_box"], edgecolor=tp_edge, alpha=tp_alpha, linewidth=1.0, zorder=1
            )
            rect_sl = Rectangle(
                (x_box_start, entry), box_width, risk_height,
                facecolor=t["sl_box"], edgecolor=sl_edge, alpha=sl_alpha, linewidth=1.0, zorder=1
            )
            tp_text_y = take_profit + profit_height * 0.5
            sl_text_y = entry + risk_height * 0.5

        ax.add_patch(rect_tp)
        ax.add_patch(rect_sl)

        # Диагональная пунктирная линия
        ax.plot([x_box_start, x_box_end], [entry, take_profit], color="#777777", linestyle=":", linewidth=1.0, alpha=0.8, zorder=2)

        # Линии уровней внутри бокса
        ax.plot([x_box_start, x_box_end], [entry, entry], color=t["entry_line"], linewidth=1.4, linestyle='-', zorder=2)
        ax.plot([x_box_start, x_box_end], [stop_loss, stop_loss], color="#555555" if is_ict else t["sl_box"], linewidth=1.1, linestyle='-', zorder=2)
        ax.plot([x_box_start, x_box_end], [take_profit, take_profit], color="#888888" if is_ict else t["tp_box"], linewidth=1.1, linestyle='-', zorder=2)

        # Плашки внутри бокса
        box_center_x = x_box_start + box_width / 2
        badge_tp_text = f"Target: +{reward_pips:.1f} pips" + "\n" + f"R:R = 1:{rr:.1f}"
        if is_ict:
            ax.text(
                box_center_x, tp_text_y, badge_tp_text,
                color='#222222', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=10,
                bbox=dict(boxstyle='round,pad=0.25', facecolor='#ffffff', alpha=0.85, edgecolor='#a8a8a8', linewidth=0.6)
            )
            ax.text(
                box_center_x, sl_text_y, f"Stop: -{risk_pips:.1f} pips",
                color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=10,
                bbox=dict(boxstyle='round,pad=0.25', facecolor='#555555', alpha=0.9, edgecolor='none')
            )
        else:
            ax.text(
                box_center_x, tp_text_y, badge_tp_text,
                color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=10,
                bbox=dict(boxstyle='round,pad=0.3', facecolor=t["tp_box"], alpha=1.0, edgecolor='#ffffff', linewidth=0.4)
            )
            ax.text(
                box_center_x, sl_text_y, f"Stop: -{risk_pips:.1f} pips",
                color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=10,
                bbox=dict(boxstyle='round,pad=0.3', facecolor=t["sl_box"], alpha=1.0, edgecolor='#ffffff', linewidth=0.4)
            )

        # ── 3. Отрисовка РЕАЛЬНЫХ СВЕЧЕЙ СДЕЛКИ ВНУТРИ БОКСА ──
        for j in range(n_outcome):
            idx = n_hist + j
            row = df_outcome.iloc[j]
            o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
            is_up = c >= o
            w_color = t.get("up_wick", t.get("up_candle")) if is_up else t.get("down_wick", t.get("down_candle"))
            f_color = t.get("up_body", t.get("up_candle")) if is_up else t.get("down_body", t.get("down_candle"))
            e_color = t.get("up_edge", t.get("up_candle")) if is_up else t.get("down_edge", t.get("down_candle"))
            ax.plot([idx, idx], [l, h], color=w_color, linewidth=wick_width, zorder=4)
            b_bot = min(o, c)
            b_h = max(abs(c - o), (h - l) * 0.015)
            rect = Rectangle((idx - candle_width / 2, b_bot), candle_width, b_h, facecolor=f_color, edgecolor=e_color, linewidth=1.1, zorder=5)
            ax.add_patch(rect)

        # ── 4. Маркер точки закрытия на последней свече ──
        last_idx = n_hist + n_outcome - 1
        exit_badge_color = "#131722" if is_ict else (t["tp_box"] if is_tp else (t["sl_box"] if is_sl else "#2962ff"))
        ax.scatter([last_idx], [close_price], color=exit_badge_color, s=95, edgecolors='#ffffff', linewidth=1.6, zorder=8)
        ax.plot([x_box_start, last_idx], [close_price, close_price], color=exit_badge_color, linestyle='--', linewidth=1.1, alpha=0.75, zorder=6)

        # ── 5. Границы осей ──
        all_candles_low = list(df_hist['low']) + list(df_outcome['low'])
        all_candles_high = list(df_hist['high']) + list(df_outcome['high'])
        all_y = all_candles_low + all_candles_high + [entry, stop_loss, take_profit, close_price]
        y_min, y_max = min(all_y), max(all_y)
        y_padding = max(0.0005, (y_max - y_min) * 0.12)
        ax.set_ylim(y_min - y_padding, y_max + y_padding)
        ax.set_xlim(-1, total_x_span)

        ax.spines['top'].set_visible(False)
        ax.spines['bottom'].set_color(t["axis_color"])
        ax.spines['left'].set_visible(False)
        ax.spines['right'].set_color(t["axis_color"])

        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.tick_params(axis='y', colors=t["subtext_color"], labelsize=8.5, length=3)
        ax.tick_params(axis='x', colors=t["subtext_color"], labelsize=8, length=3)

        # ── 6. Правые плашки цен ──
        def add_price_badge(y_val, text, bg_color, text_color='#ffffff'):
            ax.text(
                total_x_span, y_val, f" {text} ",
                color=text_color, fontsize=8.5, fontweight='bold',
                va='center', ha='left',
                bbox=dict(boxstyle='square,pad=0.25', facecolor=bg_color, edgecolor='none'),
                clip_on=False, zorder=10
            )

        add_price_badge(take_profit, p_fmt.format(take_profit), "#787878" if is_ict else t["tp_box"])
        add_price_badge(close_price, p_fmt.format(close_price), exit_badge_color)
        add_price_badge(entry, p_fmt.format(entry), "#131722" if is_ict else "#4a4e58")
        add_price_badge(stop_loss, p_fmt.format(stop_loss), "#444444" if is_ict else t["sl_box"])

        # ── 7. Заголовок и метаданные ──
        if is_tp:
            res_str = "TAKE PROFIT"
        elif is_sl:
            res_str = "STOP LOSS"
        elif is_manual:
            res_str = "MANUAL CLOSE"
        else:
            res_str = "БЕЗУБЫТОК"

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

        # Водяной знак TradingView
        ax.text(
            0.015, 0.03, "17 TradingView", transform=ax.transAxes,
            color=t["watermark"], fontsize=12, fontweight='bold', va='bottom', ha='left'
        )

        # ── 8. Временные метки по оси X ──
        n_total_drawn = n_hist + n_outcome
        step = max(1, n_total_drawn // 6)
        x_ticks = list(range(0, n_hist, step))
        if (n_total_drawn - 1) not in x_ticks:
            x_ticks.append(n_total_drawn - 1)

        x_labels = []
        for x_idx in x_ticks:
            if x_idx < n_hist:
                ts_val = df_hist['timestamp'].iloc[x_idx] if 'timestamp' in df_hist.columns else None
            else:
                out_i = x_idx - n_hist
                ts_val = df_outcome['timestamp'].iloc[out_i] if 'timestamp' in df_outcome.columns else None

            if ts_val is not None:
                try:
                    ts_dt = pd.to_datetime(ts_val)
                    x_labels.append(ts_dt.strftime('%d %b %H:%M'))
                except Exception:
                    x_labels.append(str(ts_val)[:12])
            else:
                x_labels.append(str(x_idx))

        ax.set_xticks(x_ticks)
        ax.set_xticklabels(x_labels, rotation=0, ha='center', fontsize=7.5, color=t["subtext_color"])

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

