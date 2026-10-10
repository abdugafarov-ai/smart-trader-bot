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
        self.lot_mode = "fixed"  # "fixed" or "risk"
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

        # Институциональный риск-менеджмент для реального счёта
        self.max_daily_loss_pct: float = getattr(config, 'MAX_DAILY_DRAWDOWN_PCT', 3.0)
        self.max_concurrent_positions: int = getattr(config, 'MAX_CONCURRENT_POSITIONS', 5)
        self.daily_loss_locked: bool = False
        self.daily_lock_reason: str = ""
        self.daily_lock_alerted_date: str = ""
        self.current_trade_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.today_stats_cache: Dict[str, Any] = {}

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        logger.info("Auto-Trading Bridge enabled set to: %s", enabled)

    def set_risk(self, risk_percent: float):
        self.default_risk = max(0.1, min(5.0, risk_percent))
        self.lot_mode = "risk"
        logger.info("Auto-Trading Bridge risk set to: %.2f%% (mode: risk)", self.default_risk)

    def set_lot(self, lot: float):
        self.default_lot = max(0.01, min(10.0, lot))
        self.lot_mode = "fixed"
        logger.info("Auto-Trading Bridge lot set to: %.2f (mode: fixed)", self.default_lot)

    async def load_settings_from_db(self):
        """Загружает персистентные настройки моста (лот, риск, режим) из базы данных SQLite."""
        from db.database import get_bot_setting
        try:
            lot_str = await get_bot_setting("trading_lot", "")
            if lot_str:
                self.default_lot = max(0.01, min(10.0, float(lot_str)))
            risk_str = await get_bot_setting("trading_risk", "")
            if risk_str:
                self.default_risk = max(0.1, min(5.0, float(risk_str)))
            lot_mode_str = await get_bot_setting("lot_mode", "")
            if lot_mode_str in ("fixed", "risk"):
                self.lot_mode = lot_mode_str
            enabled_str = await get_bot_setting("autotrade_enabled", "")
            if enabled_str:
                self.enabled = (enabled_str.lower() in ("true", "1", "yes"))
            daily_loss_str = await get_bot_setting("max_daily_loss_pct", "")
            if daily_loss_str:
                self.max_daily_loss_pct = max(0.5, min(15.0, float(daily_loss_str)))
            max_pos_str = await get_bot_setting("max_concurrent_positions", "")
            if max_pos_str:
                self.max_concurrent_positions = max(1, min(10, int(max_pos_str)))
            logger.info("Loaded Bridge settings from DB: lot=%.2f, risk=%.1f%%, lot_mode=%s, enabled=%s, daily_loss_limit=%.1f%%, max_positions=%d",
                        self.default_lot, self.default_risk, self.lot_mode, self.enabled, self.max_daily_loss_pct, self.max_concurrent_positions)
        except Exception as e:
            logger.error("Failed to load Bridge settings from DB: %s", e)

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
            "lot_mode": self.lot_mode,
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

    def set_daily_loss_limit(self, pct: float):
        self.max_daily_loss_pct = max(0.5, min(15.0, pct))
        logger.info("Auto-Trading Daily Loss Limit set to: %.1f%%", self.max_daily_loss_pct)

    def set_max_positions(self, count: int):
        self.max_concurrent_positions = max(1, min(10, count))
        logger.info("Auto-Trading Max Concurrent Positions set to: %d", self.max_concurrent_positions)

    def unlock_daily_loss(self):
        """Ручной сброс блокировки дневного риска администратором."""
        self.daily_loss_locked = False
        self.daily_lock_reason = ""
        logger.info("Daily loss lock was MANUALLY RESET by admin.")

    def check_daily_loss(self, today_closed_pnl: float, today_deals_count: int = 0) -> tuple[bool, float, float, float]:
        """
        Проверяет соблюдение Hard Daily Drawdown Limit.
        Возвращает (is_locked, today_net_pnl, today_drawdown_pct, daily_start_balance).
        """
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        # Автоматический сброс дневного замка при наступлении нового торгового дня UTC (00:00)
        if today_str != self.current_trade_date:
            logger.info("New trading day UTC: %s (was %s). Resetting daily loss lock.", today_str, self.current_trade_date)
            self.current_trade_date = today_str
            self.daily_loss_locked = False
            self.daily_lock_reason = ""
            self.daily_lock_alerted_date = ""

        is_online, _ = self.is_mt5_online()
        if not is_online:
            return self.daily_loss_locked, 0.0, 0.0, 0.0

        bal = float(self.mt5_telemetry.get("balance", 0.0))
        eq = float(self.mt5_telemetry.get("equity", 0.0))
        if bal <= 0:
            return self.daily_loss_locked, 0.0, 0.0, 0.0

        floating_pnl = eq - bal
        today_net_pnl = today_closed_pnl + floating_pnl
        daily_start_balance = bal - today_closed_pnl
        if daily_start_balance <= 0:
            daily_start_balance = bal

        self.today_stats_cache = {
            "closed_pnl": today_closed_pnl,
            "floating_pnl": floating_pnl,
            "net_pnl": today_net_pnl,
            "start_balance": daily_start_balance,
            "deals_count": today_deals_count,
            "date": today_str
        }

        # Если суммарный дневной результат отрицательный, проверяем превышение лимита
        if today_net_pnl < 0:
            dd_pct = (abs(today_net_pnl) / daily_start_balance) * 100.0
            if dd_pct >= self.max_daily_loss_pct:
                self.daily_loss_locked = True
                self.daily_lock_reason = f"Дневная просадка {dd_pct:.2f}% >= лимита {self.max_daily_loss_pct:.1f}% (PnL: {today_net_pnl:.2f} USD)"
                return True, today_net_pnl, dd_pct, daily_start_balance
            return False, today_net_pnl, dd_pct, daily_start_balance

        return False, today_net_pnl, 0.0, daily_start_balance

    def can_open_new_position(self, symbol: str, trading_mode: str = "prop") -> tuple[bool, str]:
        """
        Институциональная 10-факторная валидация перед открытием ордера на реальные деньги.
        Возвращает (allowed: bool, reason: str).
        """
        # 1. Проверка активности автопилота
        if not self.enabled:
            return False, "Автопилот выключен"

        # 2. Проверка Hard Daily Drawdown Lock
        if self.daily_loss_locked:
            return False, f"Защита от дневного убытка: {self.daily_lock_reason}"

        # 3. Проверка институционального недельного торгового окна
        w_open, w_reason = config.is_weekly_trading_window_open()
        if not w_open:
            return False, f"Торговое окно закрыто: {w_reason}"

        # 4. Проверка связи с терминалом MT5
        is_online, ping = self.is_mt5_online()
        if not is_online:
            return False, f"MT5 не в сети (задержка {ping}с)"

        # 5. Жесткий запрет на золото (XAUUSD) для безопасности депозита
        sym_u = symbol.upper()
        if sym_u in ["XAUUSD", "GOLD"] or "XAU" in sym_u:
            return False, "Золото (XAUUSD) ПОЛНОСТЬЮ ОТКЛЮЧЕНО в настройках робота"

        # 6. Лимит одновременно активных слотов (позиции + отложенные ордера)
        positions = self.mt5_telemetry.get("positions") or []
        orders = self.mt5_telemetry.get("orders") or []
        active_slots = len(positions) + len(orders)
        
        max_slots = 1 if trading_mode == "micro" else self.max_concurrent_positions
        if active_slots >= max_slots:
            return False, f"Лимит слотов исчерпан ({active_slots}/{max_slots} занято)"

        # 7. Защита от дублирования инструмента: не более 1 позиции на пару
        for p in positions:
            if str(p.get("symbol", "")).upper() == sym_u:
                return False, f"Позиция по {sym_u} уже открыта в рынке"
        for o in orders:
            if str(o.get("symbol", "")).upper() == sym_u:
                return False, f"Отложенный ордер по {sym_u} уже выставлен"

        # 8. Защита от корреляции валют (Currency Correlation Exposure Cap)
        # Максимум 2 позиции, содержащие одну и ту же базовую или котируемую валюту
        if len(sym_u) == 6:
            base_curr = sym_u[:3]
            quote_curr = sym_u[3:]
            active_symbols = [str(p.get("symbol", "")).upper() for p in positions] + [str(o.get("symbol", "")).upper() for o in orders]
            base_count = sum(1 for s in active_symbols if base_curr in s)
            quote_count = sum(1 for s in active_symbols if quote_curr in s)
            max_curr = getattr(config, 'MAX_CURRENCY_EXPOSURE', 2)
            if base_count >= max_curr:
                return False, f"Превышен лимит корреляции по валюте {base_curr} ({base_count}/{max_curr})"
            if quote_count >= max_curr:
                return False, f"Превышен лимит корреляции по валюте {quote_curr} ({quote_count}/{max_curr})"

        # 9. Проверка свободной маржи
        bal = float(self.mt5_telemetry.get("balance", 0.0))
        mf = float(self.mt5_telemetry.get("margin_free", 0.0))
        min_usd = getattr(config, 'MIN_MARGIN_FREE_USD', 50.0)
        min_pct = getattr(config, 'MIN_MARGIN_FREE_PCT', 25.0)
        if bal > 0:
            mf_pct = (mf / bal) * 100.0
            if mf < min_usd or mf_pct < min_pct:
                return False, f"Недостаточно маржи: ${mf:.2f} ({mf_pct:.1f}% свободных средств)"

        # 10. Защита от расширения спреда (Spread Spike Guard)
        is_spread_ok, cur_spread, max_s = self.is_spread_acceptable(sym_u)
        if not is_spread_ok:
            return False, f"Спред расширен: {cur_spread:.1f} > max {max_s:.1f} pips"

        return True, "OK"

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

        mode_badge = "Фиксированный лот" if self.lot_mode == "fixed" else "Динамический (% риска)"
        
        # Институциональный блок риск-контроля
        today_cache = getattr(self, 'today_stats_cache', {})
        today_pnl = today_cache.get("net_pnl", (t.get('equity', 0.0) - t.get('balance', 0.0)) if is_online else 0.0)
        today_pnl_sign = "+" if today_pnl >= 0 else ""
        start_b = today_cache.get("start_balance", t.get('balance', 0.0) if is_online else 0.0)
        today_dd_pct = (abs(today_pnl) / start_b * 100.0) if (start_b > 0 and today_pnl < 0) else 0.0
        
        lock_badge = "🟢 Норма" if not self.daily_loss_locked else f"🚨 <b>БЛОКИРОВКА</b> ({self.daily_lock_reason})"
        daily_limit_str = f"{self.max_daily_loss_pct:.1f}%"
        
        bal_val = float(t.get('balance', 0.0))
        mf_val = float(t.get('margin_free', 0.0))
        mf_pct = (mf_val / bal_val * 100.0) if bal_val > 0 else 0.0
        margin_health = f"{mf_pct:.1f}% (Безопасно)" if mf_pct >= 35.0 else f"{mf_pct:.1f}% (Внимание)"

        active_slots = len(t.get('positions') or []) + len(t.get('orders') or [])
        max_slots = self.max_concurrent_positions

        lines = [
            "🖥 <b>ТЕРМИНАЛ METATRADER 5 | ПУЛЬТ УПРАВЛЕНИЯ</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📡 <b>Связь с советником:</b> {status_badge}",
            f"🏢 <b>Брокер:</b> <code>{broker_str}</code>",
            f"👤 <b>Счёт:</b> <code>#{account_str}</code>",
            f"💰 <b>Баланс:</b> <code>{balance_str}</code>",
            f"📈 <b>Эквити (Средства):</b> <code>{equity_str}</code>",
            f"🛡 <b>Свободная маржа:</b> <code>{margin_str}</code> ({margin_health})",
            f"💵 <b>Плавающий PnL:</b> {pnl_badge}",
            f"🤖 <b>Автопилот:</b> {'🟢 ВКЛЮЧЕН' if self.enabled else '🔴 ВЫКЛЮЧЕН'}",
            f"⚖️ <b>Режим объема:</b> <code>{mode_badge}</code>",
            f"📊 <b>Рабочий лот:</b> <code>{self.default_lot:.2f}</code> | <b>Риск:</b> <code>{self.default_risk:.1f}%</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🛡 <b>ИНСТИТУЦИОНАЛЬНЫЙ РИСК-КОНТРОЛЬ:</b>",
            f"🔒 <b>Дневной замок:</b> {lock_badge}",
            f"📉 <b>PnL сегодня:</b> <b>{today_pnl_sign}{today_pnl:.2f} USD</b> ({today_pnl_sign}{today_dd_pct:.1f}%) | <b>Лимит:</b> <code>{daily_limit_str}</code>",
            f"📊 <b>Активные слоты:</b> <code>{active_slots} / {max_slots}</code> (макс. {max_slots} сделки)",
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
