"""
Smart Trader Bot — Execution Bridge Manager.
Координирует автоматическое исполнение ордеров через MetaTrader 4/5 и внешние вебхуки.
"""

import logging
from typing import Optional, Dict, Any

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

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        logger.info("Auto-Trading Bridge enabled set to: %s", enabled)

    def set_risk(self, risk_percent: float):
        self.default_risk = max(0.1, min(5.0, risk_percent))
        logger.info("Auto-Trading Bridge risk set to: %.2f%%", self.default_risk)

    def set_lot(self, lot: float):
        self.default_lot = max(0.01, min(10.0, lot))
        logger.info("Auto-Trading Bridge lot set to: %.2f", self.default_lot)

    def get_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "risk_percent": self.default_risk,
            "default_lot": self.default_lot,
            "terminals_connected": len(self._connected_terminals),
        }


# Глобальный синглтон моста
bridge_manager = ExecutionBridge()
