"""
Smart Trader Bot — Equity Curve Generator.
Генерирует институциональный график кривой капитала (Equity Curve) в стиле Wall Street / Bloomberg.
"""

import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def generate_equity_curve_chart(
    equity_points: list[float],
    title: str = "INSTITUTIONAL EQUITY CURVE",
    symbol: str = "PORTFOLIO",
    total_pnl: float = 0.0,
    win_rate: float = 0.0,
    profit_factor: float = 0.0,
    max_dd: float = 0.0
) -> bytes:
    """Генерирует PNG изображение кривой капитала."""
    if not equity_points or len(equity_points) < 2:
        equity_points = [0.0, 10.0, 25.0, 15.0, 40.0, 65.0]

    # Стилистика Wall Street Dark
    bg_color = "#0d1117"
    panel_color = "#161b22"
    grid_color = "#21262d"
    text_color = "#e6edf3"
    line_color = "#2f81f7"  # Bloomberg Blue
    fill_color = "#238636" if equity_points[-1] >= 0 else "#da3633"

    fig, ax = plt.subplots(figsize=(10, 5), dpi=140, facecolor=bg_color)
    ax.set_facecolor(panel_color)

    x = list(range(len(equity_points)))
    y = equity_points

    # Основная линия кривой капитала
    ax.plot(x, y, color=line_color, linewidth=2.2, label="Equity (Pips)", zorder=4)

    # Заливка под графиком
    ax.fill_between(x, y, 0, color=fill_color, alpha=0.15, zorder=3)

    # Нулевая горизонтальная линия
    ax.axhline(0, color="#8b949e", linestyle="--", linewidth=1.0, alpha=0.6, zorder=2)

    # Сетка
    ax.grid(True, linestyle="--", linewidth=0.5, color=grid_color, alpha=0.8, zorder=1)

    # Настройки осей
    ax.tick_params(colors="#8b949e", labelsize=9)
    ax.set_xlabel("Trades Count", color="#8b949e", fontsize=10, labelpad=8)
    ax.set_ylabel("Cumulative PnL (Pips)", color="#8b949e", fontsize=10, labelpad=8)

    for spine in ax.spines.values():
        spine.set_color("#30363d")

    # Заголовок
    main_title = f"{title} | {symbol}"
    ax.set_title(main_title, fontsize=12, fontweight="bold", color=text_color, pad=12)

    # Информационный бейдж статистики
    stats_text = (
        f"Net PnL: {total_pnl:+.1f} pips\n"
        f"Win Rate: {win_rate:.1f}%\n"
        f"Profit Factor: {profit_factor:.2f}\n"
        f"Max Drawdown: -{max_dd:.1f} pips"
    )
    ax.text(
        0.03, 0.93, stats_text,
        transform=ax.transAxes,
        fontsize=9,
        verticalalignment='top',
        color=text_color,
        bbox=dict(boxstyle='round,pad=0.5', facecolor=bg_color, edgecolor='#30363d', alpha=0.9),
        zorder=5
    )

    # Водяной знак
    fig.text(0.5, 0.45, "SMART TRADER BOT",
             fontsize=26, color="#8b949e",
             ha="center", va="center", alpha=0.07, fontweight="bold")

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=140, facecolor=fig.get_facecolor(), bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
