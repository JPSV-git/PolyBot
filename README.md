# PolyBot

Polymarket BTC binary options trading bot with 4 validated strategies, backtesting, and paper trading.

## Quick Start

```bash
cd /Users/valemacmini/Desktop/PolyBot
pip install -r requirements.txt
python run.py
# Open http://localhost:8000
```

## Architecture

```
PolyBot/
├── app.py             FastAPI server + 7 background loops
├── config.py          Strategy definitions + constants
├── db.py              SQLite (WAL mode) schema + CRUD
├── polymarket.py      Gamma API (discovery) + CLOB API (order books, history)
├── binance.py         BTC price + indicators (RSI, momentum)
├── strategies.py      Strategy evaluation engine
├── backtest.py        Backtest engine on historical data
├── paper_trader.py    Live paper trading simulation
└── static/            Frontend (vanilla JS + Chart.js)
```

## The 4 Strategies

Validated on June 2026 data with split-half validation, after 1-cent spread costs:

| ID | Action | When | Hold | WR | Avg Return |
|----|--------|------|------|----|------------|
| **A** | BUY YES on reach | BTC 1h > +0.5%, strike 2-10% OTM | 4h | 65% | +7% |
| **B** | BUY YES on dip | RSI < 30, strike 0-10% ITM | 24h | 50% | +35% |
| **C** | SELL YES on reach | RSI < 35, strike near ATM | 24h | 80% | +23% |
| **D** | SELL YES on dip | BTC 1h > +1.5%, strike 0-10% ITM | 4h | 90% | +26% |

**How they complement each other:**
- **BTC goes UP** → A (buy reach) + D (sell dip) fire
- **BTC goes DOWN** → B (buy dip) + C (sell reach) fire
- Profits in both directions. Combined WR: ~68%, June ROI: +45.6%

**"SELL YES" means BUY NO tokens** — economically equivalent, mechanically different on Polymarket.

## Tabs

### 1. Charts
- Interactive Chart.js line chart with Polymarket YES prices per strike
- BTC price overlay on secondary axis
- Time range: 1 Day / 1 Week / Full Month
- Multi-strike selection dropdown
- Hover tooltip shows price, time, date

### 2. Strategy & Backtest
- Strategy cards showing A, B, C, D with parameters
- Month selector with data availability status
- Backtest engine: runs all 4 strategies on historical data
- Results: equity curve, per-strategy breakdown, trade log, metrics (WR, ROI, Sharpe, max drawdown)

### 3. Paper Trading
- Default $1000 starting balance (configurable)
- Runs the 4 strategies live on real Polymarket data
- Accounts for order book depth/liquidity
- 1-cent spread cost per trade
- Trade history with strategy labels
- Live metrics: Balance, Equity, ROI%, P/L, Win Rate
- Equity curve, open positions, per-strategy performance

## Data Pipeline

7 background loops run automatically:

1. **BTC Price** (5s) — Binance REST spot price
2. **BTC Candle Sync** (5min) — Backfill 1-minute candles from Binance (last 30 days)
3. **Market Discovery** (30min) — Gamma API slug-based monthly event discovery
4. **Polymarket Prices** (60s) — CLOB order book for all markets (correct bid/ask ordering)
5. **Price History Backfill** (startup) — CLOB prices-history for all discovered markets
6. **Paper Trading** (60s) — Strategy evaluation, entry/exit checks
7. **WebSocket Broadcast** (5s) — Real-time BTC + market prices to frontend

## Lessons Learned

### The Bid/Ask Bug (Critical)
Polymarket's CLOB `/book` API returns bids sorted **ascending** (worst-first) and asks sorted **descending** (worst-first). Using `bids[0]` and `asks[0]` gives you the WORST prices, not the best.

**Correct:**
```python
best_bid = max(float(b["price"]) for b in bids)
best_ask = min(float(a["price"]) for a in asks)
```

This bug caused 4 months of wrong price data in the original project, making all strategy analysis unreliable.

### Gamma API vs CLOB Price
The Polymarket website shows **Gamma API model prices** (analytical estimates). The CLOB API shows the actual **tradeable order book**. These can differ significantly. Never assume the website price equals the CLOB price.

### "Dip" vs "Reach" Markets
Monthly BTC events have two types of markets:
- **"Will Bitcoin dip to $X?"** — YES pays if BTC drops to or below $X
- **"Will Bitcoin reach $X?"** — YES pays if BTC rises to or above $X

Strategies must differentiate these because identical BTC moves have opposite effects on each type.

### CLOB prices-history Endpoint
The correct endpoint for historical prices is:
```
GET /prices-history?market={token_id}&interval=max&fidelity=60
```
Pass the **token_id** (not market_id/conditionId). Returns `{"history": [{"t": unix_sec, "p": price}]}`.

### Dual Token IDs
Each market has two tokens: `clobTokenIds[0]` = YES token, `clobTokenIds[1]` = NO token. "SELL YES" strategies need the NO token to check its order book and simulate fills.

### Startup Grace Period
Don't enter trades within 120 seconds of server start. CLOB prices may be stale or incomplete. The `STARTUP_GRACE_SEC` constant controls this.

## Suggestions for Improvement

1. **Live trading** — Add CLOB order placement via py-clob-client once paper trading validates the strategies
2. **Multi-month validation** — As correct data accumulates over more months, re-run walk-forward validation
3. **Dynamic threshold tuning** — Auto-adjust RSI/momentum thresholds based on rolling performance
4. **Browser notifications** — Alert on trade entries/exits
5. **Telegram integration** — Push trade notifications to a Telegram bot
6. **Take profit / Stop loss** — Add optional early exit at +50% profit or -30% loss instead of fixed hold duration
7. **Portfolio heat map** — Visual showing which strikes have open positions
8. **Slippage tracking** — Compare paper fills to actual CLOB depth at entry/exit time
9. **Correlation analysis** — Track how strategies A-D correlate to avoid over-concentration
10. **Multi-asset expansion** — Apply the same framework to ETH or other Polymarket monthly events

## Technical Notes

- **Python 3.9+** required
- **SQLite WAL mode** for concurrent reads/writes without locks
- **Chart.js 4.4.0** loaded from CDN (no vendor file needed)
- All API calls use **httpx** async client with timeouts
- Market discovery uses slug format: `what-price-will-bitcoin-hit-in-{month}-{year}`
