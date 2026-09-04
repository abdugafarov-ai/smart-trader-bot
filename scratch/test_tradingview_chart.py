import io
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import Rectangle
from datetime import datetime, timedelta

def generate_tradingview_style_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    direction: str,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float = None,
    current_price: float = None,
    order_type: str = "BUY_LIMIT",
    stars: int = 4,
    last_n_candles: int = 45,
    future_padding_bars: int = 18,
) -> bytes:
    """
    Генерирует график в точном стиле TradingView:
    - Свечи занимают левые ~70% графика и НЕ доходят до правого края
    - Справа от текущей свечи расположен интерактивный бокс позиции (Long/Short Position tool)
    - Красная зона риска (SL) и зеленая зона профита (TP) проецируются ВПЕРЕД во времени
    - На правой ценовой шкале четкие цветные плашки: SL (красная), Entry (серая), TP (зеленая), Market (желтая)
    - Палитра TradingView Dark: фон #131722, сетка #1e222d, свечи #089981 / #f23645
    """
    df_chart = df.tail(last_n_candles).copy().reset_index(drop=True)
    n_candles = len(df_chart)
    
    if current_price is None:
        current_price = float(df_chart['close'].iloc[-1])
        
    # Формат цены (Forex 5 знаков, JPY 3 знака, Gold 2 знака)
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
    fig.patch.set_facecolor('#131722')
    ax.set_facecolor('#131722')

    # Настройка сетки
    ax.grid(True, color='#1e222d', linestyle='-', linewidth=0.8, alpha=0.7)
    ax.set_axisbelow(True)

    # Рисуем свечи
    candle_width = 0.58
    wick_width = 1.0

    for i in range(n_candles):
        row = df_chart.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        
        is_up = c >= o
        c_color = '#089981' if is_up else '#f23645'
        
        # Тень
        ax.plot([i, i], [l, h], color=c_color, linewidth=wick_width, zorder=2)
        
        # Тело
        body_bottom = min(o, c)
        body_height = max(abs(c - o), (h - l) * 0.01)  # чтобы доджи не исчезали
        
        rect = Rectangle(
            (i - candle_width / 2, body_bottom),
            candle_width, body_height,
            facecolor=c_color,
            edgecolor=c_color,
            linewidth=0.8,
            zorder=3
        )
        ax.add_patch(rect)

    # ── TradingView Position Tool Box (R:R Box) ──
    # Бокс начинается от текущей последней свечи и проецируется вправо в пустое пространство
    x_start = n_candles - 0.5
    box_width = future_padding_bars - 2
    x_end = x_start + box_width

    sl_color = '#f23645'
    tp_color = '#089981'
    entry_color = '#787b86'

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

    # Линия входа (горизонтальная через весь бокс)
    ax.plot([x_start, x_end], [entry, entry], color='#d1d4dc', linewidth=1.5, linestyle='-', zorder=5)
    # Верхняя и нижняя границы
    ax.plot([x_start, x_end], [stop_loss, stop_loss], color=sl_color, linewidth=1.2, linestyle='-', zorder=5)
    ax.plot([x_start, x_end], [tp1, tp1], color=tp_color, linewidth=1.2, linestyle='-', zorder=5)

    # Текст внутри бокса позиции (Target & Stop & R:R)
    box_center_x = x_start + box_width / 2
    if direction == "LONG":
        tp_text_y = entry + profit_height * 0.5
        sl_text_y = stop_loss + risk_height * 0.5
    else:
        tp_text_y = tp1 + profit_height * 0.5
        sl_text_y = entry + risk_height * 0.5

    ax.text(box_center_x, tp_text_y, f"Target: +{reward_pips:.1f} pips\nR:R = 1:{rr:.1f}",
            color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#089981', alpha=0.6, edgecolor='none'))

    ax.text(box_center_x, sl_text_y, f"Stop: -{risk_pips:.1f} pips",
            color='#ffffff', fontsize=8.5, fontweight='bold', ha='center', va='center', zorder=6,
            bbox=dict(boxstyle='round,pad=0.2', facecolor='#f23645', alpha=0.6, edgecolor='none'))

    # Линия текущей цены (Market Price)
    ax.axhline(current_price, color='#2962ff', linestyle='--', linewidth=0.9, alpha=0.7, zorder=3)

    # Границы по X: от -1 до n_candles + future_padding_bars
    total_x_span = n_candles + future_padding_bars
    ax.set_xlim(-1, total_x_span)

    # Границы по Y: с запасом
    all_y = list(df_chart['low']) + list(df_chart['high']) + [entry, stop_loss, tp1, current_price]
    if tp2:
        all_y.append(tp2)
    y_min, y_max = min(all_y), max(all_y)
    y_padding = (y_max - y_min) * 0.08
    ax.set_ylim(y_min - y_padding, y_max + y_padding)

    # Настройка осей и подписей
    ax.spines['top'].set_visible(False)
    ax.spines['bottom'].set_color('#2a2e39')
    ax.spines['left'].set_visible(False)
    ax.spines['right'].set_color('#2a2e39')

    # Ось цен справа
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(axis='y', colors='#787b86', labelsize=8.5, length=3)
    ax.tick_params(axis='x', colors='#787b86', labelsize=8, length=3)

    # Плашки цен на правой шкале (TradingView price tags)
    x_badge = total_x_span

    def add_price_badge(y_val, text, bg_color, text_color='#ffffff'):
        ax.text(
            x_badge, y_val, f" {text} ",
            color=text_color, fontsize=8.5, fontweight='bold',
            va='center', ha='left',
            bbox=dict(boxstyle='square,pad=0.25', facecolor=bg_color, edgecolor='none'),
            clip_on=False, zorder=10
        )

    add_price_badge(stop_loss, p_fmt.format(stop_loss), '#f23645')
    add_price_badge(entry, p_fmt.format(entry), '#5d606b')
    add_price_badge(tp1, p_fmt.format(tp1), '#089981')
    add_price_badge(current_price, p_fmt.format(current_price), '#2962ff')

    # Заголовок в стиле TradingView (верхний левый угол)
    last_row = df_chart.iloc[-1]
    title_text = (
        f"{symbol} · {timeframe} · SMART TRADER BOT    "
        f"O {p_fmt.format(last_row['open'])}  "
        f"H {p_fmt.format(last_row['high'])}  "
        f"L {p_fmt.format(last_row['low'])}  "
        f"C {p_fmt.format(last_row['close'])}"
    )
    ax.text(0.015, 0.965, title_text, transform=ax.transAxes,
            color='#d1d4dc', fontsize=9.5, fontweight='bold', va='top', ha='left')

    dir_label = "🟢 LONG POSITION" if direction == "LONG" else "🔴 SHORT POSITION"
    sub_text = f"{dir_label} | {order_type.replace('_', ' ')} | R:R 1:{rr:.1f} | {'★' * stars}"
    ax.text(0.015, 0.915, sub_text, transform=ax.transAxes,
            color='#787b86', fontsize=8.5, va='top', ha='left')

    # Водяной знак TradingView внизу слева
    ax.text(0.015, 0.03, "17 TradingView", transform=ax.transAxes,
            color='#2a2e39', fontsize=12, fontweight='bold', va='bottom', ha='left')

    # Метки дат на оси X (берем каждые 8-10 свечей)
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
        ax.set_xticklabels(x_labels, rotation=0, ha='center', fontsize=7.5, color='#787b86')
    else:
        ax.set_xticks([])

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=140, bbox_inches='tight', facecolor='#131722')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# Тестовый прогон на реальных данных
if __name__ == '__main__':
    import asyncio
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from market.data_fetcher import DataFetcher

    async def main():
        fetcher = DataFetcher()
        df = await fetcher.fetch_ohlcv("EURUSD", "H1", limit=60)
        curr = float(df['close'].iloc[-1])
        entry = curr
        sl = curr + 0.00180
        tp = curr - 0.00450
        
        img_bytes = generate_tradingview_style_chart(
            df=df,
            symbol="EURUSD",
            timeframe="1h",
            direction="SHORT",
            entry=entry,
            stop_loss=sl,
            tp1=tp,
            current_price=curr,
            order_type="SELL_LIMIT",
            stars=5,
            last_n_candles=45,
            future_padding_bars=18
        )
        with open("scratch/test_tradingview_chart.png", "wb") as f:
            f.write(img_bytes)
        print("Chart saved to scratch/test_tradingview_chart.png, size:", len(img_bytes))

    asyncio.run(main())
