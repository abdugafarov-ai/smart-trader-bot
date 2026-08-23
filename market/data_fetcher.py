import asyncio
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
import yfinance as yf
import ccxt
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)


class DataFetcher:
    """Извлекает OHLCV данные с Binance (crypto) или Yahoo Finance (forex)."""

    @staticmethod
    def is_weekend() -> bool:
        """Проверяет, сейчас ли выходные (суббота/воскресенье) по UTC."""
        return datetime.now(ZoneInfo("UTC")).weekday() >= 5

    @staticmethod
    def get_weekend_note() -> str:
        """Возвращает предупреждение если сейчас выходные."""
        if DataFetcher.is_weekend():
            return (
                "\n⚠️ Сейчас выходные — Forex рынок закрыт.\n"
                "Данные отображаются за последний торговый день (пятница).\n"
                "Крипто-пары (BTCUSDT, ETHUSDT) обновляются 24/7.\n"
            )
        return ""
    
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
        })
        
    def _get_cache_ttl(self, timeframe: str) -> int:
        """Cache TTL in seconds based on timeframe."""
        if timeframe in ['M1', 'M5', 'M15', 'M30']:
            return 5 * 60  # 5 минут для интрадей
        return 30 * 60     # 30 минут для H4+
        
    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}_{timeframe}"
        
    async def fetch_ohlcv(self, symbol: str, timeframe: str = 'H4', limit: int = 200) -> pd.DataFrame:
        cache_key = self._get_cache_key(symbol, timeframe)
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < self._get_cache_ttl(timeframe):
                return cache_entry['data'].copy()
                
        try:
            if config.is_crypto(symbol):
                df = await self._fetch_ccxt(symbol, timeframe, limit)
            else:
                df = await self._fetch_yfinance(symbol, timeframe, limit)
                
            if df is not None and not df.empty:
                self._cache[cache_key] = {
                    'timestamp': time.time(),
                    'data': df
                }
                return df.copy()
                
        except Exception as e:
            logger.error(f"Ошибка получения данных для {symbol} {timeframe}: {e}")
            
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    async def _fetch_ccxt(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        ccxt_tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '4h')
        
        # CCXT использует формат 'BTC/USDT'
        ccxt_symbol = f"{symbol[:-4]}/{symbol[-4:]}" if symbol.endswith('USDT') else symbol
        
        def fetch():
            return self.exchange.fetch_ohlcv(ccxt_symbol, timeframe=ccxt_tf, limit=limit)
            
        ohlcv = await asyncio.to_thread(fetch)
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
        
    async def _fetch_yfinance(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        yf_symbol = config.get_yf_symbol(symbol)
        yf_tf = config.TIMEFRAME_MAP_YF.get(timeframe, '1h')
        
        fetch_limit = limit
        resample_needed = False
        
        if timeframe == 'H4':
            fetch_limit = limit * 4
            resample_needed = True
            
        def fetch():
            ticker = yf.Ticker(yf_symbol)
            # Для данных < 1d yfinance разрешает период до 730d для 1h, и до 60d для интрадей.
            period = '1y' if yf_tf in ['1h', '1d', '1wk'] else '1mo'
            return ticker.history(period=period, interval=yf_tf)
            
        df = await asyncio.to_thread(fetch)
        
        if df.empty:
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
        df = df.reset_index()
        time_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        df = df.rename(columns={
            time_col: 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })
        
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
        
        if resample_needed and timeframe == 'H4':
            df = df.set_index('timestamp')
            df = df.resample('4h').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna().reset_index()
            
        df = df.tail(limit).reset_index(drop=True)
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
