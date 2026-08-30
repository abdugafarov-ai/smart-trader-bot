import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import InstitutionalBacktester
from backtest.equity_chart import generate_equity_curve_chart

async def test_backtest_and_chart():
    print("=== TESTING BACKTEST ENGINE ===")
    tester = InstitutionalBacktester()
    res = await tester.run_backtest(symbol="EURUSD", timeframe="H1", limit=300)
    
    print(f"Symbol: {res.symbol} ({res.timeframe})")
    print(f"Total Trades: {res.total_trades}")
    print(f"Wins: {res.wins}, Losses: {res.losses}, Breakevens: {res.breakevens}")
    print(f"Win Rate: {res.win_rate}%")
    print(f"Total PnL: {res.total_pips:+.1f} pips | R: {res.total_r:+.2f}R")
    print(f"Profit Factor: {res.profit_factor:.2f}")
    print(f"Max Drawdown: -{res.max_drawdown_pips:.1f} pips")
    
    print("\n=== TESTING EQUITY CHART GENERATION ===")
    chart_bytes = generate_equity_curve_chart(
        equity_points=res.equity_curve,
        title="INSTITUTIONAL STRATEGY BACKTEST",
        symbol=f"{res.symbol} ({res.timeframe})",
        total_pnl=res.total_pips,
        win_rate=res.win_rate,
        profit_factor=res.profit_factor,
        max_dd=res.max_drawdown_pips
    )
    print(f"Generated chart: {len(chart_bytes)} bytes")
    
    with open("scratch/test_equity_curve.png", "wb") as f:
        f.write(chart_bytes)
    print("Saved to scratch/test_equity_curve.png")
    print("\nALL BACKTEST TESTS PASSED!")

asyncio.run(test_backtest_and_chart())
