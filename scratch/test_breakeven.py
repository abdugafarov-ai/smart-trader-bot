import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import init_db, save_signal, update_signal_sl, get_active_signals, update_signal_status

async def test_breakeven():
    print("=== TESTING DATABASE & BREAKEVEN DB UPDATES ===")
    await init_db()
    
    # Save a test signal
    sig_id = await save_signal(
        symbol="EURUSD",
        direction="LONG",
        order_type="BUY_LIMIT",
        tag_emoji="🔥",
        stars=5,
        current_price=1.1620,
        entry_price=1.1600,
        stop_loss=1.1570,   # Risk = 30 pips
        take_profit_1=1.1675, # TP1 = 75 pips
        take_profit_2=1.1720,
        risk_reward=2.5,
        strategies_agreed="ICT: LONG",
        timeframes_agreed="H4: LONG",
    )
    print(f"Saved test signal id={sig_id}")
    
    # Simulate activation
    await update_signal_status(sig_id, "ACTIVE")
    active = await get_active_signals()
    my_sig = next((s for s in active if s['id'] == sig_id), None)
    print(f"Active signal status: {my_sig['status']}, breakeven_applied={my_sig.get('breakeven_applied')}")
    
    # Simulate breakeven trigger (SL moved to entry 1.1600)
    await update_signal_sl(sig_id, 1.1600, breakeven=True)
    active = await get_active_signals()
    my_sig = next((s for s in active if s['id'] == sig_id), None)
    print(f"After breakeven update: SL={my_sig['stop_loss']}, breakeven_applied={my_sig.get('breakeven_applied')}")
    assert my_sig['stop_loss'] == 1.1600
    assert my_sig.get('breakeven_applied') == 1
    
    # Cleanup test signal
    await update_signal_status(sig_id, "CANCELLED", close_price=1.1600, pnl_pips=0.0, result="Test Cleanup")
    print("Breakeven DB logic verified 100%!")

asyncio.run(test_breakeven())
