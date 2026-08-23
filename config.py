"""
Smart Trader Bot — Configuration
Загрузка переменных окружения и глобальных настроек.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загрузка .env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)


# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# ── Администратор бота (только он одобряет заявки) ────────
_admin_raw = os.getenv("ADMIN_ID", "")
ADMIN_ID: int = int(_admin_raw) if _admin_raw.isdigit() else 0

# ── Биржа ─────────────────────────────────────────────────
EXCHANGE: str = os.getenv("EXCHANGE", "binance")

# ── Часовой пояс ──────────────────────────────────────────
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Tashkent")

# ── Таймфрейм по умолчанию ────────────────────────────────
DEFAULT_TIMEFRAME: str = os.getenv("DEFAULT_TIMEFRAME", "H4")

# ── Торговые пары по категориям ───────────────────────────
PAIRS_MAJORS: list[str] = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
]
PAIRS_CROSSES: list[str] = [
    "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD",
    "EURCHF", "CADJPY", "AUDCAD", "AUDNZD",
]
PAIRS_COMMODITIES: list[str] = [
    "XAUUSD", "XAGUSD",
]
PAIRS_INDICES: list[str] = [
    "US30", "NAS100", "SPX500", "DE40",
]
PAIRS_CRYPTO: list[str] = [
    "BTCUSDT", "ETHUSDT",
]

ALL_PAIRS: list[str] = (
    PAIRS_MAJORS + PAIRS_CROSSES + PAIRS_COMMODITIES + PAIRS_INDICES + PAIRS_CRYPTO
)

# Пары по умолчанию (из .env или все)
_symbols_raw = os.getenv("DEFAULT_SYMBOLS", ",".join(ALL_PAIRS))
DEFAULT_SYMBOLS: list[str] = [s.strip().upper() for s in _symbols_raw.split(",") if s.strip()]

# ── Авто-уведомления ──────────────────────────────────────
_notify_raw = os.getenv("NOTIFY_USER_IDS", "")
NOTIFY_USER_IDS: list[int] = [
    int(uid.strip()) for uid in _notify_raw.split(",") if uid.strip().isdigit()
]
SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))

# ── Минимальные звёзды для уведомления ────────────────────
MIN_SIGNAL_STARS: int = 4   # Только 4-5 звёзд → уведомление

# ── Мульти-таймфрейм ─────────────────────────────────────
MULTI_TF_LIST: list[str] = ["M15", "H1", "H4", "D1"]

# ── Новости ───────────────────────────────────────────────
NEWS_CHECK_INTERVAL_MINUTES: int = 15
NEWS_WARN_BEFORE_MINUTES: list[int] = [30, 5]   # Предупреждать за 30 и 5 минут

# ── Маппинг таймфреймов ──────────────────────────────────
TIMEFRAME_MAP_CCXT: dict[str, str] = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d", "W1": "1w",
}

TIMEFRAME_MAP_YF: dict[str, str] = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "1h", "D1": "1d", "W1": "1wk",
}

# ── Маппинг символов для yfinance ─────────────────────────
YFINANCE_SYMBOL_MAP: dict[str, str] = {
    # Мажоры
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X", "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
    "USDCAD": "USDCAD=X",
    # Кроссы
    "EURGBP": "EURGBP=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X",
    "EURAUD": "EURAUD=X", "GBPAUD": "GBPAUD=X", "EURCHF": "EURCHF=X",
    "CADJPY": "CADJPY=X", "AUDCAD": "AUDCAD=X", "AUDNZD": "AUDNZD=X",
    # Металлы / Сырьё
    "XAUUSD": "GC=F", "XAGUSD": "SI=F",
    # Индексы
    "US30": "YM=F", "NAS100": "NQ=F", "SPX500": "ES=F", "DE40": "^GDAXI",
}

# ── Крипто-пары (через ccxt) ─────────────────────────────
CRYPTO_SYMBOLS: set[str] = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}

# ── Доступные таймфреймы ──────────────────────────────────
AVAILABLE_TIMEFRAMES: list[str] = ["M5", "M15", "M30", "H1", "H4", "D1", "W1"]

# ── Названия стратегий ────────────────────────────────────
STRATEGY_NAMES: dict[str, str] = {
    "ict": "ICT / Smart Money Concepts",
    "sd": "Supply & Demand",
    "wyckoff": "Wyckoff",
    "breakout": "Breakout + Retest",
    "scalping": "Scalping",
    "volume": "Volume Analysis",
}

# ── Маппинг валют к странам (для новостей) ────────────────
CURRENCY_COUNTRY: dict[str, str] = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CHF": "🇨🇭", "AUD": "🇦🇺", "NZD": "🇳🇿", "CAD": "🇨🇦",
}

# Какие валюты затрагивает пара
PAIR_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "USDCHF": ["USD", "CHF"],
    "AUDUSD": ["AUD", "USD"], "NZDUSD": ["NZD", "USD"],
    "USDCAD": ["USD", "CAD"], "EURGBP": ["EUR", "GBP"],
    "EURJPY": ["EUR", "JPY"], "GBPJPY": ["GBP", "JPY"],
    "EURAUD": ["EUR", "AUD"], "GBPAUD": ["GBP", "AUD"],
    "EURCHF": ["EUR", "CHF"], "CADJPY": ["CAD", "JPY"],
    "AUDCAD": ["AUD", "CAD"], "AUDNZD": ["AUD", "NZD"],
    "XAUUSD": ["USD"], "XAGUSD": ["USD"],
    "US30": ["USD"], "NAS100": ["USD"], "SPX500": ["USD"], "DE40": ["EUR"],
    "BTCUSDT": ["USD"], "ETHUSDT": ["USD"],
}


def is_crypto(symbol: str) -> bool:
    """Определяет, является ли символ крипто-парой."""
    return symbol.upper() in CRYPTO_SYMBOLS


def get_yf_symbol(symbol: str) -> str:
    """Преобразует наш символ в символ yfinance."""
    return YFINANCE_SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}=X")


def get_affected_pairs(currency: str) -> list[str]:
    """Возвращает список пар, которые затрагивает валюта."""
    currency = currency.upper()
    return [pair for pair, currencies in PAIR_CURRENCIES.items() if currency in currencies]
