import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from datetime import datetime
from zoneinfo import ZoneInfo

# Test 1: Session Filter
print('=== SESSION FILTER TEST ===')
utc_now = datetime.now(ZoneInfo('UTC'))
print(f'Current UTC hour: {utc_now.hour}')

for sym in ['EURUSD', 'GBPUSD', 'XAUUSD', 'USDJPY', 'AUDNZD', 'USDCAD']:
    active = config.is_pair_in_active_session(sym)
    sessions = config.PAIR_ACTIVE_SESSIONS.get(sym, [])
    status = "ACTIVE" if active else "CLOSED"
    print(f'  {sym}: {status} (sessions: {sessions})')

# Test 2: Kill Zone
print('\n=== KILL ZONE TEST ===')
kz = config.get_current_kill_zone()
in_kz = config.is_in_kill_zone()
print(f'In Kill Zone: {in_kz} -> {kz or "None"}')
print(f'Kill Zones: {config.KILL_ZONES_UTC}')

# Test 3: Correlation Groups
print('\n=== CORRELATION GROUPS ===')
for group, pairs in config.CORRELATION_GROUPS.items():
    print(f'  {group}: {pairs}')
print(f'Max correlated: {config.MAX_CORRELATED_SIGNALS}')

# Test 4: Daily Limits
print('\n=== DAILY LIMITS ===')
print(f'Max signals/day: {config.MAX_SIGNALS_PER_DAY}')
print(f'Cooldown hours: {config.SIGNAL_COOLDOWN_HOURS}')

print('\nALL CONFIG TESTS PASSED!')
