import pandas as pd
import numpy as np
from .base import BaseStrategy, StrategySignal, StrategyResult

class VolumeAnalysisStrategy(BaseStrategy):
    name = "Volume Analysis"
    short_name = "volume_analysis"
    emoji = "📊"

    def analyze(self, df: pd.DataFrame, symbol: str, timeframe: str) -> StrategyResult:
        df = df.copy()
        if "atr" not in df.columns:
            df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
            df['atr'] = df['tr'].rolling(14).mean()
            
        atr_value = df['atr'].iloc[-1] if not pd.isna(df['atr'].iloc[-1]) else df['close'].iloc[-1] * 0.01
        
        direction = "NEUTRAL"
        confidence = 0
        details = []
        
        # Анализ климакса объема (climactic volume)
        if 'volume' in df.columns:
            vol_sma = df['volume'].rolling(20).mean()
            current_vol = df['volume'].iloc[-1]
            prev_vol = df['volume'].iloc[-2]
            
            is_climactic = current_vol > vol_sma.iloc[-1] * 3 or prev_vol > vol_sma.iloc[-2] * 3
            
            if is_climactic:
                details.append("🌪 Зафиксирован кульминационный объем (Climactic Volume)!")
                
                # Если объем огромен, а свеча падающая - возможен откуп (Long)
                if df['close'].iloc[-1] < df['open'].iloc[-1]:
                    direction = "LONG"
                    confidence = 3
                    details.append("🟢 Кульминация продаж, вероятность разворота вверх (Stopping Volume).")
                else:
                    direction = "SHORT"
                    confidence = 3
                    details.append("🔴 Кульминация покупок, вероятность разворота вниз (Exhaustion Volume).")
                    
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
            signal = StrategySignal(direction="NEUTRAL", confidence=0, details=["Объемы в норме, нет аномалий и кульминаций."])

        return self._make_result(signal, details)
