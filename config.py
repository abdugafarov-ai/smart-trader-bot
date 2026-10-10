"""
Smart Trader Bot — Configuration
Загрузка переменных окружения и глобальных настроек.
"""

import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Загрузка .env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)


# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

# ── Администратор бота (только он одобряет заявки) ────────
_admin_raw = os.getenv("ADMIN_ID", "")
ADMIN_ID: int = int(_admin_raw) if _admin_raw.isdigit() else 0
ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "SmartTraderAdmin")

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
PAIRS_COMMODITIES: list[str] = []
PAIRS_INDICES: list[str] = []
PAIRS_CRYPTO: list[str] = []
BANNED_PAIRS: list[str] = ["XAUUSD", "GOLD"]

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

# ── Local Execution Bridge (MetaTrader 5 API) ─────────────
WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT: int = int(os.getenv("WEBAPP_PORT", "8080"))
WEBAPP_URL: str = ""

# ── Auto-Trading Bridge (MetaTrader 4/5) ───────────────────
AUTOTRADE_ENABLED: bool = os.getenv("AUTOTRADE_ENABLED", "true").lower() in ("true", "1", "yes")
AUTOTRADE_DEFAULT_RISK: float = float(os.getenv("AUTOTRADE_DEFAULT_RISK", "1.0"))
AUTOTRADE_DEFAULT_LOT: float = float(os.getenv("AUTOTRADE_DEFAULT_LOT", "0.01"))

# ── Стиль графиков (ict, dark, light) ────────────────────
CHART_THEME: str = os.getenv("CHART_THEME", "ict")

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

# ── Крипто-пары (отключены) ───────────────────────────────
CRYPTO_SYMBOLS: set[str] = set()

# ── Доступные таймфреймы ──────────────────────────────────
AVAILABLE_TIMEFRAMES: list[str] = ["M5", "M15", "M30", "H1", "H4", "D1", "W1"]

# ── Названия стратегий (Единая институциональная ось ICT/SMC) ───────
STRATEGY_NAMES: dict[str, str] = {
    "ict": "ICT / Smart Money Concepts",
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
}


def is_crypto(symbol: str) -> bool:
    """Определяет, является ли символ крипто-парой (крипта отключена)."""
    return False


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
}

# ── Correlation Groups (для ограничения одновременных ордеров) ──
CORRELATION_GROUPS = {
    "USD_LONG": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],   # SHORT USD = LONG these
    "USD_SHORT": ["USDJPY", "USDCHF", "USDCAD"],              # LONG USD = LONG these
    "JPY_PAIRS": ["EURJPY", "GBPJPY", "CADJPY", "USDJPY"],
    "AUD_PAIRS": ["AUDUSD", "EURAUD", "GBPAUD", "AUDCAD", "AUDNZD"],
}
MAX_CORRELATED_SIGNALS = 2  # Max simultaneous signals in one correlation group

# ── Order Limits (Лимит одновременно открытых ордеров) ────
MAX_CONCURRENT_ORDERS = 5  # Синхронизировано с MAX_CONCURRENT_POSITIONS (макс. 5 позиций в рынке)

# ── Micro Account Protection Mode ($12 - $100) ────────────
MICRO_MAX_CONCURRENT_ORDERS = 1      # Строго 1 сделка в рынке одновременно
MICRO_MAX_SL_PIPS = 18.0             # Максимальный стоп-лосс в пипсах (макс. риск ~$1.80 на 0.01 лота)
MICRO_EXCLUDED_PAIRS = ["XAUUSD"]    # Золото исключено для защиты от разрушительной волатильности

# ── Daily Limits ──────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 7    # Максимум 7 сигналов в сутки
SIGNAL_COOLDOWN_HOURS = 1  # 1 час между сигналами на одну пару (динамичнее)

def is_pair_in_active_session(symbol: str) -> bool:
    """Проверяет, торгуется ли пара в текущую сессию."""
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
    current_hour = datetime.now(ZoneInfo('UTC')).hour
    for kz in KILL_ZONES_UTC.values():
        if kz['start'] <= current_hour < kz['end']:
            return True
    return False

def get_current_kill_zone() -> str | None:
    """Возвращает название текущей Kill Zone или None."""
    current_hour = datetime.now(ZoneInfo('UTC')).hour
    for key, kz in KILL_ZONES_UTC.items():
        if kz['start'] <= current_hour < kz['end']:
            return kz['name']
    return None


def get_affected_pairs(currency: str) -> list[str]:
    """Возвращает список пар, которые затрагивает валюта."""
    currency = currency.upper()
    return [pair for pair, currencies in PAIR_CURRENCIES.items() if currency in currencies]


# ── SPREAD SPIKE GUARD ──
# Защита от расширения спреда: если текущий спред брокера выше лимита (в пипсах), вход блокируется
MAX_SPREAD_PIPS: dict[str, float] = {
    "EURUSD": 2.5,
    "GBPUSD": 3.0,
    "USDJPY": 2.5,
    "USDCHF": 3.0,
    "AUDUSD": 3.0,
    "NZDUSD": 3.5,
    "USDCAD": 3.0,
    "EURGBP": 3.0,
    "EURJPY": 3.0,
    "GBPJPY": 4.0,
    "EURAUD": 4.0,
    "GBPAUD": 4.5,
    "EURCHF": 3.5,
    "CADJPY": 3.5,
    "AUDCAD": 3.5,
    "AUDNZD": 3.5,
    "DEFAULT": 4.0
}


# ── SMART WEEKLY TRADING WINDOW (Защита понедельника и пятницы) ──
# Понедельник: старт новых сделок с 07:00 UTC (12:00 Ташкент / открытие Лондона)
# Пятница: запрет новых сделок после 14:00 UTC (19:00 Ташкент / защита от гэпа на выходных)
WEEKLY_WINDOW_ENABLED: bool = True
MONDAY_START_HOUR_UTC: int = 7    # 12:00 Ташкент (UTC+5)
FRIDAY_END_HOUR_UTC: int = 14     # 19:00 Ташкент (UTC+5)


def is_weekly_trading_window_open() -> tuple[bool, str]:
    """
    Проверяет институциональное торговое окно недели:
    - Понедельник: входы разрешены с 07:00 UTC (12:00 Ташкент / Лондон)
    - Вторник - Четверг: круглосуточно (24h)
    - Пятница: новые входы разрешены только до 14:00 UTC (19:00 Ташкент)
    - Выходные (Сб-Вс): закрыто
    """
    if not WEEKLY_WINDOW_ENABLED:
        return True, ""

    now = datetime.now(ZoneInfo('UTC'))
    wd = now.weekday()  # 0 = Mon, ..., 4 = Fri, 5 = Sat, 6 = Sun
    hour = now.hour

    if wd == 5:  # Saturday
        return False, "Суббота: рынок Forex закрыт"
    if wd == 6:  # Sunday — block entirely
        return False, "Воскресенье: рынок Forex закрыт"

    if wd == 0 and hour < MONDAY_START_HOUR_UTC:
        return False, f"Понедельник утро (до 12:00 Ташкент / 07:00 UTC) — ожидание институционального объема Лондона"

    if wd == 4 and hour >= FRIDAY_END_HOUR_UTC:
        return False, f"Пятница вечер (после 19:00 Ташкент / 14:00 UTC) — защита от гэпа на выходных"

    return True, ""


# ── VPS/Wine пути ────────────────────────────────────────
WINE_MT5_PATH = os.getenv('WINE_MT5_PATH', '/home/trader/.wine/drive_c/Program Files/MetaTrader 5')


BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', 'stb-default-key-change-me-2026')


# ── РИСК-МЕНЕДЖМЕНТ ДЛЯ РЕАЛЬНЫХ ДЕНЕГ (REAL MONEY PROTECTION) ──
# 1. Жесткий лимит дневной просадки (% от баланса на начало дня)
MAX_DAILY_DRAWDOWN_PCT: float = 3.0  # При дневном убытке 3.0% автопилот полностью блокируется

# 2. Ограничение совокупного риска и количества позиций
MAX_CONCURRENT_POSITIONS: int = 5    # Не более 5 одновременно открытых позиций во всем терминале
MAX_POSITIONS_PER_PAIR: int = 1      # Не более 1 позиции на одну валютную пару
MAX_CURRENCY_EXPOSURE: int = 2       # Не более 2 пар с одинаковой валютой (защита от скачков USD/EUR)

# 3. Маржинальная защита (Margin Guard)
MIN_MARGIN_FREE_USD: float = 50.0    # Минимальная свободная маржа в USD
MIN_MARGIN_FREE_PCT: float = 25.0    # Минимальный запас свободной маржи (% от баланса)
