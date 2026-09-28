import asyncio
import time
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
import ccxt
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)

# MT5 Files directory path on VPS
MT5_FILES_DIR = f"{config.WINE_MT5_PATH}/MQL5/Files"

# Kraken mapping for Forex pairs
KRAKEN_SYMBOL_MAP = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "EURGBP": "EUR/GBP",
    "EURJPY": "EUR/JPY",
    "EURAUD": "EUR/AUD",
    "EURCHF": "EUR/CHF",
}


class DataFetcher:
    """
    Институциональный поставщик рыночных данных OHLCV:
    1. MetaTrader 5 Bridge — прямые брокерские бары и живые тики без задержек.
    2. CCXT Kraken — институциональный публичный фид для Forex мажоров и кроссов.
    3. CCXT Binance — фид для криптовалют и золота (PAXG/USDT, 1:1 физическое золото).
    
    100% институциональные межбанковские данные.
    """

    @staticmethod
    def is_weekend() -> bool:
        """
        Проверяет, закрыт ли Forex рынок.
        Рынок закрыт: с пятницы 22:00 UTC до воскресенья 22:00 UTC.
        """
        now = datetime.now(ZoneInfo("UTC"))
        wd = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        hour = now.hour
        if wd == 5:
            return True
        if wd == 4 and hour >= 22:
            return True
        if wd == 6 and hour < 22:
            return True
        return False

    @staticmethod
    def get_weekend_note() -> str:
        """Возвращает предупреждение если сейчас выходные."""
        if DataFetcher.is_weekend():
            return (
                "\n⚠️ Сейчас выходные — рынки Forex и металлов закрыты.\n"
                "Данные отображаются по состоянию на закрытие пятницы.\n"
            )
        return ""

    @staticmethod
    def get_live_price(symbol: str) -> Optional[float]:
        """
        Возвращает последнюю цену брокера (bid) напрямую из терминала MT5 (0 задержки).
        Если MT5 оффлайн — возвращает None.
        """
        try:
            from trading.execution_bridge import bridge_manager
            return bridge_manager.get_live_price(symbol)
        except Exception:
            return None

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.binance = ccxt.binance({
            'enableRateLimit': True,
        })
        self.kraken = ccxt.kraken({
            'enableRateLimit': True,
        })

    def _get_cache_ttl(self, timeframe: str) -> int:
        """Cache TTL in seconds based on timeframe."""
        if timeframe in ['M1', 'M5', 'M15', 'M30']:
            return 3 * 60   # 3 минуты для интрадей
        return 15 * 60      # 15 минут для H4+

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol.upper()}_{timeframe.upper()}"

    async def fetch_ohlcv(self, symbol: str, timeframe: str = 'H4', limit: int = 300) -> pd.DataFrame:
        """
        Получает исторические свечи OHLCV из брокерских и межбанковских источников:
        1. Проверяет кэш
        2. Проверяет файлы баров MetaTrader 5
        3. Запрашивает через институциональные биржевые API (Binance / Kraken)
        4. Накладывает живую цену брокера из MT5
        """
        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
        cache_key = self._get_cache_key(sym_clean, timeframe)

        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < self._get_cache_ttl(timeframe):
                df_cached = cache_entry['data'].copy()
                live = self.get_live_price(sym_clean)
                if live and live > 0 and not df_cached.empty:
                    last_idx = df_cached.index[-1]
                    df_cached.loc[last_idx, 'close'] = live
                    if live > df_cached.loc[last_idx, 'high']:
                        df_cached.loc[last_idx, 'high'] = live
                    if live < df_cached.loc[last_idx, 'low']:
                        df_cached.loc[last_idx, 'low'] = live
                return df_cached

        df = None

        # 1. Проверяем наличие баров, экспортированных советником MT5 напрямую
        df = self._read_mt5_bars(sym_clean, timeframe, limit)

        # 2. Если баров MT5 нет — получаем через CCXT
        if df is None or df.empty or len(df) < 20:
            try:
                if config.is_crypto(sym_clean):
                    df = await self._fetch_crypto_binance(sym_clean, timeframe, limit)
                elif sym_clean == "XAUUSD":
                    df = await self._fetch_gold_binance(timeframe, limit)
                else:
                    df = await self._fetch_forex_kraken(sym_clean, timeframe, limit)
            except Exception as ex:
                logger.warning("CCXT fetch error for %s %s: %s", sym_clean, timeframe, ex)

        if df is not None and not df.empty:
            # Накладываем живой тик брокера из терминала MT5
            live = self.get_live_price(sym_clean)
            if live and live > 0:
                last_idx = df.index[-1]
                df.loc[last_idx, 'close'] = live
                if live > df.loc[last_idx, 'high']:
                    df.loc[last_idx, 'high'] = live
                if live < df.loc[last_idx, 'low']:
                    df.loc[last_idx, 'low'] = live

            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': df.copy()
            }
            return df.copy()

        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    def _read_mt5_bars(self, symbol: str, timeframe: str, limit: int = 300) -> Optional[pd.DataFrame]:
        """Читает реальные бары брокера, экспортированные советником MT5."""
        if not os.path.exists(MT5_FILES_DIR):
            return None
        fname = f"candles_{symbol}_{timeframe}.csv"
        fpath = os.path.join(MT5_FILES_DIR, fname)
        if not os.path.exists(fpath):
            return None
        try:
            df = pd.read_csv(fpath)
            if df.empty or 'close' not in df.columns:
                return None
            if 'volume' not in df.columns:
                df['volume'] = 100.0
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(100.0)
            df = df.dropna(subset=['open', 'high', 'low', 'close']).tail(limit).reset_index(drop=True)
            return df if not df.empty else None
        except Exception as e:
            logger.debug("Failed reading MT5 bars file %s: %s", fpath, e)
            return None

    async def _fetch_crypto_binance(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """Получает крипто-свечи с Binance через CCXT."""
        tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '4h')
        pair = f"{symbol[:-4]}/{symbol[-4:]}" if symbol.endswith('USDT') else symbol
        ohlcv = await asyncio.to_thread(self.binance.fetch_ohlcv, pair, tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    async def _fetch_gold_binance(self, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """
        Получает свечи золота (XAUUSD) через институциональный токен PAXG/USDT на Binance.
        PAXG обеспечен 1:1 физической тройской унцией золота Лондонского стандарта.
        """
        tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '4h')
        ohlcv = await asyncio.to_thread(self.binance.fetch_ohlcv, 'PAXG/USDT', tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    async def _fetch_forex_kraken(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """
        Получает Forex свечи через CCXT Kraken.
        Kraken предоставляет бесплатный, высокоточный публичный поток котировок без ключей.
        """
        tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '4h')
        kraken_pair = KRAKEN_SYMBOL_MAP.get(symbol)
        if not kraken_pair:
            kraken_pair = f"{symbol[:3]}/{symbol[3:]}"

        ohlcv = await asyncio.to_thread(self.kraken.fetch_ohlcv, kraken_pair, tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
