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
BINANCE_API = "https://data-api.binance.vision"

# ── Monthly slug template ────────────────────────────────────────────────────

MONTHLY_SLUG_TEMPLATE = "what-price-will-bitcoin-hit-in-{month}-{year}"

# ── The 5 all-weather strategies (June 2026 validated) ───────────────────────
# All profitable across UP, DOWN, and FLAT BTC regimes.
# Backtested: +49.8% ROI, 68.8% WR, 33.2% max DD at 5% risk.

STRATEGIES = {
    "A": {
        "name": "SELL reach OTM on momentum",
        "description": "When BTC bounces (1h > +0.5%), sell YES on reach strikes 5-20% OTM priced $0.10-$0.55. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "reach",
        "rsi_min": 15,
        "rsi_max": 80,
        "btc_1h_min": 0.5,
        "btc_1h_max": 99.0,
        "moneyness_min": -20.0,
        "moneyness_max": -5.0,
        "entry_price_min": 0.10,
        "entry_price_max": 0.55,
        "hold_hours": 24,
        "expected_wr": 58,
        "priority": 2,
    },
    "B": {
        "name": "SELL reach OTM on weakness",
        "description": "When BTC weakens (1h < -0.5%) and RSI > 45, sell YES on reach strikes 5-20% OTM priced under $0.30. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "reach",
        "rsi_min": 45,
        "rsi_max": 100,
        "btc_1h_min": -99.0,
        "btc_1h_max": -0.5,
        "moneyness_min": -20.0,
        "moneyness_max": -5.0,
        "entry_price_min": 0.04,
        "entry_price_max": 0.30,
        "hold_hours": 24,
        "expected_wr": 76,
        "priority": 1,
    },
    "C": {
        "name": "SELL reach ATM on weakness",
        "description": "When BTC weakens (1h < -0.5%) and RSI < 55, sell YES on reach strikes near ATM priced $0.15-$0.65. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "reach",
        "rsi_min": 0,
        "rsi_max": 55,
        "btc_1h_min": -99.0,
        "btc_1h_max": -0.5,
        "moneyness_min": -5.0,
        "moneyness_max": 5.0,
        "entry_price_min": 0.15,
        "entry_price_max": 0.65,
        "hold_hours": 24,
        "expected_wr": 80,
        "priority": 3,
    },
    "D": {
        "name": "SELL reach OTM 8h on surge",
        "description": "When BTC surges (1h > +1.0%), sell YES on reach strikes 5-20% OTM priced $0.15-$0.55. Hold 8h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "reach",
        "rsi_min": 20,
        "rsi_max": 70,
        "btc_1h_min": 1.0,
        "btc_1h_max": 99.0,
        "moneyness_min": -20.0,
        "moneyness_max": -5.0,
        "entry_price_min": 0.15,
        "entry_price_max": 0.55,
        "hold_hours": 8,
        "expected_wr": 67,
        "priority": 4,
    },
    "E": {
        "name": "SELL dip ATM on BTC surge",
        "description": "When BTC surges (1h > +1.0%), sell YES on dip strikes near ATM priced $0.10-$0.50. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "dip",
        "rsi_min": 25,
        "rsi_max": 75,
        "btc_1h_min": 1.0,
        "btc_1h_max": 99.0,
        "moneyness_min": -5.0,
        "moneyness_max": 5.0,
        "entry_price_min": 0.10,
        "entry_price_max": 0.50,
        "hold_hours": 24,
        "expected_wr": 100,
        "priority": 5,
    },
    "F": {
        "name": "SELL dip ATM on BTC flat",
        "description": "When BTC is calm (1h between -0.5% and +0.5%) and RSI 20-65, sell YES on dip strikes near ATM priced $0.30-$0.70. Hold 24h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "dip",
        "rsi_min": 20,
        "rsi_max": 65,
        "btc_1h_min": -0.5,
        "btc_1h_max": 0.5,
        "moneyness_min": -5.0,
        "moneyness_max": 5.0,
        "entry_price_min": 0.30,
        "entry_price_max": 0.70,
        "hold_hours": 24,
        "expected_wr": 93,
        "priority": 6,
    },
    "G": {
        "name": "SELL dip OTM on BTC surge",
        "description": "When BTC surges (1h > +1.0%) and RSI 25-70, sell YES on dip strikes 5-10% OTM priced under $0.20. Hold 12h.",
        "action": "SELL",
        "token_side": "no",
        "market_type": "dip",
        "rsi_min": 25,
        "rsi_max": 70,
        "btc_1h_min": 1.0,
        "btc_1h_max": 99.0,
        "moneyness_min": -10.0,
        "moneyness_max": 0.0,
        "entry_price_min": 0.03,
        "entry_price_max": 0.20,
        "hold_hours": 12,
        "expected_wr": 94,
        "priority": 7,
    },
    "H": {
        "name": "BUY reach OTM on positive BTC",
        "description": "When BTC is positive (1h > 0%) and RSI 30-70, buy YES on reach strikes 10-20% OTM priced $0.30-$0.70. Hold 12h.",
        "action": "BUY",
        "token_side": "yes",
        "market_type": "reach",
        "rsi_min": 30,
        "rsi_max": 70,
        "btc_1h_min": 0.0,
        "btc_1h_max": 99.0,
        "moneyness_min": -20.0,
        "moneyness_max": -10.0,
        "entry_price_min": 0.30,
        "entry_price_max": 0.70,
        "hold_hours": 12,
        "expected_wr": 77,
        "priority": 8,
    },
}
