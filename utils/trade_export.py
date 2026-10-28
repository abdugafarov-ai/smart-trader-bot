"""
Smart Trader Bot — Secure Trade Journal Export.
Защищённый экспорт журнала сделок в CSV и JSON:
- Исключает любые токены, пароли, секретные ключи
- Маскирует полные идентификаторы торгового счёта (например, ****1234)
- Форматирует отсутствующие данные как N/A
"""

import csv
import io
import json
import logging
from typing import List, Dict, Any, Optional

from datetime import datetime, timezone
import re

logger = logging.getLogger(__name__)

EXPORT_COLUMNS = [
    ("id", "ID"),
    ("record_type", "Record_Type"),
    ("position_id", "Position_ID"),
    ("order_id", "Order_ID"),
    ("close_order_id", "Close_Order_ID"),
    ("deal_in_ticket", "Deal_In_Ticket"),
    ("deal_out_ticket", "Deal_Out_Ticket"),
    ("signal_id", "Signal_ID"),
    ("broker_symbol", "Broker_Symbol"),
    ("symbol", "Symbol"),
    ("direction", "Direction"),
    ("lot", "Lot"),
    ("entry_price", "Entry_Price"),
    ("close_price", "Close_Price"),
    ("initial_sl", "Initial_SL"),
    ("final_sl", "Final_SL"),
    ("initial_tp", "Initial_TP"),
    ("final_tp", "Final_TP"),
    ("signal_time", "Signal_Time"),
    ("open_time", "Open_Time"),
    ("close_time", "Close_Time"),
    ("duration_seconds", "Duration_Seconds"),
    ("duration_str", "Duration_Str"),
    ("entry_type", "Entry_Type"),
    ("strategy", "Strategy"),
    ("setup_version", "Setup_Version"),
    ("trading_session", "Trading_Session"),
    ("balance_before", "Balance_Before"),
    ("equity_before", "Equity_Before"),
    ("risk_usd", "Risk_USD"),
    ("risk_pct", "Risk_Pct"),
    ("planned_rr", "Planned_RR"),
    ("actual_r", "Actual_R"),
    ("gross_profit", "Gross_Profit_USD"),
    ("commission", "Commission_USD"),
    ("swap", "Swap_USD"),
    ("slippage", "Slippage"),
    ("net_pnl", "Net_PnL_USD"),
    ("pnl_pips", "PnL_Pips"),
    ("result", "Result"),
    ("mae", "MAE_Price"),
    ("mfe", "MFE_Price"),
    ("mae_pips", "MAE_Pips"),
    ("mfe_pips", "MFE_Pips"),
    ("mae_mfe_status", "MAE_MFE_Status"),
    ("comment", "Comment"),
    ("account", "Account"),
    ("created_at", "Recorded_At"),
]

SENSITIVE_KEY_PATTERNS = {
    "token", "password", "secret", "api_key", "key", "auth", "credential",
    "private", "cert", "hash", "session_token"
}

BOT_TOKEN_REGEX = re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{35}\b")
SECRET_VALUE_REGEX = re.compile(r"(?i)\b(password|passwd|secret|api_key|token)\s*[:=]\s*([^\s,;\"']+)")

PRICE_FIELDS = {
    "entry_price", "close_price", "initial_sl", "final_sl",
    "initial_tp", "final_tp", "mae", "mfe"
}
CURRENCY_FIELDS = {
    "gross_profit", "commission", "swap", "net_pnl",
    "balance_before", "equity_before", "risk_usd"
}
PIP_FIELDS = {
    "pnl_pips", "mae_pips", "mfe_pips", "planned_rr", "actual_r", "risk_pct", "slippage", "slippage_pts"
}


def _sanitize_string_value(val: Any) -> Any:
    if not isinstance(val, str):
        return val
    s = BOT_TOKEN_REGEX.sub("[REDACTED_BOT_TOKEN]", val)
    s = SECRET_VALUE_REGEX.sub(r"\1=[REDACTED]", s)
    return s


def sanitize_trade_record(trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Очищает запись сделки от конфиденциальных данных перед экспортом:
    - Удаляет токены, пароли, секреты
    - Маскирует номер счета брокера (оставляет только последние 4 цифры)
    - Очищает строковые поля от встроенных токенов и паролей
    """
    clean = {}
    for k, v in trade.items():
        k_lower = k.lower()
        if any(pat in k_lower for pat in SENSITIVE_KEY_PATTERNS):
            continue

        if k_lower in ("account", "account_login", "account_id", "login"):
            if v is not None:
                acc_s = str(v).strip()
                clean[k] = f"****{acc_s[-4:]}" if len(acc_s) > 4 else "****"
            else:
                clean[k] = "N/A"
            continue

        if k_lower in ("raw_telemetry", "raw_data") and v:
            try:
                raw_obj = json.loads(v) if isinstance(v, str) else v
                if isinstance(raw_obj, dict):
                    sanitized_raw = {}
                    for rk, rv in raw_obj.items():
                        rk_lower = rk.lower()
                        if any(pat in rk_lower for pat in SENSITIVE_KEY_PATTERNS):
                            continue
                        if rk_lower in ("account", "account_login"):
                            acc_s = str(rv).strip()
                            sanitized_raw[rk] = f"****{acc_s[-4:]}" if len(acc_s) > 4 else "****"
                        elif isinstance(rv, str):
                            sanitized_raw[rk] = _sanitize_string_value(rv)
                        else:
                            sanitized_raw[rk] = rv
                    clean[k] = json.dumps(sanitized_raw)
                else:
                    clean[k] = "[PROTECTED]"
            except Exception:
                clean[k] = "[PROTECTED]"
            continue

        if isinstance(v, str):
            clean[k] = _sanitize_string_value(v)
        else:
            clean[k] = v

    return clean


def export_trades_to_csv(trades: List[Dict[str, Any]]) -> str:
    """
    Генерирует защищённый CSV-документ полного журнала сделок с сохранением институциональной точности котировок.
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    # Header row
    headers = [col_label for _, col_label in EXPORT_COLUMNS]
    writer.writerow(headers)

    for t in trades:
        safe_t = sanitize_trade_record(t)
        row = []
        for field_key, _ in EXPORT_COLUMNS:
            val = safe_t.get(field_key)
            if val is None or val == "":
                row.append("N/A")
            elif isinstance(val, float):
                if field_key in PRICE_FIELDS:
                    if float(val).is_integer() or round(val, 2) == round(val, 5):
                        row.append(f"{val:.2f}")
                    else:
                        row.append(f"{val:.5f}".rstrip("0").rstrip("."))
                elif field_key in CURRENCY_FIELDS or field_key in PIP_FIELDS or field_key == "lot":
                    row.append(f"{val:.2f}")
                else:
                    row.append(f"{val:.2f}")
            else:
                row.append(str(val))
        writer.writerow(row)

    return output.getvalue()


def export_trades_to_json(trades: List[Dict[str, Any]], indent: int = 2) -> str:
    """
    Генерирует защищённый JSON-документ полного журнала сделок.
    """
    sanitized_list = []
    for t in trades:
        safe_t = sanitize_trade_record(t)
        # Упорядочиваем поля согласно EXPORT_COLUMNS
        ordered_trade = {}
        for field_key, col_label in EXPORT_COLUMNS:
            ordered_trade[field_key] = safe_t.get(field_key)
            if ordered_trade[field_key] is None:
                ordered_trade[field_key] = "N/A"
        sanitized_list.append(ordered_trade)

    return json.dumps({
        "status": "ok",
        "total_records": len(sanitized_list),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "trades": sanitized_list
    }, indent=indent, ensure_ascii=False)
