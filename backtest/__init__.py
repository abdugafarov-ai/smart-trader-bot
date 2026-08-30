"""
Backtesting and Analytics Package.
"""
from .backtester import InstitutionalBacktester, BacktestResult, BacktestTrade
from .equity_chart import generate_equity_curve_chart

__all__ = ["InstitutionalBacktester", "BacktestResult", "BacktestTrade", "generate_equity_curve_chart"]
