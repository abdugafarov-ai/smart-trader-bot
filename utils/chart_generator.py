"""
Генератор институциональных графиков с разметкой сигнала.
Строит свечной график с чёткой визуализацией Entry, SL, TP1, TP2, TP3.
Поддержка тёмной (Wall Street) и светлой тем.
"""

import io
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib
matplotlib.use('Agg')  # Headless rendering
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

logger = logging.getLogger(__name__)

# ── Темы ──────────────────────────────────────────────────
THEMES = {
    "dark": {
        "base_mpf_style": "nightclouds",
        "bg_color": "#0d1117",
        "face_color": "#0d1117",
        "edge_color": "#30363d",
        "text_color": "#e6edf3",
        "grid_color": "#21262d",
        "up_color": "#26a641",
        "down_color": "#f85149",
        "up_edge": "#26a641",
        "down_edge": "#f85149",
        "wick_up": "#26a641",
        "wick_down": "#f85149",
        "volume_up": "#26a64180",
        "volume_down": "#f8514980",
        "entry_color": "#58a6ff",
        "sl_color": "#f85149",
        "tp_color": "#3fb950",
        "tp2_color": "#a371f7",
        "tp3_color": "#f0883e",
        "current_color": "#e3b341",
        "watermark_color": "#30363d",
    },
    "light": {
        "base_mpf_style": "charles",
        "bg_color": "#ffffff",
        "face_color": "#ffffff",
        "edge_color": "#d0d7de",
        "text_color": "#1f2328",
        "grid_color": "#eaeef2",
        "up_color": "#1a7f37",
        "down_color": "#cf222e",
        "up_edge": "#1a7f37",
        "down_edge": "#cf222e",
        "wick_up": "#1a7f37",
        "wick_down": "#cf222e",
        "volume_up": "#1a7f3740",
        "volume_down": "#cf222e40",
        "entry_color": "#0969da",
        "sl_color": "#cf222e",
        "tp_color": "#1a7f37",
        "tp2_color": "#8250df",
        "tp3_color": "#bf8700",
        "current_color": "#bf8700",
        "watermark_color": "#eaeef2",
    },
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
    last_n_candles: int = 60,
) -> Optional[bytes]:
    """
    Генерирует свечной график с институциональной разметкой Entry, SL, TP.
    Возвращает PNG-картинку в bytes.
    """
    try:
        if df is None or df.empty or len(df) < 10:
            logger.warning("Not enough data to generate chart for %s", symbol)
            return None

        # Берём последние N свечей для чистоты графика
        df_chart = df.tail(last_n_candles).copy()
        
        # mplfinance требует DatetimeIndex
        if 'timestamp' in df_chart.columns:
            df_chart = df_chart.set_index('timestamp')
        if not isinstance(df_chart.index, pd.DatetimeIndex):
            df_chart.index = pd.to_datetime(df_chart.index)
        
        # Переименовываем колонки для mplfinance
        df_chart = df_chart.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        })
        
        # Убираем строки с NaN
        required_cols = ['Open', 'High', 'Low', 'Close']
        for col in required_cols:
            if col not in df_chart.columns:
                logger.error("Missing column %s in df", col)
                return None
        df_chart = df_chart.dropna(subset=required_cols)
        if len(df_chart) < 5:
            return None

        t = THEMES.get(theme, THEMES["dark"])

        # Создаём стиль mplfinance
        mc = mpf.make_marketcolors(
            up=t["up_color"], down=t["down_color"],
            edge={'up': t["up_edge"], 'down': t["down_edge"]},
            wick={'up': t["wick_up"], 'down': t["wick_down"]},
            volume={'up': t["volume_up"], 'down': t["volume_down"]},
        )
        s = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor=t["face_color"],
            edgecolor=t["edge_color"],
            gridcolor=t["grid_color"],
            gridstyle='--',
            gridaxis='both',
            rc={
                'font.size': 9,
                'axes.labelsize': 9,
                'axes.titlesize': 11,
            }
        )

        # ── Горизонтальные уровни ──
        hlines_prices = []
        hlines_colors = []
        hlines_widths = []
        hlines_styles = []
        
        # Entry
        hlines_prices.append(entry)
        hlines_colors.append(t["entry_color"])
        hlines_widths.append(2.0)
        hlines_styles.append('-')
        
        # Stop Loss
        hlines_prices.append(stop_loss)
        hlines_colors.append(t["sl_color"])
        hlines_widths.append(2.0)
        hlines_styles.append('--')
        
        # TP1
        hlines_prices.append(tp1)
        hlines_colors.append(t["tp_color"])
        hlines_widths.append(1.8)
        hlines_styles.append('-')
        
        # TP2
        if tp2:
            hlines_prices.append(tp2)
            hlines_colors.append(t["tp2_color"])
            hlines_widths.append(1.5)
            hlines_styles.append('-')
        
        # TP3
        if tp3:
            hlines_prices.append(tp3)
            hlines_colors.append(t["tp3_color"])
            hlines_widths.append(1.5)
            hlines_styles.append('-.')

        # Current price
        if current_price:
            hlines_prices.append(current_price)
            hlines_colors.append(t["current_color"])
            hlines_widths.append(1.0)
            hlines_styles.append(':')

        hlines_dict = dict(
            hlines=hlines_prices,
            colors=hlines_colors,
            linewidths=hlines_widths,
            linestyle=hlines_styles,
        )

        # ── Определяем формат цены ──
        if 'JPY' in symbol:
            price_fmt = ".2f"
        elif 'XAU' in symbol:
            price_fmt = ".2f"
        else:
            price_fmt = ".5f"

        # ── Рисуем график ──
        fig, axes = mpf.plot(
            df_chart,
            type='candle',
            style=s,
            volume=True if 'Volume' in df_chart.columns else False,
            hlines=hlines_dict,
            figsize=(14, 8),
            returnfig=True,
            tight_layout=True,
            warn_too_much_data=1000,
        )

        ax_price = axes[0]

        # ── Заголовок ──
        dir_text = "LONG (BUY)" if direction == "LONG" else "SHORT (SELL)"
        stars_text = "*" * stars
        title = f"{symbol}  |  {dir_text}  |  {order_type.replace('_', ' ')}  |  {stars_text}"
        ax_price.set_title(title, fontsize=13, fontweight='bold', color=t["text_color"], pad=12)

        # ── Подписи уровней на правом краю ──
        x_right = len(df_chart) - 0.5  # Правая граница графика
        y_pad = (df_chart['High'].max() - df_chart['Low'].min()) * 0.005

        label_configs = [
            (entry, f"ENTRY  {entry:{price_fmt}}", t["entry_color"], 'bold'),
            (stop_loss, f"SL  {stop_loss:{price_fmt}}", t["sl_color"], 'bold'),
            (tp1, f"TP1  {tp1:{price_fmt}}", t["tp_color"], 'bold'),
        ]
        if tp2:
            label_configs.append((tp2, f"TP2  {tp2:{price_fmt}}", t["tp2_color"], 'bold'))
        if tp3:
            label_configs.append((tp3, f"TP3  {tp3:{price_fmt}}", t["tp3_color"], 'bold'))
        if current_price:
            label_configs.append((current_price, f"MARKET  {current_price:{price_fmt}}", t["current_color"], 'normal'))

        for price_val, label, color, weight in label_configs:
            ax_price.annotate(
                label,
                xy=(x_right, price_val),
                xytext=(x_right + 2, price_val),
                fontsize=8.5,
                fontweight=weight,
                color=color,
                va='center',
                bbox=dict(
                    boxstyle='round,pad=0.3',
                    facecolor=t["bg_color"],
                    edgecolor=color,
                    alpha=0.9,
                    linewidth=1.2,
                ),
            )

        # ── Зона риска (SL) и зона профита (TP) — полупрозрачные области ──
        x_fill = range(len(df_chart))
        
        if direction == "LONG":
            # Зона риска (Entry -> SL) - красная
            ax_price.fill_between(x_fill, stop_loss, entry, alpha=0.08, color=t["sl_color"])
            # Зона профита (Entry -> TP1) - зелёная
            ax_price.fill_between(x_fill, entry, tp1, alpha=0.06, color=t["tp_color"])
            if tp2:
                ax_price.fill_between(x_fill, tp1, tp2, alpha=0.04, color=t["tp2_color"])
        else:
            # Зона риска (Entry -> SL) - красная
            ax_price.fill_between(x_fill, entry, stop_loss, alpha=0.08, color=t["sl_color"])
            # Зона профита (Entry -> TP1) - зелёная
            ax_price.fill_between(x_fill, tp1, entry, alpha=0.06, color=t["tp_color"])
            if tp2:
                ax_price.fill_between(x_fill, tp2, tp1, alpha=0.04, color=t["tp2_color"])

        # ── Водяной знак ──
        ax_price.text(
            0.5, 0.5, "SMART TRADER BOT",
            transform=ax_price.transAxes,
            fontsize=28, fontweight='bold',
            color=t["watermark_color"],
            ha='center', va='center',
            alpha=0.15,
            zorder=0,
        )

        # ── Легенда ──
        legend_elements = [
            Line2D([0], [0], color=t["entry_color"], linewidth=2, label=f'Entry: {entry:{price_fmt}}'),
            Line2D([0], [0], color=t["sl_color"], linewidth=2, linestyle='--', label=f'Stop Loss: {stop_loss:{price_fmt}}'),
            Line2D([0], [0], color=t["tp_color"], linewidth=2, label=f'TP1: {tp1:{price_fmt}}'),
        ]
        if tp2:
            legend_elements.append(Line2D([0], [0], color=t["tp2_color"], linewidth=1.5, label=f'TP2: {tp2:{price_fmt}}'))
        if tp3:
            legend_elements.append(Line2D([0], [0], color=t["tp3_color"], linewidth=1.5, linestyle='-.', label=f'TP3: {tp3:{price_fmt}}'))

        ax_price.legend(
            handles=legend_elements,
            loc='upper left',
            fontsize=8,
            framealpha=0.85,
            facecolor=t["bg_color"],
            edgecolor=t["edge_color"],
            labelcolor=t["text_color"],
        )

        # ── Сохраняем в буфер ──
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                    facecolor=t["bg_color"], edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        
        logger.info("Chart generated for %s (%s theme, %d candles)", symbol, theme, len(df_chart))
        return buf.getvalue()

    except Exception as e:
        logger.error("Failed to generate chart for %s: %s", symbol, e, exc_info=True)
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
