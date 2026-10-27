import unittest
import asyncio
import config
from trading.execution_bridge import ExecutionBridge
from unittest.mock import patch, AsyncMock

class TestStateRestoration(unittest.TestCase):
    def test_config_defaults_are_safe(self):
        """
        Tests that configuration defaults are safe for prop challenges:
        - AUTOTRADE_ENABLED must default to False
        - AUTOTRADE_DEFAULT_RISK must default to 0.25%
        - AUTOTRADE_DEFAULT_LOT must default to 0.01
        """
        self.assertFalse(config.AUTOTRADE_ENABLED, "AUTOTRADE_ENABLED must default to False!")
        self.assertEqual(config.AUTOTRADE_DEFAULT_RISK, 0.25, "Default risk must be 0.25%!")
        self.assertEqual(config.AUTOTRADE_DEFAULT_LOT, 0.01, "Default lot must be 0.01!")

    def test_bridge_does_not_auto_enable_from_db_when_config_false(self):
        """
        Tests that ExecutionBridge refuses to enable autotrade on reboot
        even if SQLite DB had enabled=1, whenever config.AUTOTRADE_ENABLED is False.
        """
        async def run_test():
            bridge = ExecutionBridge()
            with patch('db.database.get_bot_setting', new_callable=AsyncMock) as mock_get:
                mock_get.side_effect = lambda key, default="": "true" if "enabled" in key else default
                await bridge.load_settings_from_db()
                # Must remain False because config.AUTOTRADE_ENABLED is False
                self.assertFalse(bridge.enabled, "ExecutionBridge must NOT enable autotrade on reboot if config.AUTOTRADE_ENABLED is False")

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
