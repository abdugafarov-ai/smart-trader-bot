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

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """
        Нормализует тикер брокера к каноническому виду (например, XAUUSDm, XAUUSD.pro, GOLD -> XAUUSD).
        """
        if not symbol:
            return ""
        sym = symbol.upper().strip().replace("/", "").replace("=X", "")
        if sym in ("XAUUSD", "GOLD", "GOLDM") or sym.startswith("XAUUSD") or sym.startswith("GOLD."):
            return "XAUUSD"
        forex_set = set(KRAKEN_SYMBOL_MAP.keys()) | set(getattr(config, "FOREX_PAIRS", {}).keys())
        if len(sym) > 6 and sym[:6] in forex_set:
            return sym[:6]
        return sym

    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        return f"{self.normalize_symbol(symbol)}_{timeframe.upper()}"

    async def fetch_ohlcv(self, symbol: str, timeframe: str = 'M15', limit: int = 300) -> pd.DataFrame:
        """
        Получает исторические закрытые свечи OHLCV из брокерских и межбанковских источников:
        1. Проверяет кэш
        2. Проверяет файлы закрытых баров MetaTrader 5
        3. Запрашивает через институциональные биржевые API (Binance / Kraken) — кроме XAUUSD
        """
        sym_clean = self.normalize_symbol(symbol)
        cache_key = self._get_cache_key(sym_clean, timeframe)

        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if time.time() - cache_entry['timestamp'] < self._get_cache_ttl(timeframe):
                return cache_entry['data'].copy()

        df = None

        # 0. Синтетический индекс доллара (DXY / USDX)
        if sym_clean in ("DXY", "USDX", "DX"):
            return await self.fetch_dxy(timeframe, limit)

        # 1. Проверяем наличие баров, экспортированных советником MT5 напрямую
        df = self._read_mt5_bars(sym_clean, timeframe, limit, closed_only=True)

        # 2. Если баров MT5 нет — получаем через CCXT (строго запрещено для XAUUSD)
        if df is None or df.empty or len(df) < 20:
            try:
                forex_set = set(KRAKEN_SYMBOL_MAP.keys()) | set(getattr(config, "FOREX_PAIRS", {}).keys())
                if config.is_crypto(sym_clean):
                    df = await self._fetch_crypto_binance(sym_clean, timeframe, limit)
                elif sym_clean == "XAUUSD":
                    # Hard Rule: For live trading and signals on XAUUSD, never mix or fallback to PAXG!
                    logger.warning("MT5 broker candles unavailable for XAUUSD %s. Refusing PAXG fallback (NO TRADE).", timeframe)
                    return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                elif sym_clean in forex_set:
                    df = await self._fetch_forex_kraken(sym_clean, timeframe, limit)
                else:
                    logger.warning("Unsupported or unknown symbol %s (normalized: %s).", symbol, sym_clean)
                    return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            except Exception as ex:
                logger.warning("CCXT fetch error for %s %s: %s", sym_clean, timeframe, ex)

        if df is not None and not df.empty:
            # Closed historical bars remain strictly immutable to prevent repainting / false confirmation.
            self._cache[cache_key] = {
                'timestamp': time.time(),
                'data': df.copy()
            }
            return df.copy()

        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    def _read_mt5_bars(self, symbol: str, timeframe: str, limit: int = 300, closed_only: bool = True) -> Optional[pd.DataFrame]:
        """Читает и валидирует реальные бары брокера, экспортированные советником MT5."""
        if not os.path.exists(MT5_FILES_DIR):
            return None
        sym_norm = self.normalize_symbol(symbol)
        fname = f"candles_{sym_norm}_{timeframe}.csv"
        fpath = os.path.join(MT5_FILES_DIR, fname)
        if not os.path.exists(fpath):
            return None
        try:
            df = pd.read_csv(fpath)
            required_cols = {'open', 'high', 'low', 'close'}
            if df.empty or not required_cols.issubset(df.columns):
                return None
            if 'volume' not in df.columns:
                df['volume'] = 100.0
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(100.0)
            df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
            if df.empty:
                return None

            # Validate OHLC integrity invariants (no negative/zero prices, high >= max(open, close), low <= min(open, close))
            valid_ohlc = (
                (df['open'] > 0) &
                (df['high'] > 0) &
                (df['low'] > 0) &
                (df['close'] > 0) &
                (df['high'] >= df['low']) &
                (df['high'] >= df['open']) &
                (df['high'] >= df['close']) &
                (df['low'] <= df['open']) &
                (df['low'] <= df['close'])
            )
            df = df.loc[valid_ohlc].reset_index(drop=True)
            if df.empty:
                return None

            # Deduplicate and sort chronologically if timestamp column exists
            if 'timestamp' in df.columns:
                dt_parsed = pd.to_datetime(df['timestamp'], errors='coerce')
                if dt_parsed.notna().any():
                    df = (
                        df.loc[dt_parsed.notna()]
                        .assign(_dt=dt_parsed[dt_parsed.notna()])
                        .drop_duplicates(subset=['_dt'], keep='last')
                        .sort_values('_dt')
                        .drop(columns=['_dt'])
                        .reset_index(drop=True)
                    )

            # If closed_only is True, drop the last row which is the current unclosed forming candle in MT5 export
            if closed_only and len(df) > 1:
                df = df.iloc[:-1].reset_index(drop=True)
            df = df.tail(limit).reset_index(drop=True)
            if not df.empty:
                df.attrs['is_broker_time'] = True
                return df
            return None
        except Exception as e:
            logger.debug("Failed reading MT5 bars file %s: %s", fpath, e)
            return None

    async def _fetch_crypto_binance(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """Получает закрытые крипто-свечи с Binance через CCXT."""
        tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '15m')
        pair = f"{symbol[:-4]}/{symbol[-4:]}" if symbol.endswith('USDT') else symbol
        ohlcv = await asyncio.to_thread(self.binance.fetch_ohlcv, pair, tf, limit=limit + 1)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        if len(df) > 1:
            df = df.iloc[:-1].reset_index(drop=True)
        return df.tail(limit).reset_index(drop=True)

    async def _fetch_forex_kraken(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """
        Получает закрытые Forex свечи через CCXT Kraken.
        Kraken предоставляет бесплатный, высокоточный публичный поток котировок без ключей.
        """
        tf = config.TIMEFRAME_MAP_CCXT.get(timeframe, '15m')
        kraken_pair = KRAKEN_SYMBOL_MAP.get(symbol)
        if not kraken_pair:
            kraken_pair = f"{symbol[:3]}/{symbol[3:]}"

        ohlcv = await asyncio.to_thread(self.kraken.fetch_ohlcv, kraken_pair, tf, limit=limit + 1)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        if len(df) > 1:
            df = df.iloc[:-1].reset_index(drop=True)
        return df.tail(limit).reset_index(drop=True)

    def get_dxy_bars_sync(
        self,
        timeframe: str = 'H1',
        limit: int = 150,
        df_eur_override: Optional[pd.DataFrame] = None
    ) -> pd.DataFrame:
        """
        Синхронно рассчитывает синтетический индекс доллара (DXY) по формуле ICE
        только при наличии реальных синхронизированных баров всех валютных пар.
        Никаких искусственных констант-заглушек при отсутствии данных.
        """
        empty_df = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        try:
            if df_eur_override is not None and not df_eur_override.empty:
                df_eur = df_eur_override.copy()
            else:
                df_eur = self._read_mt5_bars("EURUSD", timeframe, limit=limit)

            df_gbp = self._read_mt5_bars("GBPUSD", timeframe, limit=limit)
            df_jpy = self._read_mt5_bars("USDJPY", timeframe, limit=limit)
            df_chf = self._read_mt5_bars("USDCHF", timeframe, limit=limit)
            df_cad = self._read_mt5_bars("USDCAD", timeframe, limit=limit)

            # Strictly require real data for all major DXY basket components (no fake constant prices)
            for comp_df in (df_eur, df_gbp, df_jpy, df_chf, df_cad):
                if comp_df is None or comp_df.empty:
                    return empty_df

            # Align on timestamp
            prepared = []
            for suffix, d in [('eur', df_eur), ('gbp', df_gbp), ('jpy', df_jpy), ('chf', df_chf), ('cad', df_cad)]:
                d_copy = d.copy()
                if 'timestamp' in d_copy.columns:
                    dt_s = pd.to_datetime(d_copy['timestamp'], errors='coerce')
                elif isinstance(d_copy.index, pd.DatetimeIndex):
                    dt_s = pd.to_datetime(d_copy.index, errors='coerce')
                else:
                    dt_s = pd.to_datetime(d_copy.iloc[:, 0], errors='coerce')
                if getattr(dt_s.dt, 'tz', None) is not None:
                    dt_s = dt_s.dt.tz_localize(None)
                d_copy['dt'] = dt_s
                d_copy = d_copy.dropna(subset=['dt', 'open', 'high', 'low', 'close'])
                d_copy = d_copy[['dt', 'open', 'high', 'low', 'close']].rename(
                    columns={
                        'open': f'open_{suffix}',
                        'high': f'high_{suffix}',
                        'low': f'low_{suffix}',
                        'close': f'close_{suffix}',
                    }
                )
                prepared.append(d_copy)

            m = prepared[0]
            for other in prepared[1:]:
                m = pd.merge(m, other, on='dt', how='inner')

            if m.empty:
                return empty_df

            # Optional USDSEK if exported by broker, otherwise re-normalize weights across the 5 available majors (95.8% of DXY basket)
            df_sek = self._read_mt5_bars("USDSEK", timeframe, limit=limit)
            if df_sek is not None and not df_sek.empty and 'timestamp' in df_sek.columns:
                df_sek_c = df_sek.copy()
                dt_sek = pd.to_datetime(df_sek_c['timestamp'], errors='coerce')
                if getattr(dt_sek.dt, 'tz', None) is not None:
                    dt_sek = dt_sek.dt.tz_localize(None)
                df_sek_c['dt'] = dt_sek
                df_sek_c = df_sek_c[['dt', 'open', 'high', 'low', 'close']].rename(
                    columns={'open': 'open_sek', 'high': 'high_sek', 'low': 'low_sek', 'close': 'close_sek'}
                )
                m_sek = pd.merge(m, df_sek_c, on='dt', how='inner')
                if not m_sek.empty:
                    m = m_sek
                    k = 50.14348112
                    dxy = pd.DataFrame()
                    dxy['timestamp'] = m['dt']
                    dxy['open'] = k * (m['open_eur'] ** -0.576) * (m['open_jpy'] ** 0.136) * (m['open_gbp'] ** -0.119) * (m['open_cad'] ** 0.091) * (m['open_sek'] ** 0.042) * (m['open_chf'] ** 0.036)
                    dxy['close'] = k * (m['close_eur'] ** -0.576) * (m['close_jpy'] ** 0.136) * (m['close_gbp'] ** -0.119) * (m['close_cad'] ** 0.091) * (m['close_sek'] ** 0.042) * (m['close_chf'] ** 0.036)
                    dxy['high'] = k * (m['low_eur'] ** -0.576) * (m['high_jpy'] ** 0.136) * (m['low_gbp'] ** -0.119) * (m['high_cad'] ** 0.091) * (m['high_sek'] ** 0.042) * (m['high_chf'] ** 0.036)
                    dxy['low'] = k * (m['high_eur'] ** -0.576) * (m['low_jpy'] ** 0.136) * (m['high_gbp'] ** -0.119) * (m['low_cad'] ** 0.091) * (m['low_sek'] ** 0.042) * (m['low_chf'] ** 0.036)
                    dxy['volume'] = 100.0
                    return dxy.tail(limit).reset_index(drop=True)

            # 5-major re-normalized DXY basket (EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, CHF 3.6% = 95.8% total weight)
            w_scale = 1.0 / 0.958
            w_eur = -0.576 * w_scale
            w_jpy = 0.136 * w_scale
            w_gbp = -0.119 * w_scale
            w_cad = 0.091 * w_scale
            w_chf = 0.036 * w_scale
            k5 = 50.14348112
            dxy = pd.DataFrame()
            dxy['timestamp'] = m['dt']
            dxy['open'] = k5 * (m['open_eur'] ** w_eur) * (m['open_jpy'] ** w_jpy) * (m['open_gbp'] ** w_gbp) * (m['open_cad'] ** w_cad) * (m['open_chf'] ** w_chf)
            dxy['close'] = k5 * (m['close_eur'] ** w_eur) * (m['close_jpy'] ** w_jpy) * (m['close_gbp'] ** w_gbp) * (m['close_cad'] ** w_cad) * (m['close_chf'] ** w_chf)
            dxy['high'] = k5 * (m['low_eur'] ** w_eur) * (m['high_jpy'] ** w_jpy) * (m['low_gbp'] ** w_gbp) * (m['high_cad'] ** w_cad) * (m['high_chf'] ** w_chf)
            dxy['low'] = k5 * (m['high_eur'] ** w_eur) * (m['low_jpy'] ** w_jpy) * (m['high_gbp'] ** w_gbp) * (m['low_cad'] ** w_cad) * (m['low_chf'] ** w_chf)
            dxy['volume'] = 100.0
            return dxy.tail(limit).reset_index(drop=True)
        except Exception as e:
            logger.error("Error synthesizing DXY: %s", e)
            return empty_df

    async def fetch_dxy(self, timeframe: str = 'H1', limit: int = 150) -> pd.DataFrame:
        """Асинхронная обертка для fetch_dxy."""
        return self.get_dxy_bars_sync(timeframe=timeframe, limit=limit)


