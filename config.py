"""
Smart Trader Bot — Configuration
Загрузка переменных окружения и глобальных настроек.
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Optional
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
PAIRS_MAJORS: list[str] = []
PAIRS_CROSSES: list[str] = []
PAIRS_COMMODITIES: list[str] = ["XAUUSD"]
PAIRS_INDICES: list[str] = []
PAIRS_CRYPTO: list[str] = []
BANNED_PAIRS: list[str] = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD", "EURCHF", "CADJPY", "AUDCAD", "AUDNZD"]

ALL_PAIRS: list[str] = (
    PAIRS_COMMODITIES
)

# Пары по умолчанию (из .env или все)
_symbols_raw = os.getenv("DEFAULT_SYMBOLS", "XAUUSD")
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
AUTOTRADE_DEFAULT_RISK: float = float(os.getenv("AUTOTRADE_DEFAULT_RISK", "0.8"))
AUTOTRADE_DEFAULT_LOT: float = float(os.getenv("AUTOTRADE_DEFAULT_LOT", "0.02"))

# ── Стиль графиков (ict, dark, light) ────────────────────
CHART_THEME: str = os.getenv("CHART_THEME", "ict")

# ── Минимальные звёзды для уведомления ────────────────────
MIN_SIGNAL_STARS: int = 4   # Только 4-5 звёзд → уведомление

# ── Мульти-таймфрейм (Фрактальная 3-слойная модель HTF -> ITF -> LTF) ────
MULTI_TF_LIST: list[str] = ["H1", "H4", "D1", "W1"]

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

# ── IPDA ALGORITHMIC TIMINGS & ICT MACRO WINDOWS (UTC) ────
# 1. ICT Macro Windows (Окна максимальной межбанковской алгоритмической ликвидности):
#    - London Morning Macro: 08:50 - 09:10 UTC
#    - New York Morning Macro: 13:50 - 14:10 UTC (09:50 - 10:10 ET)
ICT_MACRO_WINDOWS = [
    {"start_h": 8, "start_m": 50, "end_h": 9, "end_m": 10, "name": "London Morning Macro (08:50–09:10 UTC)"},
    {"start_h": 13, "start_m": 50, "end_h": 14, "end_m": 10, "name": "New York AM Macro (13:50–14:10 UTC)"},
]

# 2. Временные запреты на новые входы (Algorithmic Trading Bans):
#    - Пре-Лондон фикс/закрытие: 15:45 - 16:00 UTC (за 15 мин до закрытия Лондона 16:00 UTC)
#    - Мертвая зона ликвидности: 17:00 - 00:00 UTC (расползание спредов, закрытие NY)
PRE_LONDON_CLOSE_BAN_START_MIN = 15 * 60 + 45  # 15:45 UTC
PRE_LONDON_CLOSE_BAN_END_MIN   = 16 * 60 + 0   # 16:00 UTC
DEAD_ZONE_START_MIN            = 17 * 60 + 0   # 17:00 UTC
DEAD_ZONE_END_MIN              = 24 * 60 + 0   # 00:00 UTC

def is_in_macro_window(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """Проверяет, находится ли текущее время внутри ICT Macro Window (08:50-09:10 или 13:50-14:10 UTC)."""
    if now_dt is None:
        now = datetime.now(ZoneInfo('UTC'))
    else:
        now = now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))
    current_mins = now.hour * 60 + now.minute
    for mw in ICT_MACRO_WINDOWS:
        s_mins = mw["start_h"] * 60 + mw["start_m"]
        e_mins = mw["end_h"] * 60 + mw["end_m"]
        if s_mins <= current_mins <= e_mins:
            return True, mw["name"]
    return False, ""

def is_ipda_time_banned(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Проверяет институциональные временные запреты IPDA:
    1. За 15 минут до закрытия Лондонской сессии (15:45-16:00 UTC)
    2. Мертвая зона ликвидности (17:00-00:00 UTC)
    """
    if now_dt is None:
        now = datetime.now(ZoneInfo('UTC'))
    else:
        now = now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))
    current_mins = now.hour * 60 + now.minute
    
    # 1. За 15 минут до закрытия Лондона (15:45-16:00 UTC)
    if PRE_LONDON_CLOSE_BAN_START_MIN <= current_mins < PRE_LONDON_CLOSE_BAN_END_MIN:
        return True, "Запрет входа: 15 минут до закрытия Лондона (15:45–16:00 UTC) — фиксация институциональных позиций"
        
    # 2. Мертвая зона (17:00-00:00 UTC)
    if current_mins >= DEAD_ZONE_START_MIN:
        return True, "Мертвая зона ликвидности (17:00–00:00 UTC) — закрытие NY, тонкий рынок и расползание спредов"
        
    return False, ""

# ── Session Filter: какие пары активны в какие сессии (UTC часы) ──
# Пара торгуется ТОЛЬКО когда хотя бы одна её сессия активна
PAIR_ACTIVE_SESSIONS = {
    "XAUUSD": [(0, 24)],
}

CORRELATION_GROUPS = {
    "USD_LONG": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],   # SHORT USD = LONG these
    "USD_SHORT": ["USDJPY", "USDCHF", "USDCAD"],              # LONG USD = LONG these
    "JPY_PAIRS": ["EURJPY", "GBPJPY", "CADJPY", "USDJPY"],
    "AUD_PAIRS": ["AUDUSD", "EURAUD", "GBPAUD", "AUDCAD", "AUDNZD"],
}
MAX_CORRELATED_SIGNALS: int = 3  # Max simultaneous signals in one correlation group

# ── Order Limits (Лимит одновременно открытых ордеров) ────
MAX_CONCURRENT_ORDERS: int = 1  # Синхронизировано с MAX_CONCURRENT_POSITIONS (макс. 4 позиции в рынке)

# ── Micro Account Protection Mode ($12 - $100) ────────────
MICRO_MAX_CONCURRENT_ORDERS = 1      # Строго 1 сделка в рынке одновременно
MICRO_MAX_SL_PIPS = 18.0             # Максимальный стоп-лосс в пипсах (макс. риск ~$1.80 на 0.01 лота)
MICRO_EXCLUDED_PAIRS: list[str] = []    # Золото исключено для защиты от разрушительной волатильности

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

def is_in_kill_zone(now_dt: Optional[datetime] = None) -> bool:
    """Проверяет, находимся ли мы в ICT Kill Zone (London/NY Open)."""
    if now_dt is None:
        current_hour = datetime.now(ZoneInfo('UTC')).hour
    else:
        current_hour = (now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))).hour
    for kz in KILL_ZONES_UTC.values():
        if kz['start'] <= current_hour < kz['end']:
            return True
    return False

def get_current_kill_zone(now_dt: Optional[datetime] = None) -> str | None:
    """Возвращает название текущей Kill Zone или None."""
    if now_dt is None:
        current_hour = datetime.now(ZoneInfo('UTC')).hour
    else:
        current_hour = (now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))).hour
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
    "XAUUSD": 6.0,
    "DEFAULT": 6.0
}


# ── WEEKLY TRADING WINDOW ──
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


BRIDGE_API_KEY = os.getenv('BRIDGE_API_KEY', 'stb-fp-9f8e7d6c5b4a3f2e1d0c-2026')


# ── РИСК-МЕНЕДЖМЕНТ ДЛЯ РЕАЛЬНЫХ ДЕНЕГ (REAL MONEY PROTECTION) ──
# 1. Жесткий лимит дневной просадки (% от баланса на начало дня)
MAX_DAILY_DRAWDOWN_PCT: float = 2.5  # При дневном убытке 2.5% ($125) автопилот блокируется (запас $75 до лимита брокера 4%/$200)

# 2. Ограничение совокупного риска и количества позиций
MAX_CONCURRENT_POSITIONS: int = 1    # Не более 4 одновременно открытых позиций во всем терминале
MAX_POSITIONS_PER_PAIR: int = 1      # Не более 1 позиции на одну валютную пару
MAX_CURRENCY_EXPOSURE: int = 1       # Не более 2 пар с одинаковой валютой (защита от скачков USD/EUR)

# 3. Маржинальная защита (Margin Guard)
MIN_MARGIN_FREE_USD: float = 50.0    # Минимальная свободная маржа в USD
MIN_MARGIN_FREE_PCT: float = 25.0    # Минимальный запас свободной маржи (% от баланса)


# ── ROLLOVER & VOLATILITY PROTECTION (Защита от ночного ролловера и узких стопов) ──
ROLLOVER_GUARD_ENABLED: bool = True
ROLLOVER_START_UTC_HOUR: int = 20
ROLLOVER_START_UTC_MIN: int = 30
ROLLOVER_END_UTC_HOUR: int = 21
ROLLOVER_END_UTC_MIN: int = 45

CROSS_PAIRS_5D = {"EURAUD", "GBPAUD", "AUDCAD", "AUDNZD", "EURCHF", "EURGBP"}
CROSS_PAIRS_JPY = {"EURJPY", "GBPJPY", "CADJPY"}
MIN_SL_PIPS_CROSS: float = 20.0
MIN_SL_PIPS_MAJOR: float = 8.0


def get_min_sl_pips(symbol: str) -> float:
    sym_u = symbol.upper().replace("/", "").replace("=X", "")
    if "XAU" in sym_u or "GOLD" in sym_u:
        return 10.0  # $10.00 minimum for Gold swing (10.0 pips @ $1.00/pip)
    if sym_u in CROSS_PAIRS_5D or sym_u in CROSS_PAIRS_JPY:
        return MIN_SL_PIPS_CROSS
    return MIN_SL_PIPS_MAJOR


def is_rollover_window() -> tuple[bool, str]:
    """
    Проверяет, находимся ли мы в окне банковского ролловера (20:30 - 21:45 UTC / 23:30 - 00:45 MT5 Server Time).
    В это время ликвидность падает, а спреды на кросс-парах (EURAUD, GBPAUD и др.) расширяются в 5-10 раз.
    """
    if not ROLLOVER_GUARD_ENABLED:
        return False, ""
    now = datetime.now(ZoneInfo('UTC'))
    mins = now.hour * 60 + now.minute
    start_mins = ROLLOVER_START_UTC_HOUR * 60 + ROLLOVER_START_UTC_MIN
    end_mins = ROLLOVER_END_UTC_HOUR * 60 + ROLLOVER_END_UTC_MIN
    if start_mins <= mins <= end_mins:
        return True, "Банковский ролловер (20:30–21:45 UTC) — защита от 10-кратного расширения спредов"
    return False, ""

# ── FundingPips Challenge Profile ( 2 Step Flex) ───────
CHALLENGE_BROKER = 'FundingPips'
CHALLENGE_ACCOUNT = '21027982'
CHALLENGE_PLAN = ' · 2 Step Flex (Biggest Max Loss)'
CHALLENGE_INITIAL_BALANCE = 5000.0
CHALLENGE_P1_TARGET_PCT = 10.0   # + -> ,500
CHALLENGE_P2_TARGET_PCT = 8.0    # + -> ,400
CHALLENGE_DAILY_LOSS_PCT = 4.0   # -/day
CHALLENGE_MAX_LOSS_PCT = 12.0    # - static (floor ,400)
CHALLENGE_MIN_DAYS = 1
CHALLENGE_SPLIT = 'Up to 100%'
CHALLENGE_PRICE_USD = 39.0

# ── FundingPips Hard Equity Floor & Safety Buffer ───────
CHALLENGE_MIN_EQUITY_FLOOR_USD = 4400.0   # Абсолютный пол просадки брокера FundingPips ($4,400.00)
HARD_EQUITY_STOP_USD = 4450.0             # Аварийный хард-стоп эквити бота (запас $50.00 до пола брокера)

# ── Часовой пояс сервера брокера MT5 (FundingPips) ─────
# FundingPips сбрасывает дневной лимит в 00:00 сервера MT5 (EET: UTC+2 зима, UTC+3 лето)
BROKER_TIMEZONE_NAME = 'Europe/Helsinki'