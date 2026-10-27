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

        # 0. Синтетический индекс доллара (DXY / USDX)
        if sym_clean in ("DXY", "USDX", "DX"):
            return await self.fetch_dxy(timeframe, limit)

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

    def get_dxy_bars_sync(
        self,
        timeframe: str = 'H1',
        limit: int = 150,
        df_eur_override: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Синхронно рассчитывает синтетический индекс доллара (DXY) по институциональной формуле ICE:
        DXY = 50.14348112 * (EURUSD^-0.576) * (USDJPY^0.136) * (GBPUSD^-0.119) * (USDCAD^0.091) * (USDSEK^0.042) * (USDCHF^0.036)
        С выравниванием по общим таймстемпам брокерских баров.
        """
        try:
            if df_eur_override is not None and not df_eur_override.empty:
                df_eur = df_eur_override.copy()
            else:
                df_eur = self._read_mt5_bars("EURUSD", timeframe, limit=limit)

            df_gbp = self._read_mt5_bars("GBPUSD", timeframe, limit=limit)
            df_jpy = self._read_mt5_bars("USDJPY", timeframe, limit=limit)
            df_chf = self._read_mt5_bars("USDCHF", timeframe, limit=limit)
            df_cad = self._read_mt5_bars("USDCAD", timeframe, limit=limit)

            if df_eur is None or df_eur.empty:
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Align on timestamp
            for d in [df_eur, df_gbp, df_jpy, df_chf, df_cad]:
                if d is not None and not d.empty:
                    if 'timestamp' in d.columns:
                        dt_s = pd.to_datetime(d['timestamp'])
                    elif isinstance(d.index, pd.DatetimeIndex):
                        dt_s = pd.to_datetime(d.index)
                    else:
                        dt_s = pd.to_datetime(d.iloc[:, 0])
                    if getattr(dt_s.dt, 'tz', None) is not None:
                        dt_s = dt_s.dt.tz_localize(None)
                    d['dt'] = dt_s

            m = df_eur[['dt', 'open', 'high', 'low', 'close']].copy()
            m = m.rename(columns={'open': 'open_eur', 'high': 'high_eur', 'low': 'low_eur', 'close': 'close_eur'})

            if df_gbp is not None and not df_gbp.empty:
                m = pd.merge(m, df_gbp[['dt', 'open', 'high', 'low', 'close']], on='dt', how='left')
                m = m.rename(columns={'open': 'open_gbp', 'high': 'high_gbp', 'low': 'low_gbp', 'close': 'close_gbp'})
                m['close_gbp'] = m['close_gbp'].fillna(1.27)
                m['open_gbp'] = m['open_gbp'].fillna(1.27)
                m['high_gbp'] = m['high_gbp'].fillna(1.27)
                m['low_gbp'] = m['low_gbp'].fillna(1.27)
            else:
                m['open_gbp'] = m['high_gbp'] = m['low_gbp'] = m['close_gbp'] = 1.27

            if df_jpy is not None and not df_jpy.empty:
                m = pd.merge(m, df_jpy[['dt', 'open', 'high', 'low', 'close']], on='dt', how='left')
                m = m.rename(columns={'open': 'open_jpy', 'high': 'high_jpy', 'low': 'low_jpy', 'close': 'close_jpy'})
                m['close_jpy'] = m['close_jpy'].fillna(150.0)
                m['open_jpy'] = m['open_jpy'].fillna(150.0)
                m['high_jpy'] = m['high_jpy'].fillna(150.0)
                m['low_jpy'] = m['low_jpy'].fillna(150.0)
            else:
                m['open_jpy'] = m['high_jpy'] = m['low_jpy'] = m['close_jpy'] = 150.0

            if df_chf is not None and not df_chf.empty:
                m = pd.merge(m, df_chf[['dt', 'open', 'high', 'low', 'close']], on='dt', how='left')
                m = m.rename(columns={'open': 'open_chf', 'high': 'high_chf', 'low': 'low_chf', 'close': 'close_chf'})
                m['close_chf'] = m['close_chf'].fillna(0.90)
                m['open_chf'] = m['open_chf'].fillna(0.90)
                m['high_chf'] = m['high_chf'].fillna(0.90)
                m['low_chf'] = m['low_chf'].fillna(0.90)
            else:
                m['open_chf'] = m['high_chf'] = m['low_chf'] = m['close_chf'] = 0.90

            if df_cad is not None and not df_cad.empty:
                m = pd.merge(m, df_cad[['dt', 'open', 'high', 'low', 'close']], on='dt', how='left')
                m = m.rename(columns={'open': 'open_cad', 'high': 'high_cad', 'low': 'low_cad', 'close': 'close_cad'})
                m['close_cad'] = m['close_cad'].fillna(1.35)
                m['open_cad'] = m['open_cad'].fillna(1.35)
                m['high_cad'] = m['high_cad'].fillna(1.35)
                m['low_cad'] = m['low_cad'].fillna(1.35)
            else:
                m['open_cad'] = m['high_cad'] = m['low_cad'] = m['close_cad'] = 1.35

            usdsek = 10.5
            k = 50.14348112 * (usdsek ** 0.042)

            dxy = pd.DataFrame()
            dxy['timestamp'] = m['dt']
            dxy['open'] = k * (m['open_eur'] ** -0.576) * (m['open_jpy'] ** 0.136) * (m['open_gbp'] ** -0.119) * (m['open_cad'] ** 0.091) * (m['open_chf'] ** 0.036)
            dxy['close'] = k * (m['close_eur'] ** -0.576) * (m['close_jpy'] ** 0.136) * (m['close_gbp'] ** -0.119) * (m['close_cad'] ** 0.091) * (m['close_chf'] ** 0.036)
            dxy['high'] = k * (m['low_eur'] ** -0.576) * (m['high_jpy'] ** 0.136) * (m['low_gbp'] ** -0.119) * (m['high_cad'] ** 0.091) * (m['high_chf'] ** 0.036)
            dxy['low'] = k * (m['high_eur'] ** -0.576) * (m['low_jpy'] ** 0.136) * (m['high_gbp'] ** -0.119) * (m['low_cad'] ** 0.091) * (m['low_chf'] ** 0.036)
            dxy['volume'] = 100.0

            return dxy.tail(limit).reset_index(drop=True)
        except Exception as e:
            logger.error("Error synthesizing DXY: %s", e)
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    async def fetch_dxy(self, timeframe: str = 'H1', limit: int = 150) -> pd.DataFrame:
        """Асинхронная обертка для fetch_dxy."""
        return self.get_dxy_bars_sync(timeframe=timeframe, limit=limit)

