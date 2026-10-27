"""
Smart Trader Bot — Broker Time Utilities (FundingPips MT5 Server Time: EET / EEST)
FundingPips сбрасывает дневную просадку ровно в 00:00 по времени сервера MT5 (EET / UTC+2 зимой, UTC+3 летом).
Синхронизирует сброс daily_stats и daily_loss_locked с 00:00 брокера.
"""
from datetime import datetime, timezone
import zoneinfo
import logging
import config

logger = logging.getLogger(__name__)

def get_broker_timezone() -> zoneinfo.ZoneInfo:
    """Возвращает часовой пояс сервера брокера MT5 (по умолчанию Europe/Helsinki = EET/EEST)."""
    tz_name = getattr(config, "BROKER_TIMEZONE_NAME", "Europe/Helsinki")
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception as e:
        logger.warning("Failed loading ZoneInfo(%s): %s. Fallback to UTC.", tz_name, e)
        return zoneinfo.ZoneInfo("UTC")

def get_broker_now() -> datetime:
    """Возвращает текущее время по серверу брокера MT5 (EET: UTC+2 зимой, UTC+3 летом)."""
    return datetime.now(get_broker_timezone())

def get_broker_today_date_str() -> str:
    """Возвращает текущую дату брокера MT5 в формате YYYY-MM-DD."""
    return get_broker_now().strftime("%Y-%m-%d")

def get_broker_today_midnight() -> datetime:
    """
    Возвращает объект datetime 00:00:00 текущего торгового дня по серверу брокера MT5.
    """
    b_now = get_broker_now()
    return datetime(b_now.year, b_now.month, b_now.day, 0, 0, 0, tzinfo=b_now.tzinfo)

def get_broker_today_midnight_ts() -> int:
    """
    Возвращает unix timestamp 00:00:00 текущего торгового дня брокера MT5.
    Используется для фильтрации broker_deals.close_time >= today_ts.
    """
    return int(get_broker_today_midnight().timestamp())
