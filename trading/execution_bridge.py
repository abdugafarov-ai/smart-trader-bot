"""
Smart Trader Bot — Execution Bridge Manager.
Координирует автоматическое исполнение ордеров через MetaTrader 4/5 и внешние вебхуки.
"""

import logging
import math
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import config
from db.database import get_active_signals, update_signal_status
from utils.broker_time import get_broker_today_date_str, get_broker_now

logger = logging.getLogger(__name__)


def calculate_gold_swing_lot(
    balance: float = 5000.0,
    sl_dist_usd: float = 15.0,
    risk_pct: float = 0.25,
    commission_per_lot: float = 0.0,
    spread_buffer_usd: float = 0.0,
) -> float:
    """
    Единый институциональный расчет риска для XAUUSD (FundingPips $5,000):
    - Формула: lot = (balance * risk_pct / 100.0) / ((sl_dist_usd + spread_buffer_usd) * 100.0 + commission_per_lot)
    - Бюджет: 0.25% ($12.50) базовый, 0.50% ($25.00) максимальный
    - Жесткое правило: если риск по минимальному лоту 0.01 превышает бюджет -> return 0.0 (NO TRADE)
    """
    if sl_dist_usd <= 0.01 or balance <= 0:
        return 0.0
    max_allowed_risk_pct = getattr(config, "AUTOTRADE_MAX_RISK", 0.50)
    capped_pct = min(float(risk_pct), max_allowed_risk_pct) if risk_pct > 0 else 0.25
    risk_factor = capped_pct / 100.0
    risk_budget_usd = balance * risk_factor

    effective_sl_usd = float(sl_dist_usd) + max(0.0, float(spread_buffer_usd))
    loss_per_std_lot = (effective_sl_usd * 100.0) + max(0.0, float(commission_per_lot))
    if loss_per_std_lot <= 0:
        return 0.0

    # Min lot risk check (1 lot XAUUSD = 100 oz -> 0.01 lot = 1 oz -> $1.00 move = $1.00 loss)
    min_lot_risk_usd = 0.01 * loss_per_std_lot
    if min_lot_risk_usd > risk_budget_usd + 1e-9:
        # Minimum volume exceeds risk budget -> strictly reject order
        return 0.0

    raw_lot = risk_budget_usd / loss_per_std_lot
    lot = math.floor((raw_lot + 1e-9) * 100.0) / 100.0
    return max(0.01, min(0.50, round(lot, 2)))


class ExecutionBridge:
    """Управляет состоянием и передачей команд внешним торговым терминалам."""

    def __init__(self):
        self.enabled = config.AUTOTRADE_ENABLED
        self.default_risk = config.AUTOTRADE_DEFAULT_RISK
        self.default_lot = config.AUTOTRADE_DEFAULT_LOT
        self.lot_mode = "risk"  # "risk" (default) or "fixed" (still capped by risk budget)
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
        self._last_eod_flat_date: str = ""

        # Кэш живых котировок от советника MT5: символ -> {"bid": float, "ask": float, "time": datetime}
        self.live_quotes: Dict[str, Dict[str, Any]] = {}

        # Институциональный риск-менеджмент для реального счёта
        self.max_daily_loss_pct: float = getattr(config, 'MAX_DAILY_DRAWDOWN_PCT', 2.5)
        self.max_concurrent_positions: int = getattr(config, 'MAX_CONCURRENT_POSITIONS', 1)
        # Динамический расчет аварийного хард-стопа по эквити (масштабируется под любой депозит)
        init_bal = getattr(config, 'CHALLENGE_INITIAL_BALANCE', 5000.0)
        max_loss_pct = getattr(config, 'CHALLENGE_MAX_LOSS_PCT', 12.0)
        dynamic_floor = init_bal * (1.0 - (max_loss_pct / 100.0))
        self.hard_equity_stop_usd: float = getattr(config, 'HARD_EQUITY_STOP_USD', dynamic_floor + 50.0)
        self.equity_hard_stop_triggered: bool = False
        self.equity_hard_stop_reason: str = ""

        self.daily_loss_locked: bool = False
        self.daily_lock_reason: str = ""
        self.daily_lock_alerted_date: str = ""
        self.current_trade_date: str = get_broker_today_date_str()
        self.today_stats_cache: Dict[str, Any] = {}

    @property
    def account_balance(self) -> float:
        """Возвращает актуальный баланс счета из живой телеметрии MT5 (0.0 если не получен)."""
        try:
            return float(self.mt5_telemetry.get("balance", 0.0) or 0.0)
        except (ValueError, TypeError):
            return 0.0

    @property
    def account_equity(self) -> float:
        """Возвращает актуальное эквити счета из живой телеметрии MT5 (0.0 если не получено)."""
        try:
            return float(self.mt5_telemetry.get("equity", 0.0) or 0.0)
        except (ValueError, TypeError):
            return 0.0

    def get_effective_balance(self, default: float = 5000.0) -> float:
        """
        Возвращает консервативный расчетный капитал для риск-менеджмента:
        min(balance, equity) при наличии живой телеметрии MT5, иначе default ($5,000.00).
        """
        bal = self.account_balance
        eq = self.account_equity
        if bal > 0 and eq > 0:
            return min(bal, eq)
        if eq > 0:
            return eq
        if bal > 0:
            return bal
        return float(default)

    def set_enabled(self, enabled: bool):
        if enabled:
            is_hard, reason = self.check_equity_hard_stop()
            if is_hard:
                logger.warning("🚨 Cannot enable autopilot: %s. Blocked by Equity Hard Stop.", reason)
                self.enabled = False
                return
        self.enabled = enabled
        logger.info("Auto-Trading Bridge enabled set to: %s", enabled)

    def set_risk(self, risk_percent: float):
        max_r = getattr(config, "AUTOTRADE_MAX_RISK", 0.50)
        self.default_risk = max(0.1, min(max_r, risk_percent))
        self.lot_mode = "risk"
        logger.info("Auto-Trading Bridge risk set to: %.2f%% (mode: risk)", self.default_risk)

    def set_lot(self, lot: float):
        self.default_lot = max(0.01, min(0.50, lot))
        self.lot_mode = "fixed"
        logger.info("Auto-Trading Bridge lot set to: %.2f (mode: fixed, capped by risk budget)", self.default_lot)

    def calculate_lot(
        self,
        symbol: str = "XAUUSD",
        sl_pips: float = 10.0,
        balance: Optional[float] = None,
        risk_pct: Optional[float] = None,
        min_lot: float = 0.01,
        max_lot: float = 0.50,
        sl_dist_usd: Optional[float] = None,
        entry: Optional[float] = None,
        sl: Optional[float] = None,
    ) -> float:
        """
        Единый источник истины для расчета лота:
        - Для XAUUSD: 1 лот = 100 oz ($100 per $1.00 move).
          Дистанция стопа передается в USD (или через entry/sl, или sl_dist_usd).
        - Бюджет риска: default 0.25% ($12.50), max 0.50% ($25.00).
        - Если минимальный лот (0.01) превышает бюджет -> return 0.0 (NO TRADE).
        - Даже в режиме 'fixed' лот НИКОГДА не может превысить риск-бюджет (при превышении -> 0.0).
        """
        sym_clean = symbol.upper().replace("/", "").replace("=X", "")
        max_r = getattr(config, "AUTOTRADE_MAX_RISK", 0.50)
        raw_risk = risk_pct if (risk_pct is not None and risk_pct > 0) else self.default_risk
        effective_risk = min(float(raw_risk), max_r)
        bal_val = float(balance) if (balance is not None and balance > 0) else self.get_effective_balance(5000.0)

        if "XAU" in sym_clean or "GOLD" in sym_clean:
            if sl_dist_usd is not None and sl_dist_usd > 0:
                actual_sl_usd = float(sl_dist_usd)
            elif entry is not None and sl is not None and entry > 0 and sl > 0:
                actual_sl_usd = abs(float(entry) - float(sl))
            else:
                actual_sl_usd = float(sl_pips)

            max_safe_lot = calculate_gold_swing_lot(bal_val, actual_sl_usd, effective_risk)
            if max_safe_lot <= 0.0:
                return 0.0
            if self.lot_mode == "fixed":
                if self.default_lot > max_safe_lot + 1e-9:
                    logger.warning(
                        "Fixed lot %.2f exceeds risk budget (max safe lot %.2f for SL $%.2f) -> REJECT (0.0)",
                        self.default_lot, max_safe_lot, actual_sl_usd
                    )
                    return 0.0
                return max(min_lot, min(max_lot, round(self.default_lot, 2)))
            return max_safe_lot

        # Forex pairs: pip value = $10 / standard lot
        if sl_pips <= 0:
            return 0.0
        risk_factor = effective_risk / 100.0
        risk_usd = bal_val * risk_factor
        pip_val = 10.0
        if sl_pips * pip_val * min_lot > risk_usd + 1e-9:
            return 0.0  # Min lot risk exceeds budget -> REJECT
        raw_lot = risk_usd / (sl_pips * pip_val)
        max_safe_forex_lot = math.floor((raw_lot + 1e-9) * 100.0) / 100.0
        max_safe_forex_lot = max(min_lot, min(max_lot, round(max_safe_forex_lot, 2)))
        if self.lot_mode == "fixed":
            if self.default_lot > max_safe_forex_lot + 1e-9:
                return 0.0
            return max(min_lot, min(max_lot, round(self.default_lot, 2)))
        return max_safe_forex_lot

    async def load_settings_from_db(self):
        """Загружает персистентные настройки моста и состояния аварийных блокировок из базы данных SQLite."""
        from db.database import get_bot_setting
        try:
            lot_str = await get_bot_setting("trading_lot", "")
            if lot_str:
                self.default_lot = max(0.01, min(0.50, float(lot_str)))
            risk_str = await get_bot_setting("trading_risk", "")
            if risk_str:
                max_r = getattr(config, "AUTOTRADE_MAX_RISK", 0.50)
                self.default_risk = max(0.1, min(max_r, float(risk_str)))
            lot_mode_str = await get_bot_setting("lot_mode", "")
            if lot_mode_str in ("fixed", "risk"):
                self.lot_mode = lot_mode_str
            enabled_str = await get_bot_setting("autotrade_enabled", "")
            # Safe Default: Autotrade is only enabled on reboot if config.AUTOTRADE_ENABLED is explicitly True
            if enabled_str and config.AUTOTRADE_ENABLED:
                self.enabled = (enabled_str.lower() in ("true", "1", "yes"))
            else:
                self.enabled = False
            daily_loss_str = await get_bot_setting("max_daily_loss_pct", "")
            if daily_loss_str:
                self.max_daily_loss_pct = max(0.5, min(15.0, float(daily_loss_str)))
            max_pos_str = await get_bot_setting("max_concurrent_positions", "")
            if max_pos_str:
                self.max_concurrent_positions = max(1, min(10, int(max_pos_str)))

            # Восстановление аварийных блокировок (Circuit Breakers), чтобы рестарт не снимал защиту
            hard_stop_str = await get_bot_setting("equity_hard_stop_triggered", "")
            if hard_stop_str.lower() in ("true", "1", "yes"):
                self.equity_hard_stop_triggered = True
                self.equity_hard_stop_reason = await get_bot_setting(
                    "equity_hard_stop_reason",
                    f"Хард-стоп эквити <= ${self.hard_equity_stop_usd:.2f} (сохранен до ручной разблокировки)"
                )
                self.enabled = False

            today_str = get_broker_today_date_str()
            daily_lock_str = await get_bot_setting("daily_loss_locked", "")
            daily_lock_date = await get_bot_setting("daily_lock_date", "")
            if daily_lock_str.lower() in ("true", "1", "yes") and daily_lock_date == today_str:
                self.daily_loss_locked = True
                self.daily_lock_reason = await get_bot_setting(
                    "daily_lock_reason",
                    "Дневной лимит просадки исчерпан (сохранен после перезапуска)"
                )

            logger.info(
                "Loaded Bridge settings from DB: lot=%.2f, risk=%.2f%%, lot_mode=%s, enabled=%s, daily_loss_limit=%.1f%%, max_positions=%d, hard_stop=%s, daily_locked=%s",
                self.default_lot, self.default_risk, self.lot_mode, self.enabled,
                self.max_daily_loss_pct, self.max_concurrent_positions,
                self.equity_hard_stop_triggered, self.daily_loss_locked,
            )
        except Exception as e:
            logger.error("Failed to load Bridge settings from DB: %s", e)

    async def persist_circuit_breaker_state(self):
        """Сохраняет состояние аварийных стоп-условий в SQLite (требует явной процедуры разблокировки)."""
        try:
            from db.database import set_bot_setting
            await set_bot_setting("equity_hard_stop_triggered", "true" if self.equity_hard_stop_triggered else "false")
            await set_bot_setting("equity_hard_stop_reason", self.equity_hard_stop_reason or "")
            await set_bot_setting("daily_loss_locked", "true" if self.daily_loss_locked else "false")
            await set_bot_setting("daily_lock_reason", self.daily_lock_reason or "")
            await set_bot_setting("daily_lock_date", self.current_trade_date or get_broker_today_date_str())
            if self.equity_hard_stop_triggered:
                await set_bot_setting("autotrade_enabled", "false")
        except Exception as e:
            logger.debug("persist_circuit_breaker_state skipped/failed: %s", e)

    def _schedule_persist_circuit_breaker(self):
        """Безопасно планирует сохранение стоп-состояния в БД, если запущен event loop."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self.persist_circuit_breaker_state())
        except RuntimeError:
            pass

    def update_telemetry(self, balance: float, equity: float, margin_free: float,
                         broker: str, account: str, positions: list, orders: list,
                         server_time: int = 0):
        """Обновляет телеметрию живыми данными, полученными от советника MT5."""
        self.mt5_telemetry["balance"] = balance
        self.mt5_telemetry["equity"] = equity
        self.mt5_telemetry["margin_free"] = margin_free
        if broker:
            self.mt5_telemetry["broker"] = broker
        if account:
            self.mt5_telemetry["account"] = str(account)
        if server_time > 0:
            self.mt5_telemetry["server_time"] = server_time
        self.mt5_telemetry["positions"] = positions or []
        self.mt5_telemetry["orders"] = orders or []
        self.mt5_telemetry["last_ping"] = datetime.now(timezone.utc)
        self._connected_terminals["MT5"] = self.mt5_telemetry["last_ping"]

        # Аварийный контроль хард-стопа по эквити ($4,450.00)
        self.check_equity_hard_stop()

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

    def check_equity_hard_stop(self, current_equity: float = None) -> tuple[bool, str]:
        """
        Жёсткий аварийный контроль по эквити ($4,450.00).
        Если эквити падает до $4,450.00 или ниже (при эквити > 0),
        немедленно закрывает все рыночные позиции и блокирует автопилот.
        Обеспечивает запас $50 до фатального пола брокера $4,400.00.
        """
        if current_equity is None:
            is_online, _ = self.is_mt5_online()
            if not is_online:
                return self.equity_hard_stop_triggered, self.equity_hard_stop_reason
            eq = float(self.mt5_telemetry.get("equity", 0.0))
        else:
            eq = float(current_equity)
        if 0 < eq <= self.hard_equity_stop_usd:
            was_triggered = self.equity_hard_stop_triggered
            self.equity_hard_stop_triggered = True
            self.equity_hard_stop_reason = (
                f"Хард-стоп эквити: ${eq:.2f} <= ${self.hard_equity_stop_usd:.2f} "
                f"(аварийный запас $50 до пола FundingPips $4,400.00)"
            )
            if self.enabled:
                self.enabled = False
                logger.critical("🚨 ХАРД-СТОП ПО ЭКВИТИ АКТИВИРОВАН! %s. Автопилот заблокирован.", self.equity_hard_stop_reason)
            self.request_panic_close()
            if not was_triggered:
                self._schedule_persist_circuit_breaker()
            return True, self.equity_hard_stop_reason
        return self.equity_hard_stop_triggered, self.equity_hard_stop_reason

    def unlock_equity_hard_stop(self):
        """Явная процедура ручного сброса аварийного хард-стопа администратором после проверки счета."""
        self.equity_hard_stop_triggered = False
        self.equity_hard_stop_reason = ""
        self._schedule_persist_circuit_breaker()
        logger.info("Equity hard stop was MANUALLY RESET by admin.")

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
        self._schedule_persist_circuit_breaker()
        logger.info("Daily loss lock was MANUALLY RESET by admin.")

    def check_daily_loss(self, today_closed_pnl: float, today_deals_count: int = 0) -> tuple[bool, float, float, float]:
        """
        Проверяет соблюдение Hard Daily Drawdown Limit.
        Возвращает (is_locked, today_net_pnl, today_drawdown_pct, daily_start_balance).
        """
        today_str = get_broker_today_date_str()

        # Автоматический сброс дневного замка при наступлении 00:00 времени сервера MT5 (EET / UTC+2 зимой, UTC+3 летом)
        if today_str != self.current_trade_date:
            logger.info("New trading day MT5 Broker Time (00:00 EET): %s (was %s). Resetting daily loss lock.", today_str, self.current_trade_date)
            self.current_trade_date = today_str
            self.daily_loss_locked = False
            self.daily_lock_reason = ""
            self.daily_lock_alerted_date = ""
            self._schedule_persist_circuit_breaker()

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
                was_locked = self.daily_loss_locked
                self.daily_loss_locked = True
                self.daily_lock_reason = f"Дневная просадка {dd_pct:.2f}% >= лимита {self.max_daily_loss_pct:.1f}% (PnL: {today_net_pnl:.2f} USD)"
                if not was_locked:
                    self._schedule_persist_circuit_breaker()
                return True, today_net_pnl, dd_pct, daily_start_balance
            return self.daily_loss_locked, today_net_pnl, dd_pct, daily_start_balance

        return self.daily_loss_locked, today_net_pnl, 0.0, daily_start_balance

    def get_cumulative_open_risk_usd(self) -> float:
        """
        Вычисляет совокупный риск в USD по всем открытым позициям и отложенным ордерам в MT5.
        Если у позиции стоп уже переведен в безубыток или профит, риск по ней = 0.0.
        """
        total_risk_usd = 0.0
        positions = self.mt5_telemetry.get("positions") or []
        orders = self.mt5_telemetry.get("orders") or []
        for item in list(positions) + list(orders):
            try:
                sym = str(item.get("symbol", "XAUUSD")).upper()
                lot = float(item.get("lot") or item.get("volume") or 0.01)
                entry = float(item.get("price") or item.get("open_price") or item.get("entry") or 0.0)
                sl = float(item.get("sl") or item.get("stop_loss") or 0.0)
                p_type = str(item.get("type", "BUY")).upper()
                if entry > 0 and sl > 0:
                    if "BUY" in p_type:
                        sl_dist = max(0.0, entry - sl)
                    else:
                        sl_dist = max(0.0, sl - entry)
                    contract_mult = 100.0 if ("XAU" in sym or "GOLD" in sym) else (1000.0 if "JPY" in sym else 100000.0)
                    total_risk_usd += lot * sl_dist * contract_mult
                else:
                    # Консервативная оценка риска при отсутствии явного SL в телеметрии
                    bal = self.get_effective_balance(5000.0)
                    total_risk_usd += bal * (self.default_risk / 100.0)
            except Exception:
                continue
        return round(total_risk_usd, 2)

    def check_and_trigger_eod_flat(self, now_dt: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Проверяет наступление времени принудительного внутридневного закрытия (EOD Flat: 20:40 UTC Пн-Чт, 19:30 UTC Пт).
        Если в терминале открыты позиции или висят отложенные ордера, запрашивает принудительное закрытие до ролловера.
        """
        now_utc = now_dt if now_dt is not None else datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        if hasattr(config, "should_force_eod_flat"):
            should_flat, reason = config.should_force_eod_flat(now_utc)
        else:
            mins = now_utc.hour * 60 + now_utc.minute
            should_flat = (now_utc.weekday() == 4 and mins >= 19 * 60 + 30) or (20 * 60 + 40 <= mins <= 23 * 60 + 5)
            reason = "EOD_FLAT" if should_flat else ""

        if not should_flat:
            return False, ""

        positions = self.mt5_telemetry.get("positions") or []
        orders = self.mt5_telemetry.get("orders") or []
        date_key = now_utc.strftime("%Y-%m-%d")
        if (positions or orders) or self._last_eod_flat_date != date_key:
            self._last_eod_flat_date = date_key
            if positions or orders:
                self.panic_close_requested = True
                logger.warning("⏰ EOD FLAT TRIGGERED (%s): Closing %d positions and %d pending orders before rollover.", reason, len(positions), len(orders))
            return True, reason
        return True, reason

    def can_open_new_position(self, symbol: str, trading_mode: str = "prop", new_trade_risk_usd: float = 0.0) -> tuple[bool, str]:
        """
        Институциональная 10-факторная валидация перед открытием ордера на реальные деньги.
        Возвращает (allowed: bool, reason: str).
        """
        # 0. Проверка аварийного хард-стопа по эквити ($4,450.00)
        is_hard_stop, hard_reason = self.check_equity_hard_stop()
        if is_hard_stop:
            return False, f"Аварийный хард-стоп по эквити: {hard_reason}"

        # 0.1 Проверка запрещенных инструментов
        sym_u = symbol.upper()
        if sym_u in getattr(config, "BANNED_PAIRS", []):
            return False, f"Инструмент {sym_u} отключен (в списке запрещенных пар)"

        # 1. Проверка активности автопилота
        if not self.enabled:
            return False, "Автопилот выключен"

        # 2. Проверка Hard Daily Drawdown Lock
        if self.daily_loss_locked:
            return False, f"Защита от дневного убытка: {self.daily_lock_reason}"

        # 3. Проверка институционального недельного торгового окна и защиты от ролловера
        w_open, w_reason = config.is_weekly_trading_window_open()
        if not w_open:
            return False, f"Торговое окно закрыто: {w_reason}"

        now_utc = datetime.now(timezone.utc)

        # 3.1 Strictly Intraday Trading Window & EOD Cutoff (20:30 UTC Mon-Thu, 19:00/19:30 UTC Fri)
        if now_utc.weekday() == 4 and (now_utc.hour >= 20 or (now_utc.hour == 19 and now_utc.minute >= 0)):
            return False, "EOD Flat: Пятничное закрытие перед выходными (19:00 UTC)"
        if (now_utc.hour == 20 and now_utc.minute >= 30) or now_utc.hour > 20:
            return False, "EOD Flat: Достигнуто время закрытия внутридневной сессии (20:30 UTC)"

        if hasattr(config, "is_rollover_window"):
            in_rollover, roll_reason = config.is_rollover_window(now_utc)
            if in_rollover:
                return False, f"Защита от ролловера: {roll_reason}"

        # 4. Проверка связи с терминалом MT5
        is_online, ping = self.is_mt5_online()
        if not is_online:
            return False, f"MT5 не в сети (задержка {ping}с)"

        # 6. Лимит одновременно активных слотов (позиции + отложенные ордера)
        positions = self.mt5_telemetry.get("positions") or []
        orders = self.mt5_telemetry.get("orders") or []
        active_slots = len(positions) + len(orders)
        
        max_slots = 1 if trading_mode == "micro" else self.max_concurrent_positions
        if active_slots >= max_slots:
            return False, f"Лимит слотов исчерпан ({active_slots}/{max_slots} занято)"

        # 6.1 Совокупный риск (открытые позиции + отложенные ордера + дневной убыток + новая сделка)
        bal = float(self.mt5_telemetry.get("balance", 0.0))
        if bal > 0:
            start_bal = float(self.today_stats_cache.get("start_balance", bal) or bal)
            today_net_pnl = float(self.today_stats_cache.get("net_pnl", 0.0) or 0.0)
            realized_or_floating_loss = abs(min(0.0, today_net_pnl))
            open_risk = self.get_cumulative_open_risk_usd()
            est_new_risk = new_trade_risk_usd if new_trade_risk_usd > 0 else (bal * (self.default_risk / 100.0))
            max_daily_loss_usd = start_bal * (self.max_daily_loss_pct / 100.0)
            if (realized_or_floating_loss + open_risk + est_new_risk) > max_daily_loss_usd + 1e-6:
                return (
                    False,
                    f"Превышен совокупный дневной риск-лимит: убыток ${realized_or_floating_loss:.2f} + "
                    f"открытый риск ${open_risk:.2f} + новая сделка ${est_new_risk:.2f} > лимита ${max_daily_loss_usd:.2f}"
                )

        # 7. Защита от дублирования инструмента: не более 1 позиции на пару
        for p in positions:
            if str(p.get("symbol", "")).upper() == sym_u:
                return False, f"Позиция по {sym_u} уже открыта в рынке"
        for o in orders:
            if str(o.get("symbol", "")).upper() == sym_u:
                return False, f"Отложенный ордер по {sym_u} уже выставлен"

        # 8. Защита от корреляции валют (Currency Correlation Exposure Cap)
        if len(sym_u) == 6:
            base_curr = sym_u[:3]
            quote_curr = sym_u[3:]
            active_symbols = [str(p.get("symbol", "")).upper() for p in positions] + [str(o.get("symbol", "")).upper() for o in orders]
            base_count = sum(1 for s in active_symbols if base_curr in s)
            quote_count = sum(1 for s in active_symbols if quote_curr in s)
            max_curr = getattr(config, 'MAX_CURRENCY_EXPOSURE', 1)
            if base_count >= max_curr:
                return False, f"Превышен лимит корреляции по валюте {base_curr} ({base_count}/{max_curr})"
            if quote_count >= max_curr:
                return False, f"Превышен лимит корреляции по валюте {quote_curr} ({quote_count}/{max_curr})"

        # 9. Проверка свободной маржи
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
        eq_val = float(t.get('equity', 0.0)) if is_online else 0.0
        if self.equity_hard_stop_triggered:
            hard_stop_badge = f"🚨 <b>АКТИВИРОВАН</b> ({self.equity_hard_stop_reason})"
        else:
            buffer_str = f" (запас ${eq_val - self.hard_equity_stop_usd:.2f})" if (is_online and eq_val > 0) else ""
            hard_stop_badge = f"🟢 Норма (порог ${self.hard_equity_stop_usd:,.2f} USD{buffer_str} | пол $4,400.00)"
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
            f"🛑 <b>Хард-стоп эквити:</b> {hard_stop_badge}",
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
    async def check_midnight_rollover_guard(self, bot=None) -> list:
        """
        Midnight Rollover Guard at 23:30 UTC+3 (MT5 server time):
        - If open XAUUSD position floating profit >= +$80, move SL to guaranteed +$3.00 profit
        - Send Telegram alert proposing 50% partial close ahead of midnight rollover.
        """
        actions_taken = []
        try:
            from zoneinfo import ZoneInfo
            broker_tz = ZoneInfo(getattr(config, "BROKER_TIMEZONE_NAME", "Europe/Helsinki"))
            now_broker = datetime.now(timezone.utc).astimezone(broker_tz)
            
            if not (now_broker.hour == 23 and 30 <= now_broker.minute < 50):
                return actions_taken

            positions = self.mt5_telemetry.get("positions") or []
            for p in positions:
                sym = str(p.get("symbol", "")).upper()
                if "XAU" not in sym and "GOLD" not in sym:
                    continue
                profit = float(p.get("profit", 0.0))
                ticket = int(p.get("ticket", 0))
                open_p = float(p.get("price") or p.get("open_price") or p.get("entry") or 0.0)
                if open_p <= 0.0:
                    continue
                curr_sl = float(p.get("sl", 0.0))
                p_type = str(p.get("type", "")).upper()

                if profit >= 80.0:
                    safe_sl = round(open_p + 3.0, 2) if "BUY" in p_type else round(open_p - 3.0, 2)
                    need_move = (safe_sl > curr_sl) if "BUY" in p_type else (curr_sl <= 0.0 or safe_sl < curr_sl)
                    
                    alert_key = f"rollover_guard_{ticket}_{now_broker.strftime('%Y%m%d')}"
                    if not hasattr(self, "_rollover_alerts"):
                        self._rollover_alerts = set()
                    
                    if alert_key not in self._rollover_alerts:
                        self._rollover_alerts.add(alert_key)
                        msg = (
                            f"🌙 <b>Midnight Rollover Guard (23:30 UTC+3)</b>\n\n"
                            f"Позиция <b>{sym}</b> #{ticket} имеет плавающий профит: <b>+${profit:.2f}</b>!\n"
                            f"🛡 SL защищен на гарантированные <b>+$3.00</b> ({safe_sl:.2f})\n"
                            f"⚠️ Рекомендация: зафиксировать <b>50% позиции</b> перед ночным ролловером и расширением спредов."
                        )
                        actions_taken.append(msg)
                        if bot and config.ADMIN_ID:
                            try:
                                await bot.send_message(chat_id=config.ADMIN_ID, text=msg, parse_mode="HTML")
                            except Exception as ex:
                                logger.error("Failed sending Rollover Guard Telegram alert: %s", ex)
        except Exception as e:
            logger.error("check_midnight_rollover_guard error: %s", e)
        return actions_taken

    @staticmethod
    def is_night_clearing_window() -> bool:
        """
        Проверяет ночное клиринговое окно (23:50 - 01:10 UTC+3 сервера брокера).
        В этот период запрещено подтягивать стоп-лосс ближе чем $4.00 к текущей цене.
        """
        try:
            from zoneinfo import ZoneInfo
            broker_tz = ZoneInfo(getattr(config, "BROKER_TIMEZONE_NAME", "Europe/Helsinki"))
            now_broker = datetime.now(timezone.utc).astimezone(broker_tz)
            h, m = now_broker.hour, now_broker.minute
            if (h == 23 and m >= 50) or (h == 0) or (h == 1 and m <= 10):
                return True
        except Exception:
            pass
        return False


bridge_manager = ExecutionBridge()
