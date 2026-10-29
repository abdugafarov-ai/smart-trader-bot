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

# ── Таймфрейм по умолчанию (Intraday M15 setup) ───────────
DEFAULT_TIMEFRAME: str = os.getenv("DEFAULT_TIMEFRAME", "M15")

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
SCAN_INTERVAL_MINUTES: int = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))

# ── Local Execution Bridge (MetaTrader 5 API) ─────────────
WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT: int = int(os.getenv("WEBAPP_PORT", "8080"))
WEBAPP_URL: str = ""

# ── Auto-Trading Bridge (MetaTrader 4/5) ───────────────────
# Safe Default: Never enable autotrade automatically without explicit manual env or toggle
AUTOTRADE_ENABLED: bool = os.getenv("AUTOTRADE_ENABLED", "false").lower() in ("true", "1", "yes")
AUTOTRADE_DEFAULT_RISK: float = float(os.getenv("AUTOTRADE_DEFAULT_RISK", "0.25"))
AUTOTRADE_MAX_RISK: float = float(os.getenv("AUTOTRADE_MAX_RISK", "0.50"))
AUTOTRADE_DEFAULT_LOT: float = float(os.getenv("AUTOTRADE_DEFAULT_LOT", "0.01"))

# ── Стиль графиков (ict, dark, light) ────────────────────
CHART_THEME: str = os.getenv("CHART_THEME", "ict")

# ── Минимальные звёзды для уведомления ────────────────────
MIN_SIGNAL_STARS: int = 4   # Только 4-5 звёзд → уведомление

# ── Мульти-таймфрейм (Фрактальная 3-слойная модель HTF -> ITF -> LTF) ────
MULTI_TF_LIST: list[str] = ["M5", "M15", "H1", "D1"]

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
    "XAUUSD": ["USD"], "GOLD": ["USD"],
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


# ── ICT Kill Zones (DST-aware London & New York + UTC reference) ────────
KILL_ZONES_UTC = {
    "london": {"start": 7, "end": 10, "name": "London Open KZ"},
    "ny": {"start": 12, "end": 15, "name": "New York Open KZ"},
}

# ── IPDA ALGORITHMIC TIMINGS & ICT MACRO WINDOWS ────
ICT_MACRO_WINDOWS = [
    {"start_h": 8, "start_m": 50, "end_h": 9, "end_m": 10, "name": "London Morning Macro (08:50–09:10)"},
    {"start_h": 13, "start_m": 50, "end_h": 14, "end_m": 10, "name": "New York AM Macro (09:50–10:10 NY)"},
]

PRE_LONDON_CLOSE_BAN_START_MIN = 15 * 60 + 45  # 15:45 UTC
PRE_LONDON_CLOSE_BAN_END_MIN   = 16 * 60 + 0   # 16:00 UTC
DEAD_ZONE_START_MIN            = 17 * 60 + 0   # 17:00 UTC
DEAD_ZONE_END_MIN              = 24 * 60 + 0   # 00:00 UTC

def is_in_macro_window(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Проверяет, находится ли текущее время внутри ICT Macro Window
    с учетом переходов на летнее/зимнее время (DST) в Лондоне и Нью-Йорке.
    """
    if now_dt is None:
        now = datetime.now(ZoneInfo('UTC'))
    else:
        now = now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))

    london_dt = now.astimezone(ZoneInfo("Europe/London"))
    ny_dt = now.astimezone(ZoneInfo("America/New_York"))

    l_h, l_m = london_dt.hour, london_dt.minute
    ny_h, ny_m = ny_dt.hour, ny_dt.minute

    if (l_h == 7 and l_m >= 50) or (l_h == 8 and l_m <= 10):
        return True, "London Open Macro (07:50–08:10 London)"
    if (l_h == 8 and l_m >= 50) or (l_h == 9 and l_m <= 10):
        return True, "London Morning Macro (08:50–09:10 London)"
    if (ny_h == 8 and ny_m >= 50) or (ny_h == 9 and ny_m <= 10):
        return True, "New York Open Macro (08:50–09:10 NY)"
    if (ny_h == 9 and ny_m >= 50) or (ny_h == 10 and ny_m <= 10):
        return True, "New York AM Macro (09:50–10:10 NY)"
    if (ny_h == 10 and ny_m >= 50) or (ny_h == 11 and ny_m <= 10):
        return True, "London Close / NY Macro (10:50–11:10 NY)"

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
MAX_CONCURRENT_ORDERS: int = 1  # Синхронизировано с MAX_CONCURRENT_POSITIONS (макс. 1 позиция в рынке)

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
    """
    Проверяет, находимся ли мы в ICT Kill Zone (London/NY Open) с учетом DST:
    - London Open KZ: 08:00–11:00 Europe/London (или 07:00–10:30 UTC)
    - New York Open KZ: 08:00–11:30 America/New_York (или 12:00–15:30 UTC)
    """
    if now_dt is None:
        now = datetime.now(ZoneInfo('UTC'))
    else:
        now = now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))

    london_dt = now.astimezone(ZoneInfo("Europe/London"))
    ny_dt = now.astimezone(ZoneInfo("America/New_York"))

    london_mins = london_dt.hour * 60 + london_dt.minute
    ny_mins = ny_dt.hour * 60 + ny_dt.minute

    if (8 * 60) <= london_mins < (11 * 60):
        return True
    if (8 * 60) <= ny_mins < (11 * 60 + 30):
        return True

    return False

def get_current_kill_zone(now_dt: Optional[datetime] = None) -> str | None:
    """Возвращает название текущей Kill Zone (DST-aware) или None."""
    if now_dt is None:
        now = datetime.now(ZoneInfo('UTC'))
    else:
        now = now_dt.astimezone(ZoneInfo('UTC')) if now_dt.tzinfo else now_dt.replace(tzinfo=ZoneInfo('UTC'))

    london_dt = now.astimezone(ZoneInfo("Europe/London"))
    ny_dt = now.astimezone(ZoneInfo("America/New_York"))

    london_mins = london_dt.hour * 60 + london_dt.minute
    ny_mins = ny_dt.hour * 60 + ny_dt.minute

    if (8 * 60) <= london_mins < (11 * 60):
        return KILL_ZONES_UTC["london"]["name"]
    if (8 * 60) <= ny_mins < (11 * 60 + 30):
        return KILL_ZONES_UTC["ny"]["name"]

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


# Без захардкоженных секретов в коде: ключ загружается из переменных окружения (.env)
BRIDGE_API_KEY: str = os.getenv('BRIDGE_API_KEY', '').strip()


# ── РИСК-МЕНЕДЖМЕНТ ДЛЯ РЕАЛЬНЫХ ДЕНЕГ (REAL MONEY PROTECTION) ──
# 1. Жесткий лимит дневной просадки (% от баланса на начало дня)
MAX_DAILY_DRAWDOWN_PCT: float = 2.5  # При дневном убытке 2.5% ($125) автопилот блокируется (запас $75 до лимита брокера 4%/$200)

# 2. Ограничение совокупного риска и количества позиций
MAX_CONCURRENT_POSITIONS: int = 1    # Строго 1 одновременно открытая позиция
MAX_POSITIONS_PER_PAIR: int = 1      # Не более 1 позиции на одну валютную пару
MAX_CURRENCY_EXPOSURE: int = 1       # Не более 1 пары с одинаковой валютой

# 3. Маржинальная защита (Margin Guard)
MIN_MARGIN_FREE_USD: float = 50.0    # Минимальная свободная маржа в USD
MIN_MARGIN_FREE_PCT: float = 25.0    # Минимальный запас свободной маржи (% от баланса)


# ── ROLLOVER & INTRADAY EOD FLAT PROTECTION ──
ROLLOVER_GUARD_ENABLED: bool = True
EOD_NEW_TRADES_CUTOFF_UTC: tuple[int, int] = (20, 30)  # Запрет новых сделок с 20:30 UTC
EOD_FLAT_CLOSE_UTC: tuple[int, int] = (20, 40)         # Принудительное закрытие позиций в 20:40 UTC
ROLLOVER_START_UTC_HOUR: int = 20
ROLLOVER_START_UTC_MIN: int = 30
ROLLOVER_END_UTC_HOUR: int = 21
ROLLOVER_END_UTC_MIN: int = 45

CROSS_PAIRS_5D = {"EURAUD", "GBPAUD", "AUDCAD", "AUDNZD", "EURCHF", "EURGBP"}
CROSS_PAIRS_JPY = {"EURJPY", "GBPJPY", "CADJPY"}
MIN_SL_PIPS_CROSS: float = 20.0
MIN_SL_PIPS_MAJOR: float = 8.0


def get_min_sl_pips(symbol: str) -> float:
    """
    Возвращает минимально допустимый структурный стоп-лосс:
    - Для XAUUSD (Intraday M15/M5): $2.00 минимум (>5x среднего спреда $0.35),
      позволяет валидные M15/M5 стопы $2.00–$12.50 в рамках риска 0.25% ($12.50).
    - Для Forex: 8.0–20.0 стандартных пипсов.
    """
    sym_u = symbol.upper().replace("/", "").replace("=X", "")
    if "XAU" in sym_u or "GOLD" in sym_u:
        return 2.0
    if sym_u in CROSS_PAIRS_5D or sym_u in CROSS_PAIRS_JPY:
        return MIN_SL_PIPS_CROSS
    return MIN_SL_PIPS_MAJOR


def get_max_sl_usd_for_risk(
    balance: float = 5000.0,
    risk_pct: Optional[float] = None,
    min_lot: float = 0.01,
    contract_size: float = 100.0,
) -> float:
    """
    Возвращает максимальную дистанцию стоп-лосса в USD для XAUUSD,
    при которой минимальный лот (0.01) не превышает риск-бюджет сделки.
    """
    pct = risk_pct if (risk_pct is not None and risk_pct > 0) else AUTOTRADE_DEFAULT_RISK
    risk_budget_usd = balance * (pct / 100.0)
    denom = min_lot * contract_size
    if denom <= 0:
        return 0.0
    return round(risk_budget_usd / denom, 2)


def is_rollover_window(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Проверяет, находимся ли мы в окне банковского ролловера (20:30 - 21:45 UTC / 23:30 - 00:45 MT5 Server Time).
    В это время ликвидность падает, а спреды расширяются в 5-10 раз.
    """
    if not ROLLOVER_GUARD_ENABLED:
        return False, ""
    now = now_dt if now_dt is not None else datetime.now(ZoneInfo('UTC'))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo('UTC'))
    mins = now.hour * 60 + now.minute
    start_mins = ROLLOVER_START_UTC_HOUR * 60 + ROLLOVER_START_UTC_MIN
    end_mins = ROLLOVER_END_UTC_HOUR * 60 + ROLLOVER_END_UTC_MIN
    if start_mins <= mins <= end_mins:
        return True, "Банковский ролловер (20:30–21:45 UTC) — защита от 10-кратного расширения спредов"
    return False, ""


def is_eod_flat_cutoff(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Проверяет, наступило ли время запрета новых входов перед концом дня (20:30 UTC)
    или перед закрытием пятницы (19:00 UTC).
    """
    now = now_dt if now_dt is not None else datetime.now(ZoneInfo('UTC'))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo('UTC'))
    wd = now.weekday()
    mins = now.hour * 60 + now.minute
    if wd == 4 and mins >= (19 * 60):
        return True, "Пятничный EOD-фильтр: запрет новых сделок после 19:00 UTC"
    if (20 * 60 + 30) <= mins <= (23 * 60 + 5):
        return True, "Intraday EOD Flat: запрет новых сделок после 20:30 UTC перед ролловером"
    return False, ""


def should_force_eod_flat(now_dt: Optional[datetime] = None) -> tuple[bool, str]:
    """
    Проверяет, наступило ли время принудительного закрытия всех открытых Intraday-позиций
    и снятия лимитных ордеров перед банковским ролловером (20:40 UTC Пн-Чт) или выходными (19:30 UTC Пт).
    """
    now = now_dt if now_dt is not None else datetime.now(ZoneInfo('UTC'))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo('UTC'))
    wd = now.weekday()
    mins = now.hour * 60 + now.minute
    if wd == 4 and mins >= (19 * 60 + 30):
        return True, "EOD_FRIDAY_FLAT (19:30 UTC)"
    if (20 * 60 + 40) <= mins <= (23 * 60 + 5):
        return True, "EOD_ROLLOVER_FLAT (20:40 UTC)"
    return False, ""

# ── Динамическая загрузка профиля проп-челленджа (challenge_config.json) ───
import json as _json

_challenge_cfg_path = Path(__file__).parent / "challenge_config.json"
_challenge_cfg: dict = {}
if _challenge_cfg_path.exists():
    try:
        with open(_challenge_cfg_path, "r", encoding="utf-8") as _cf:
            _loaded_cfg = _json.load(_cf)
            if isinstance(_loaded_cfg, dict):
                _challenge_cfg = _loaded_cfg
    except Exception:
        _challenge_cfg = {}


def _mask_account_id(acct: str) -> str:
    acct_s = str(acct or "").strip()
    if len(acct_s) <= 4:
        return "****"
    return "*" * (len(acct_s) - 4) + acct_s[-4:]


CHALLENGE_BROKER: str = os.getenv("CHALLENGE_BROKER", str(_challenge_cfg.get("broker", "FundingPips")))
CHALLENGE_ACCOUNT: str = os.getenv("CHALLENGE_ACCOUNT", "")
CHALLENGE_ACCOUNT_MASKED: str = _mask_account_id(CHALLENGE_ACCOUNT)
CHALLENGE_PLAN: str = os.getenv(
    "CHALLENGE_PLAN", str(_challenge_cfg.get("plan_name", "$5k · 2 Step Flex (Biggest Max Loss)"))
)
CHALLENGE_INITIAL_BALANCE: float = float(
    os.getenv("CHALLENGE_INITIAL_BALANCE", str(_challenge_cfg.get("initial_balance_usd", 5000.0)))
)
CHALLENGE_P1_TARGET_PCT: float = float(
    os.getenv("CHALLENGE_P1_TARGET_PCT", str(_challenge_cfg.get("phase_1_target_pct", 10.0)))
)
CHALLENGE_P2_TARGET_PCT: float = float(
    os.getenv("CHALLENGE_P2_TARGET_PCT", str(_challenge_cfg.get("phase_2_target_pct", 8.0)))
)
CHALLENGE_DAILY_LOSS_PCT: float = float(
    os.getenv("CHALLENGE_DAILY_LOSS_PCT", str(_challenge_cfg.get("daily_loss_pct", 4.0)))
)
CHALLENGE_MAX_LOSS_PCT: float = float(
    os.getenv("CHALLENGE_MAX_LOSS_PCT", str(_challenge_cfg.get("max_loss_pct", 12.0)))
)
CHALLENGE_MIN_DAYS: int = int(
    os.getenv("CHALLENGE_MIN_DAYS", str(_challenge_cfg.get("min_trading_days", 1)))
)
CHALLENGE_SPLIT: str = os.getenv(
    "CHALLENGE_SPLIT", str(_challenge_cfg.get("profit_split_pct", "Up to 100%"))
)
CHALLENGE_PRICE_USD: float = float(
    os.getenv("CHALLENGE_PRICE_USD", str(_challenge_cfg.get("challenge_price_usd", 39.0)))
)

# ── FundingPips Hard Equity Floor & Safety Buffer ───────
_default_equity_floor = round(CHALLENGE_INITIAL_BALANCE * (1.0 - (CHALLENGE_MAX_LOSS_PCT / 100.0)), 2)
CHALLENGE_MIN_EQUITY_FLOOR_USD: float = float(
    os.getenv(
        "CHALLENGE_MIN_EQUITY_FLOOR_USD",
        str(_challenge_cfg.get("min_equity_floor_usd", _default_equity_floor)),
    )
)
HARD_EQUITY_STOP_USD: float = float(
    os.getenv(
        "HARD_EQUITY_STOP_USD",
        str(_challenge_cfg.get("hard_equity_stop_usd", CHALLENGE_MIN_EQUITY_FLOOR_USD + 50.0)),
    )
)

# ── Часовой пояс сервера брокера MT5 (FundingPips) ─────
# FundingPips сбрасывает дневной лимит в 00:00 сервера MT5 (EET: UTC+2 зима, UTC+3 лето)
BROKER_TIMEZONE_NAME: str = os.getenv("BROKER_TIMEZONE_NAME", "Europe/Helsinki")

# ── Явные торговые профили (Explicit Trading Profiles) ─────────────────
TRADING_PROFILES: dict[str, dict] = {
    "XAUUSD_INTRADAY": {
        "name": "XAUUSD_INTRADAY",
        "description": "Strictly Intraday Gold (XAUUSD) via MT5 — H1 Context -> M15 POI -> M5 Trigger",
        "symbol": "XAUUSD",
        "allowed_symbols": ["XAUUSD"],
        "timeframes": {
            "context": "D1",
            "htf": "H1",
            "itf": "M15",
            "ltf": "M5",
        },
        "multi_tf_list": ["M5", "M15", "H1", "D1"],
        "default_timeframe": "M15",
        "default_risk_pct": 0.25,
        "max_risk_pct": 0.50,
        "min_lot": 0.01,
        "max_lot": 0.50,
        "min_rr": 2.0,
        "min_sl_usd": 2.0,
        "max_sl_usd_at_default_risk": 12.50,
        "max_sl_usd_hard_cap": 25.00,
        "max_spread_pips": 6.0,
        "max_spread_usd": 0.60,
        "kill_zones": {
            "london_open": {"timezone": "Europe/London", "start": "08:00", "end": "11:00"},
            "ny_open": {"timezone": "America/New_York", "start": "08:00", "end": "11:30"},
        },
        "eod_new_trades_cutoff_utc": EOD_NEW_TRADES_CUTOFF_UTC,
        "eod_flat_close_utc": EOD_FLAT_CLOSE_UTC,
        "friday_new_trades_cutoff_utc": (19, 0),
        "friday_flat_close_utc": (19, 30),
        "auto_be_trigger_r": 1.0,
        "partial_close_enabled": False,
        "max_concurrent_positions": 1,
        "autotrade_enabled_default": False,
        "primary_entry_model": "MODEL_B_TREND_OB_PULLBACK",
        "secondary_entry_model": "MODEL_A_SWEEP_MSS_FVG",
        "status": "RESEARCH_VALIDATED_DEMO_CANDIDATE",
    },
    "LEGACY_MULTI_ASSET": {
        "name": "LEGACY_MULTI_ASSET",
        "description": "Legacy multi-currency / multi-strategy profile (EXPERIMENTAL / DISABLED BY DEFAULT)",
        "symbol": "MULTI",
        "allowed_symbols": BANNED_PAIRS,
        "default_timeframe": "H1",
        "default_risk_pct": 0.25,
        "max_risk_pct": 0.50,
        "min_lot": 0.01,
        "max_lot": 0.50,
        "min_rr": 1.5,
        "autotrade_enabled_default": False,
        "status": "EXPERIMENTAL_DISABLED",
    },
}

ACTIVE_TRADING_PROFILE: str = os.getenv("ACTIVE_TRADING_PROFILE", "XAUUSD_INTRADAY").strip().upper()


def get_active_profile() -> dict:
    """Возвращает словарь настроек активного торгового профиля."""
    return TRADING_PROFILES.get(ACTIVE_TRADING_PROFILE, TRADING_PROFILES["XAUUSD_INTRADAY"])


def validate_config(raise_on_error: bool = True) -> dict:
    """
    Проверяет целостность и безопасность критических настроек при старте системы.
    При обнаружении конфликта или выхода за безопасные границы либо выбрасывает ValueError,
    либо возвращает словарь с перечнем ошибок.
    """
    errors: list[str] = []

    if ACTIVE_TRADING_PROFILE not in TRADING_PROFILES:
        errors.append(f"Unknown ACTIVE_TRADING_PROFILE='{ACTIVE_TRADING_PROFILE}'")

    if not (0.0 < AUTOTRADE_DEFAULT_RISK <= AUTOTRADE_MAX_RISK <= 1.0):
        errors.append(
            f"Invalid risk bounds: AUTOTRADE_DEFAULT_RISK={AUTOTRADE_DEFAULT_RISK}%, "
            f"AUTOTRADE_MAX_RISK={AUTOTRADE_MAX_RISK}% (required: 0 < default <= max <= 1.0%)"
        )

    if not (0.01 <= AUTOTRADE_DEFAULT_LOT <= 0.50):
        errors.append(
            f"Invalid AUTOTRADE_DEFAULT_LOT={AUTOTRADE_DEFAULT_LOT} (required: 0.01 <= lot <= 0.50)"
        )

    if CHALLENGE_INITIAL_BALANCE <= 0:
        errors.append(f"Invalid CHALLENGE_INITIAL_BALANCE={CHALLENGE_INITIAL_BALANCE}")

    if not (CHALLENGE_MIN_EQUITY_FLOOR_USD + 20.0 <= HARD_EQUITY_STOP_USD < CHALLENGE_INITIAL_BALANCE):
        errors.append(
            f"Invalid HARD_EQUITY_STOP_USD={HARD_EQUITY_STOP_USD} "
            f"(must be >= floor {CHALLENGE_MIN_EQUITY_FLOOR_USD} + $20 buffer and < {CHALLENGE_INITIAL_BALANCE})"
        )

    if not (0.0 < MAX_DAILY_DRAWDOWN_PCT < CHALLENGE_DAILY_LOSS_PCT):
        errors.append(
            f"Invalid MAX_DAILY_DRAWDOWN_PCT={MAX_DAILY_DRAWDOWN_PCT}% "
            f"(must be > 0 and strictly less than broker limit {CHALLENGE_DAILY_LOSS_PCT}%)"
        )

    if EOD_NEW_TRADES_CUTOFF_UTC >= EOD_FLAT_CLOSE_UTC:
        errors.append(
            f"Invalid EOD schedule: cutoff {EOD_NEW_TRADES_CUTOFF_UTC} must precede flat close {EOD_FLAT_CLOSE_UTC}"
        )

    if MAX_CONCURRENT_POSITIONS < 1 or MAX_CONCURRENT_ORDERS < 1:
        errors.append("MAX_CONCURRENT_POSITIONS and MAX_CONCURRENT_ORDERS must be >= 1")

    result = {
        "valid": len(errors) == 0,
        "profile": ACTIVE_TRADING_PROFILE,
        "autotrade_enabled": AUTOTRADE_ENABLED,
        "errors": errors,
    }
    if errors and raise_on_error:
        raise ValueError("Configuration validation failed: " + "; ".join(errors))
    return result