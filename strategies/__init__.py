# Smart Trader Bot — Strategies Package

from strategies.ict_smc import ICTSMCStrategy
from strategies.supply_demand import SupplyDemandStrategy
from strategies.wyckoff import WyckoffStrategy
from strategies.breakout_retest import BreakoutRetestStrategy
from strategies.scalping import ScalpingStrategy
from strategies.volume_analysis import VolumeAnalysisStrategy

ALL_STRATEGIES = [
    ICTSMCStrategy(),
    SupplyDemandStrategy(),
    WyckoffStrategy(),
    BreakoutRetestStrategy(),
    ScalpingStrategy(),
    VolumeAnalysisStrategy(),
]

STRATEGY_MAP = {s.short_name: s for s in ALL_STRATEGIES}
