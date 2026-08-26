"""
Smart Trader Bot — Базовые интерфейсы стратегий.
Все стратегии наследуют BaseStrategy и возвращают StrategyResult.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ── Результат анализа индикаторов ─────────────────────────

@dataclass
class IndicatorResult:
    """Набор значений технических индикаторов."""

    # Trend
    ema_21: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    trend: str = "NEUTRAL"
    price_vs_ema: str = ""

    # Momentum
    rsi: float = 50.0
    rsi_state: str = "нейтральный"
    stoch_rsi_k: float = 50.0
    stoch_rsi_d: float = 50.0
    stoch_rsi_state: str = ""

    # Volatility
    atr: float = 0.0
    atr_percent: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_position: str = ""

    # Volume
    vwap: float = 0.0
    price_vs_vwap: str = ""
    volume_current: float = 0.0
    volume_sma: float = 0.0
    volume_ratio: float = 1.0
    volume_state: str = "нормальный"

    # Price
    current_price: float = 0.0


# ── Сигнал стратегии ──────────────────────────────────────

@dataclass
class StrategySignal:
    """Торговый сигнал от стратегии."""

    direction: str = "NEUTRAL"
    order_type: str = "BUY_LIMIT"   # BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP, BUY_NOW, SELL_NOW
    confidence: int = 0
    current_price: Optional[float] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_reward: Optional[float] = None
    details: list[str] = field(default_factory=list)


# ── Результат стратегии ───────────────────────────────────

@dataclass
class StrategyResult:
    """Полный результат анализа стратегии."""

    name: str = ""
    emoji: str = ""
    signal: StrategySignal = field(default_factory=StrategySignal)
    summary: str = ""
    details_text: str = ""


# ── Анализ одного таймфрейма ──────────────────────────────

@dataclass
class TimeframeAnalysis:
    """Результат анализа на одном таймфрейме."""

    timeframe: str = ""
    direction: str = "NEUTRAL"
    order_type: str = "BUY_LIMIT"
    confidence: int = 0
    strategies: list[StrategyResult] = field(default_factory=list)
    indicators: Optional[IndicatorResult] = None


# ── Мульти-таймфрейм результат ────────────────────────────

@dataclass
class MultiTFResult:
    """Агрегированный результат мульти-таймфрейм анализа."""

    symbol: str = ""
    tag_emoji: str = "🔥"
    tf_analyses: list[TimeframeAnalysis] = field(default_factory=list)

    # Общий вердикт
    overall_direction: str = "NEUTRAL"
    order_type: str = "BUY_LIMIT"   # BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP, BUY_NOW, SELL_NOW
    current_price: Optional[float] = None
    overall_stars: int = 0          # 1-5 итоговых звёзд
    tf_agreement: int = 0           # Сколько TF совпадают
    total_tfs: int = 0
    strategy_agreement: int = 0     # Сколько стратегий совпадают (лучший TF)
    total_strategies: int = 6

    # Торговые уровни
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_reward_1: Optional[float] = None
    risk_reward_2: Optional[float] = None
    pips_sl: Optional[float] = None
    pips_tp1: Optional[float] = None
    pips_tp2: Optional[float] = None

    # Детали для отображения
    strategy_verdicts: list[tuple[str, str, str]] = field(default_factory=list)
    # [(emoji, name, "✅ LONG" / "❌ нейтрально")]

    session_text: str = ""


# ── Полный результат анализа (обратная совместимость) ──────

@dataclass
class FullAnalysisResult:
    """Агрегированный результат (обратная совместимость)."""

    symbol: str = ""
    timeframe: str = ""
    indicators: Optional[IndicatorResult] = None
    strategies: list[StrategyResult] = field(default_factory=list)
    session_text: str = ""
    overall_direction: str = "NEUTRAL"
    overall_confidence: int = 0
    strategies_agreeing: int = 0
    total_strategies: int = 6
    entry_suggestion: Optional[float] = None
    stop_suggestion: Optional[float] = None
    tp1_suggestion: Optional[float] = None
    tp2_suggestion: Optional[float] = None
    rr_suggestion: Optional[float] = None


# ── Новость экономического календаря ──────────────────────

@dataclass
class EconomicEvent:
    """Событие из экономического календаря."""

    title: str = ""
    country: str = ""           # "USD", "EUR", etc.
    date_str: str = ""          # "2026-08-18"
    time_str: str = ""          # "15:30"
    impact: str = ""            # "High", "Medium", "Low"
    forecast: str = ""
    previous: str = ""
    actual: str = ""
    affected_pairs: list[str] = field(default_factory=list)
    minutes_until: int = 0      # Сколько минут до события


# ── Базовый класс стратегии ───────────────────────────────

class BaseStrategy(ABC):
    """Абстрактный базовый класс для всех торговых стратегий."""

    name: str = "Base Strategy"
    short_name: str = "base"
    emoji: str = "📊"

    @abstractmethod
    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> StrategyResult:
        ...

    def _make_result(
        self,
        signal: StrategySignal,
        details_lines: list[str],
    ) -> StrategyResult:
        """Вспомогательный метод для создания StrategyResult."""
        details_text = "\n".join(details_lines)
        direction_emoji = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
        confidence_stars = "⭐" * signal.confidence if signal.confidence > 0 else "—"

        summary = (
            f"{direction_emoji.get(signal.direction, '⚪')} "
            f"{signal.direction} | Уверенность: {confidence_stars}"
        )

        return StrategyResult(
            name=self.name,
            emoji=self.emoji,
            signal=signal,
            summary=summary,
            details_text=details_text,
        )

    @staticmethod
    def _calc_entry_sl_tp(
        df: pd.DataFrame,
        direction: str,
        atr_value: float,
    ) -> tuple[float, float, float, float]:
        """
        Рассчитывает Entry, SL, TP1, TP2 на основе ATR.
        Возвращает: (entry, stop_loss, tp1, tp2)
        """
        current = df.iloc[-1]
        price = current['close']

        if direction == "LONG":
            entry = price
            stop_loss = price - 1.5 * atr_value
            tp1 = price + 2.0 * atr_value
            tp2 = price + 3.0 * atr_value
        elif direction == "SHORT":
            entry = price
            stop_loss = price + 1.5 * atr_value
            tp1 = price - 2.0 * atr_value
            tp2 = price - 3.0 * atr_value
        else:
            return (price, price, price, price)

        return (entry, stop_loss, tp1, tp2)
