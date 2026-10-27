import unittest
from datetime import datetime, timezone, time
from sessions.trading_sessions import TradingSessions
import config
from trading.execution_bridge import ExecutionBridge
from unittest.mock import patch

class TestIntradayTimeAndSessions(unittest.TestCase):
    def setUp(self):
        self.sessions = TradingSessions()
        self.bridge = ExecutionBridge()

    def test_dynamic_dst_session_boundaries(self):
        """
        Tests that session hours in UTC are computed dynamically based on
        Europe/London and America/New_York timezones.
        """
        data = self.sessions._get_dynamic_sessions_data()
        session_names = [s["name"] for s in data]
        self.assertIn("London Session", session_names)
        self.assertIn("New York Session", session_names)
        self.assertIn("London-NY Overlap", session_names)

        # Check London session: start must be a valid time object
        london = next(s for s in data if s["name"] == "London Session")
        self.assertIsInstance(london["start"], time)
        self.assertIsInstance(london["end"], time)

    def test_rollover_window_detection(self):
        """
        Tests is_rollover_window:
        - 20:45 UTC must return True (inside rollover)
        - 14:00 UTC must return False (outside rollover)
        """
        # Mock time at 20:45 UTC
        dt_rollover = datetime(2026, 10, 27, 20, 45, tzinfo=timezone.utc)
        with patch('config.datetime') as mock_dt:
            mock_dt.now.return_value = dt_rollover
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            in_roll, _ = config.is_rollover_window()
            self.assertTrue(in_roll, "Expected 20:45 UTC to be in rollover window")

        # Mock time at 14:00 UTC
        dt_normal = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
        with patch('config.datetime') as mock_dt:
            mock_dt.now.return_value = dt_normal
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            in_roll, _ = config.is_rollover_window()
            self.assertFalse(in_roll, "Expected 14:00 UTC to be outside rollover window")

    def test_eod_cutoff_blocks_new_trades(self):
        """
        Tests that can_open_new_position blocks opening new trades
        when time reaches EOD cutoff (20:30 UTC).
        """
        self.bridge.enabled = True
        self.bridge.daily_loss_locked = False
        with patch.object(self.bridge, 'is_mt5_online', return_value=(True, 0.05)), \
             patch('config.is_weekly_trading_window_open', return_value=(True, "")):

            # Mock 20:35 UTC (Mon-Thu EOD cutoff reached)
            dt_cutoff = datetime(2026, 10, 27, 20, 35, tzinfo=timezone.utc)
            with patch('trading.execution_bridge.datetime') as mock_dt:
                mock_dt.now.return_value = dt_cutoff
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                allowed, reason = self.bridge.can_open_new_position("XAUUSD")
                self.assertFalse(allowed)
                self.assertIn("EOD Flat", reason)

if __name__ == "__main__":
    unittest.main()
