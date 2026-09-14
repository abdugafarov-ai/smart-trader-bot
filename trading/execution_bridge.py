"""
Smart Trader Bot — Execution Bridge Manager.
Координирует автоматическое исполнение ордеров через MetaTrader 4/5 и внешние вебхуки.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import config
from db.database import get_active_signals, update_signal_status

logger = logging.getLogger(__name__)


class ExecutionBridge:
    """Управляет состоянием и передачей команд внешним торговым терминалам."""

    def __init__(self):
        self.enabled = config.AUTOTRADE_ENABLED
        self.default_risk = config.AUTOTRADE_DEFAULT_RISK
        self.default_lot = config.AUTOTRADE_DEFAULT_LOT
        self._connected_terminals: Dict[str, Any] = {}
        
        # Реальная телеметрия из MetaTrader 5 (без фейковых данных по умолчанию)
        self.mt5_telemetry = {
            "balance": 0.0,
            "equity": 0.0,
            "margin_free": 0.0,
            "broker": "—",
            "account": "—",
            "positions": [],
            "orders": [],
            "last_ping": None,
        }
        self.panic_close_requested = False

        # Кэш живых котировок от советника MT5: символ -> {"bid": float, "ask": float, "time": datetime}
        self.live_quotes: Dict[str, Dict[str, Any]] = {}

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        logger.info("Auto-Trading Bridge enabled set to: %s", enabled)

    def set_risk(self, risk_percent: float):
        self.default_risk = max(0.1, min(5.0, risk_percent))
        logger.info("Auto-Trading Bridge risk set to: %.2f%%", self.default_risk)

    def set_lot(self, lot: float):
        self.default_lot = max(0.01, min(10.0, lot))
        logger.info("Auto-Trading Bridge lot set to: %.2f", self.default_lot)

    def update_telemetry(self, balance: float, equity: float, margin_free: float,
                         broker: str, account: str, positions: list, orders: list):
        """Обновляет телеметрию живыми данными, полученными от советника MT5."""
        self.mt5_telemetry["balance"] = balance
        self.mt5_telemetry["equity"] = equity
        self.mt5_telemetry["margin_free"] = margin_free
        if broker:
            self.mt5_telemetry["broker"] = broker
        if account:
            self.mt5_telemetry["account"] = str(account)
        self.mt5_telemetry["positions"] = positions or []
        self.mt5_telemetry["orders"] = orders or []
        self.mt5_telemetry["last_ping"] = datetime.now(timezone.utc)
        self._connected_terminals["MT5"] = self.mt5_telemetry["last_ping"]

    def update_quotes(self, quotes: Dict[str, Dict[str, Any]]):
        """Сохраняет живой срез котировок, присланный советником MT5."""
        if not quotes:
            return
        now = datetime.now(timezone.utc)
        for sym, q in quotes.items():
            try:
                bid = float(q.get("bid", 0.0))
                ask = float(q.get("ask", 0.0))
                if bid > 0:
                    self.live_quotes[sym.upper()] = {
                        "bid": bid,
                        "ask": ask,
                        "time": now,
                    }
            except (ValueError, TypeError):
                continue

    def get_live_price(self, symbol: str) -> Optional[float]:
        """
        Возвращает последнюю цену брокера (bid) с 0 задержкой, если MT5 в сети.
        Если котировка старее 15 секунд или MT5 оффлайн — возвращает None.
        """
        is_online, _ = self.is_mt5_online()
        if not is_online:
            return None
        q = self.live_quotes.get(symbol.upper())
        if q and q.get("bid", 0.0) > 0:
            delta = (datetime.now(timezone.utc) - q["time"]).total_seconds()
            if delta < 15.0:
                return q["bid"]
        return None

    def get_live_spread_pips(self, symbol: str) -> Optional[float]:
        """
        Вычисляет текущий спред брокера в пипсах из живого стакана котировок MT5.
        """
        is_online, _ = self.is_mt5_online()
        if not is_online:
            return None
        q = self.live_quotes.get(symbol.upper())
        if not q:
            return None
        bid = float(q.get("bid", 0.0))
        ask = float(q.get("ask", 0.0))
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        delta_p = (datetime.now(timezone.utc) - q["time"]).total_seconds()
        if delta_p > 30.0:
            return None

        diff = ask - bid
        sym_u = symbol.upper()
        if "JPY" in sym_u:
            return round(diff * 100.0, 1)
        elif sym_u == "XAUUSD":
            return round(diff * 10.0, 1)  # в центах/пунктах золота
        else:
            return round(diff * 10000.0, 1)

    def is_spread_acceptable(self, symbol: str) -> tuple[bool, float, float]:
        """
        Проверяет, находится ли текущий спред брокера в пределах безопасной нормы.
        Возвращает (acceptable, current_spread, max_allowed_spread).
        """
        cur = self.get_live_spread_pips(symbol)
        max_s = config.MAX_SPREAD_PIPS.get(symbol.upper(), config.MAX_SPREAD_PIPS.get("DEFAULT", 4.0))
        if cur is None:
            return True, 0.0, max_s  # Если живого стакана нет (напр. выходные), не блокируем
        return (cur <= max_s), cur, max_s

    def is_mt5_online(self) -> tuple[bool, float]:
        """Проверяет реальный онлайн MT5. Если пинга не было более 15 секунд — оффлайн."""
        last = self.mt5_telemetry.get("last_ping")
        if not last:
            return False, 999.0
        delta = (datetime.now(timezone.utc) - last).total_seconds()
        return (delta < 15.0), round(delta, 1)

    def request_panic_close(self):
        self.panic_close_requested = True
        logger.warning("🚨 ПАНИКА: Запрошено экстренное закрытие всех позиций и ордеров в MT5!")

    def get_status(self) -> dict:
        is_online, ping = self.is_mt5_online()
        return {
            "enabled": self.enabled,
            "risk_percent": self.default_risk,
            "default_lot": self.default_lot,
            "terminals_connected": 1 if is_online else 0,
            "mt5_online": is_online,
            "ping": ping if is_online else 0.0,
            "balance": self.mt5_telemetry["balance"] if is_online else 0.0,
            "equity": self.mt5_telemetry["equity"] if is_online else 0.0,
            "margin_free": self.mt5_telemetry["margin_free"] if is_online else 0.0,
            "broker": self.mt5_telemetry["broker"] if is_online else "Не подключен",
            "account": self.mt5_telemetry["account"] if is_online else "—",
            "positions_count": len(self.mt5_telemetry["positions"]) if is_online else 0,
            "orders_count": len(self.mt5_telemetry["orders"]) if is_online else 0,
        }

    def format_terminal_dashboard(self) -> str:
        is_online, ping = self.is_mt5_online()
        t = self.mt5_telemetry

        if is_online:
            status_badge = f"🟢 В СЕТИ (задержка {ping}с)"
            bal = float(t.get('balance', 0.0))
            eq = float(t.get('equity', 0.0))
            mf = float(t.get('margin_free', 0.0))
            balance_str = f"{bal:,.2f} USD"
            equity_str = f"{eq:,.2f} USD"
            margin_str = f"{mf:,.2f} USD"
            account_str = t['account'] if t.get('account') else "—"
            broker_str = t['broker'] if t.get('broker') else "—"

            floating_pnl = eq - bal
            pnl_sign = "+" if floating_pnl >= 0 else ""
            pnl_pct = (floating_pnl / bal * 100.0) if bal > 0 else 0.0
            pnl_badge = f"<b>{pnl_sign}{floating_pnl:.2f} USD</b> (<code>{pnl_sign}{pnl_pct:.2f}%</code>)"
        else:
            status_badge = "🔴 ОФФЛАЙН (нет связи с советником MT5)"
            balance_str = "— (терминал не в сети)"
            equity_str = "—"
            margin_str = "—"
            account_str = "—"
            broker_str = "—"
            pnl_badge = "—"

        lines = [
            "🖥 <b>ТЕРМИНАЛ METATRADER 5 | ПУЛЬТ УПРАВЛЕНИЯ</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📡 <b>Связь с советником:</b> {status_badge}",
            f"🏢 <b>Брокер:</b> <code>{broker_str}</code>",
            f"👤 <b>Счёт:</b> <code>#{account_str}</code>",
            f"💰 <b>Баланс:</b> <code>{balance_str}</code>",
            f"📈 <b>Эквити (Средства):</b> <code>{equity_str}</code>",
            f"🛡 <b>Свободная маржа:</b> <code>{margin_str}</code>",
            f"💵 <b>Плавающий PnL:</b> {pnl_badge}",
            f"🤖 <b>Автопилот:</b> {'🟢 ВКЛЮЧЕН' if self.enabled else '🔴 ВЫКЛЮЧЕН'}",
            f"⚖️ <b>Риск на сделку:</b> <code>{self.default_risk}%</code> | <b>Лот:</b> <code>{self.default_lot}</code>",
        ]

        # Статус недельного торгового окна
        w_open, w_reason = config.is_weekly_trading_window_open()
        if w_open:
            lines.append("🕒 <b>Торговое окно недели:</b> 🟢 <b>ОТКРЫТО</b> (полная активность)")
        else:
            lines.append(f"🕒 <b>Торговое окно недели:</b> ⏳ <b>ОЖИДАНИЕ</b>\n   <i>({w_reason})</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if not is_online:
            lines.append("⚠️ <i>Внимание: терминал MT5 на сервере не передает сигналы. Проверьте запущен ли советник SmartTraderBridge в MT5!</i>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            return "\n".join(lines)

        positions = t.get("positions") or []
        lines.append(f"📊 <b>Открытые позиции в рынке ({len(positions)}):</b>")
        if not positions:
            lines.append("   <i>(Нет открытых позиций в рынке)</i>")
        else:
            for p in positions:
                p_profit = p.get('profit', 0.0)
                p_sign = "+" if p_profit >= 0 else ""
                p_type = p.get('type', 'BUY')
                p_em = "🟢" if "BUY" in str(p_type).upper() else "🔴"
                p_sl = p.get('sl', 0.0)
                p_tp = p.get('tp', 0.0)
                sl_tp_info = ""
                if p_sl or p_tp:
                    sl_tp_info = f"\n     🛑 SL: <code>{p_sl}</code> | 🎯 TP: <code>{p_tp}</code>"
                lines.append(
                    f"   • {p_em} <b>{p.get('symbol')}</b> [{p_type}] <code>{p.get('lot')} lot</code>\n"
                    f"     Вход: <code>{p.get('price')}</code> | PnL: <b>{p_sign}{p_profit:.2f} USD</b>"
                    f"{sl_tp_info}"
                )

        lines.append("")
        orders = t.get("orders") or []
        lines.append(f"⏳ <b>Отложенные лимитные ордера ({len(orders)}):</b>")
        if not orders:
            lines.append("   <i>(Нет отложенных лимитных ордеров)</i>")
        else:
            for o in orders:
                o_type = o.get('type', 'LIMIT')
                o_em = "🟢" if "BUY" in str(o_type).upper() else "🔴"
                lines.append(
                    f"   • {o_em} <b>{o.get('symbol')}</b> [{o_type}] <code>{o.get('lot')} lot</code>\n"
                    f"     Вход: <code>{o.get('price')}</code> | 🛑 SL: <code>{o.get('sl')}</code> | 🎯 TP: <code>{o.get('tp')}</code>"
                )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)


# Глобальный синглтон моста
bridge_manager = ExecutionBridge()
