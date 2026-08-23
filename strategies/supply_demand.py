import pandas as pd
import numpy as np
from .base import BaseStrategy, StrategySignal, StrategyResult

class SupplyDemandStrategy(BaseStrategy):
    name = "Supply & Demand"
    short_name = "supply_demand"
    emoji = "⚖️"

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        df = df.copy()
        if "atr" not in df.columns:
            df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
            df['atr'] = df['tr'].rolling(14).mean()
            
        atr_value = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else df['close'].iloc[-1] * 0.01
        
        # Поиск импульсных движений (тело свечи > 1.5 * ATR)
        df['body'] = abs(df['close'] - df['open'])
        df['impulse_bullish'] = (df['close'] > df['open']) & (df['body'] > 1.5 * df['atr'])
        df['impulse_bearish'] = (df['close'] < df['open']) & (df['body'] > 1.5 * df['atr'])
        
        direction = "NEUTRAL"
        confidence = 0
        details = []
        
        # Простая эвристика: ищем недавнюю импульсную свечу и базовую перед ней
        last_bullish_idx = df[df['impulse_bullish']].index.max()
        last_bearish_idx = df[df['impulse_bearish']].index.max()
        
        current_price = df['close'].iloc[-1]
        
        demand_zone = False
        supply_zone = False
        
        if pd.notna(last_bullish_idx) and df.index.get_loc(last_bullish_idx) > len(df) - 10:
            # Потенциальная зона спроса (Demand)
            base_candle = df.iloc[df.index.get_loc(last_bullish_idx) - 1]
            if base_candle['close'] < base_candle['open']: # Базовая свеча была падающей
                zone_high = base_candle['high']
                zone_low = base_candle['low']
                if current_price <= zone_high and current_price >= zone_low:
                    demand_zone = True
                    details.append(f"🟢 Цена в зоне спроса (Demand Zone): {zone_low:.2f} - {zone_high:.2f}")
                    
        if pd.notna(last_bearish_idx) and df.index.get_loc(last_bearish_idx) > len(df) - 10:
            # Потенциальная зона предложения (Supply)
            base_candle = df.iloc[df.index.get_loc(last_bearish_idx) - 1]
            if base_candle['close'] > base_candle['open']: # Базовая свеча была растущей
                zone_high = base_candle['high']
                zone_low = base_candle['low']
                if current_price <= zone_high and current_price >= zone_low:
                    supply_zone = True
                    details.append(f"🔴 Цена в зоне предложения (Supply Zone): {zone_low:.2f} - {zone_high:.2f}")

        if demand_zone:
            direction = "LONG"
            confidence = 4
            details.append("⚡ Отскок от зоны спроса, возможно формирование дна")
        elif supply_zone:
            direction = "SHORT"
            confidence = 4
            details.append("⚡ Отскок от зоны предложения, возможно формирование вершины")
            
        if direction != "NEUTRAL":
            entry, sl, tp1, tp2 = self._calc_entry_sl_tp(df, direction, atr_value)
            
            rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0.0
            details.append(f"📍 Вход: {entry:.5f} | 🛑 Стоп: {sl:.5f} | 🎯 Цель: {tp1:.5f} | R:R = 1:{rr:.1f}")
            
            signal = StrategySignal(
                direction=direction, confidence=confidence,
                entry=entry, stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=rr, details=details
            )
        else:
            signal = StrategySignal(direction="NEUTRAL", confidence=0, details=["Нет четких сигналов от зон Supply/Demand"])

        return self._make_result(signal, details)
