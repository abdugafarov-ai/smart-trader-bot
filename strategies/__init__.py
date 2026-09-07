# Smart Trader Bot — Strategies Package (ICT/SMC Only)

from strategies.ict_smc import ICTSMCStrategy

ALL_STRATEGIES = [
    ICTSMCStrategy(),
]

STRATEGY_MAP = {s.short_name: s for s in ALL_STRATEGIES}
