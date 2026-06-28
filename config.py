"""
Strategy definitions and global constants.
"""

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "data" / "polybot.db"

# ── Trading defaults ─────────────────────────────────────────────────────────

DEFAULT_BALANCE = 1000.0
MAX_CONCURRENT_POSITIONS = 5
RISK_PER_TRADE = 0.05          # 5% of balance per trade
SPREAD_COST = 0.01             # 1 cent per trade (entry + exit)
MIN_TRADE_USD = 1.0
STARTUP_GRACE_SEC = 120        # no entries for 2 min after server start
MIN_DTE = 3                    # don't trade markets expiring within 3 days
MIN_GAP_HOURS = 2              # minimum hours between entries per strategy

# ── API endpoints ─────────────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
BINANCE_API = "https://api.binance.com"

# ── Monthly slug template ────────────────────────────────────────────────────

MONTHLY_SLUG_TEMPLATE = "what-price-will-bitcoin-hit-in-{month}-{year}"

# ── The 4 validated strategies ────────────────────────────────────────────────

STRATEGIES = {
    # A and B are disabled — they showed 65%/50% WR in the bar-level analysis but
    # only 27%/18% WR in sequential backtesting. The analysis inflated WR by counting
    # overlapping positions. Only SELL strategies (C, D) survive real sequential trading.
    #
    # "A": { ... BUY reach on BTC momentum — DISABLED },
    # "B": { ... BUY dip on RSI oversold — DISABLED },
    "C": {
        "name": "SELL reach on RSI oversold",
        "description": "When RSI(14) < 35, sell YES (buy NO) on reach strikes near ATM. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "reach",
        "trigger_type": "rsi",
        "rsi_threshold": 35,
        "rsi_direction": "below",
        "moneyness_min": -5.0,
        "moneyness_max": 5.0,
        "hold_hours": 24,
        "expected_wr": 80,
        "priority": 2,
        "max_entries_per_signal": 1,
    },
    "D": {
        "name": "SELL dip on BTC surge",
        "description": "When BTC surges >1.5% in 1h, sell YES (buy NO) on dip strikes 0-10% ITM. Hold 4h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "dip",
        "trigger_type": "btc_momentum",
        "btc_1h_threshold": 1.5,
        "moneyness_min": 0.0,
        "moneyness_max": 10.0,
        "hold_hours": 4,
        "expected_wr": 90,
        "priority": 1,
        "max_entries_per_signal": 1,
    },
}
