import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator, StochRSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import VolumeWeightedAveragePrice

from strategies.base import IndicatorResult

class TechnicalIndicators:
    """Класс для расчета технических индикаторов."""
    
    @staticmethod
    def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
        """Добавляет колонки с индикаторами в DataFrame."""
        df = df.copy()
        
        if df.empty or len(df) < 50:
            return df
            
        # Тренд
        df['ema_21'] = EMAIndicator(close=df['close'], window=21).ema_indicator()
        df['ema_50'] = EMAIndicator(close=df['close'], window=50).ema_indicator()
        df['ema_200'] = EMAIndicator(close=df['close'], window=200).ema_indicator()
        
        # Моментум
        df['rsi'] = RSIIndicator(close=df['close'], window=14).rsi()
        
        stoch_rsi = StochRSIIndicator(close=df['close'], window=14, smooth1=3, smooth2=3)
        df['stoch_rsi_k'] = stoch_rsi.stochrsi_k() * 100
        df['stoch_rsi_d'] = stoch_rsi.stochrsi_d() * 100
        
        # Волатильность
        df['atr'] = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14).average_true_range()
        
        bb = BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_middle'] = bb.bollinger_mavg()
        df['bb_lower'] = bb.bollinger_lband()
        
        # Объем
        df['volume_sma'] = df['volume'].rolling(window=20).mean()
        
        try:
            vwap = VolumeWeightedAveragePrice(
                high=df['high'], low=df['low'], close=df['close'], volume=df['volume'], window=14
            )
            df['vwap'] = vwap.volume_weighted_average_price()
        except Exception:
            df['vwap'] = 0.0
            
        return df

    @staticmethod
    def calculate(df: pd.DataFrame) -> IndicatorResult:
        """Рассчитывает индикаторы и возвращает структуру IndicatorResult."""
        if 'ema_21' not in df.columns:
            df = TechnicalIndicators.calculate_all(df)
            
        if df.empty or len(df) < 50:
            return IndicatorResult()
            
        last = df.iloc[-1]
        price = last['close']
        
        res = IndicatorResult()
        res.current_price = price
        
        # Тренд
        res.ema_21 = float(last['ema_21']) if pd.notna(last['ema_21']) else 0.0
        res.ema_50 = float(last['ema_50']) if pd.notna(last['ema_50']) else 0.0
        res.ema_200 = float(last['ema_200']) if pd.notna(last['ema_200']) else 0.0
        
        if price > res.ema_21 > res.ema_50:
            res.trend = "BULLISH"
        elif price < res.ema_21 < res.ema_50:
            res.trend = "BEARISH"
        else:
            res.trend = "NEUTRAL"
            
        if price > res.ema_200:
            res.price_vs_ema = "Цена выше EMA 200 (Глобальный восходящий тренд)"
        else:
            res.price_vs_ema = "Цена ниже EMA 200 (Глобальный нисходящий тренд)"
            
        # Моментум
        res.rsi = float(last['rsi']) if pd.notna(last['rsi']) else 50.0
        if res.rsi > 70:
            res.rsi_state = "перекуплен"
        elif res.rsi < 30:
            res.rsi_state = "перепродан"
        else:
            res.rsi_state = "нейтральный"
            
        res.stoch_rsi_k = float(last['stoch_rsi_k']) if pd.notna(last['stoch_rsi_k']) else 50.0
        res.stoch_rsi_d = float(last['stoch_rsi_d']) if pd.notna(last['stoch_rsi_d']) else 50.0
        
        if res.stoch_rsi_k > 80 and res.stoch_rsi_d > 80:
            res.stoch_rsi_state = "сильная перекупленность"
        elif res.stoch_rsi_k < 20 and res.stoch_rsi_d < 20:
            res.stoch_rsi_state = "сильная перепроданность"
        else:
            res.stoch_rsi_state = "нейтральное состояние"
            
        # Волатильность
        res.atr = float(last['atr']) if pd.notna(last['atr']) else 0.0
        res.atr_percent = (res.atr / price * 100) if price > 0 else 0.0
        
        res.bb_upper = float(last['bb_upper']) if pd.notna(last['bb_upper']) else 0.0
        res.bb_middle = float(last['bb_middle']) if pd.notna(last['bb_middle']) else 0.0
        res.bb_lower = float(last['bb_lower']) if pd.notna(last['bb_lower']) else 0.0
        
        if price >= res.bb_upper:
            res.bb_position = "пробой верхней границы"
        elif price <= res.bb_lower:
            res.bb_position = "пробой нижней границы"
        elif price > res.bb_middle:
            res.bb_position = "верхняя зона"
        elif price < res.bb_middle:
            res.bb_position = "нижняя зона"
        else:
            res.bb_position = "на средней линии"
            
        # Объем
        res.vwap = float(last['vwap']) if pd.notna(last['vwap']) else 0.0
        if res.vwap > 0:
            if price > res.vwap:
                res.price_vs_vwap = "выше VWAP"
            else:
                res.price_vs_vwap = "ниже VWAP"
        else:
            res.price_vs_vwap = "не рассчитан"
            
        res.volume_current = float(last['volume']) if pd.notna(last['volume']) else 0.0
        res.volume_sma = float(last['volume_sma']) if pd.notna(last['volume_sma']) else 0.0
        
        if res.volume_sma > 0:
            res.volume_ratio = res.volume_current / res.volume_sma
        else:
            res.volume_ratio = 1.0
            
        if res.volume_ratio > 2.0:
            res.volume_state = "очень высокий"
        elif res.volume_ratio > 1.2:
            res.volume_state = "повышенный"
        elif res.volume_ratio < 0.5:
            res.volume_state = "очень низкий"
        else:
            res.volume_state = "нормальный"
            
        return res
