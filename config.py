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
    "XAUUSD",
]
PAIRS_INDICES: list[str] = []
PAIRS_CRYPTO: list[str] = []

ALL_PAIRS: list[str] = (
    PAIRS_MAJORS + PAIRS_CROSSES + PAIRS_COMMODITIES
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
    # Металлы
    "XAUUSD": "GC=F",
}

# ── Крипто-пары (отключены) ───────────────────────────────
CRYPTO_SYMBOLS: set[str] = set()

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
    "XAUUSD": ["USD"],
}


def is_crypto(symbol: str) -> bool:
    """Определяет, является ли символ крипто-парой."""
    return symbol.upper() in CRYPTO_SYMBOLS


def get_yf_symbol(symbol: str) -> str:
    """Преобразует наш символ в символ yfinance."""
    return YFINANCE_SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}=X")


# ── ICT Kill Zones (UTC hours) ────────────────────────────
# London Kill Zone: 07:00-10:00 UTC (02:00-05:00 ET)
# NY Kill Zone: 12:00-15:00 UTC (07:00-10:00 ET)
KILL_ZONES_UTC = {
    "london": {"start": 7, "end": 10, "name": "London Open KZ"},
    "ny": {"start": 12, "end": 15, "name": "New York Open KZ"},
}

# ── Session Filter: какие пары активны в какие сессии (UTC часы) ──
# Пара торгуется ТОЛЬКО когда хотя бы одна её сессия активна
PAIR_ACTIVE_SESSIONS = {
    # EUR pairs — London + NY (07:00-20:00 UTC)
    "EURUSD": [(7, 20)], "EURGBP": [(7, 16)], "EURJPY": [(0, 9), (7, 16)],
    "EURAUD": [(0, 9), (7, 16)], "EURCHF": [(7, 16)],
    # GBP pairs — London + NY (07:00-20:00 UTC)
    "GBPUSD": [(7, 20)], "GBPJPY": [(0, 9), (7, 16)], "GBPAUD": [(0, 9), (7, 16)],
    # USD pairs — NY session primary (12:00-20:00), London secondary
    "USDJPY": [(0, 9), (12, 20)], "USDCHF": [(7, 20)], "USDCAD": [(12, 20)],
    # AUD/NZD pairs — Sydney/Tokyo + London open
    "AUDUSD": [(0, 9), (7, 16)], "NZDUSD": [(0, 9), (7, 16)],
    "AUDCAD": [(0, 9), (12, 20)], "AUDNZD": [(0, 6)],
    # JPY crosses — Tokyo + London
    "CADJPY": [(0, 9), (12, 20)],
    # Gold — London + NY only
    "XAUUSD": [(7, 20)],
}

# ── Correlation Groups (для ограничения одновременных ордеров) ──
CORRELATION_GROUPS = {
    "USD_LONG": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],   # SHORT USD = LONG these
    "USD_SHORT": ["USDJPY", "USDCHF", "USDCAD"],              # LONG USD = LONG these
    "JPY_PAIRS": ["EURJPY", "GBPJPY", "CADJPY", "USDJPY"],
    "AUD_PAIRS": ["AUDUSD", "EURAUD", "GBPAUD", "AUDCAD", "AUDNZD"],
}
MAX_CORRELATED_SIGNALS = 2  # Max simultaneous signals in one correlation group

# ── Daily Limits ──────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 3
SIGNAL_COOLDOWN_HOURS = 2  # Минимум 2 часа между сигналами на одну пару

def is_pair_in_active_session(symbol: str) -> bool:
    """Проверяет, торгуется ли пара в текущую сессию."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    current_hour = datetime.now(ZoneInfo('UTC')).hour
    sessions = PAIR_ACTIVE_SESSIONS.get(symbol, [(0, 24)])  # default: always active
    for start_h, end_h in sessions:
        if start_h <= end_h:
            if start_h <= current_hour < end_h:
                return True
        else:  # overnight session (e.g. 22-6)
            if current_hour >= start_h or current_hour < end_h:
                return True
    return False

def is_in_kill_zone() -> bool:
    """Проверяет, находимся ли мы в ICT Kill Zone (London/NY Open)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    current_hour = datetime.now(ZoneInfo('UTC')).hour
    for kz in KILL_ZONES_UTC.values():
        if kz['start'] <= current_hour < kz['end']:
            return True
    return False

def get_current_kill_zone() -> str | None:
    """Возвращает название текущей Kill Zone или None."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    current_hour = datetime.now(ZoneInfo('UTC')).hour
    for key, kz in KILL_ZONES_UTC.items():
        if kz['start'] <= current_hour < kz['end']:
            return kz['name']
    return None


def get_affected_pairs(currency: str) -> list[str]:
    """Возвращает список пар, которые затрагивает валюта."""
    currency = currency.upper()
    return [pair for pair, currencies in PAIR_CURRENCIES.items() if currency in currencies]
