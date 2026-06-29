# PolyBot — Project Context

Polymarket BTC binary options trading bot with 4 validated strategies, backtesting, paper trading, and a real-time web dashboard.

## Tech Stack

- **Backend**: Python 3.9+ / FastAPI / Uvicorn / SQLite (WAL mode) / httpx (async)
- **Frontend**: Vanilla JS + Chart.js 4.4.0 (CDN) + chartjs-adapter-date-fns
- **APIs**: Binance REST (BTC price/candles), Polymarket Gamma API (market discovery), Polymarket CLOB API (order books, price history)
- **Entry point**: `python run.py` → Uvicorn on `http://localhost:8000`

## Architecture & File Map

```
PolyBot/
├── run.py             Uvicorn launcher (host 0.0.0.0, port 8000)
├── app.py             FastAPI server, 7 background loops, API routes, WebSocket
├── config.py          Strategy definitions, constants, API endpoints, paths
├── db.py              SQLite layer (WAL mode), schema init, all CRUD operations
├── polymarket.py      Gamma API (discovery) + CLOB API (order books, price history)
├── binance.py         Binance BTC spot price, kline candles, RSI + momentum calc
├── strategies.py      Strategy evaluation engine (moneyness, triggers, eligibility)
├── paper_trader.py    Live paper trading simulation (entries, exits, metrics)
├── backtest.py        Historical backtest engine (PriceLookup, equity curve, stats)
├── requirements.txt   Python dependencies
├── data/              SQLite DB created at runtime (polybot.db)
└── static/
    ├── index.html     3-tab SPA (Charts, Strategy & Backtest, Paper Trading)
    ├── style.css      Dark theme, monospace font, responsive grid
    └── app.js         WebSocket client, Chart.js rendering, API calls, polling
```

## Database Schema (SQLite — WAL mode)

| Table | Purpose |
|-------|---------|
| `btc_candles` | 1-min BTC OHLCV from Binance (PK: timestamp ms) |
| `markets` | Discovered Polymarket monthly markets (PK: market_id/conditionId) |
| `price_snapshots` | Live CLOB order book snapshots (bid/ask/mid per market, 60s interval) |
| `price_history` | Historical YES prices from CLOB backfill (per market) |
| `paper_state` | Singleton row: running flag, balance, risk_pct, max_positions |
| `paper_trades` | Paper trade log: entry/exit, strategy, PnL, status (open/closed) |
| `paper_equity` | Equity snapshots for paper trading curve |
| `backtest_runs` | Saved backtest results (params + results as JSON) |

DB path: `data/polybot.db` (created automatically on first run).

## The 4 Strategies

All validated on June 2026 data with split-half validation, after 1-cent spread costs.

| ID | Name | Action | Trigger | Market Type | Moneyness | Hold | Expected WR |
|----|------|--------|---------|-------------|-----------|------|-------------|
| **A** | BUY reach on BTC momentum | BUY YES | BTC 1h > +1.0% | reach | 2-10% OTM | 4h | 65% |
| **B** | BUY dip on RSI oversold | BUY YES | RSI(14) < 30 | dip | 0-10% ITM | 24h | 50% |
| **C** | SELL reach on RSI oversold | SELL YES (buy NO) | RSI(14) < 35 | reach | -5% to +5% ATM | 24h | 80% |
| **D** | SELL dip on BTC surge | SELL YES (buy NO) | BTC 1h > +1.5% | dip | 0-10% ITM | 4h | 90% |

**Complementary design**: BTC UP triggers A+D, BTC DOWN triggers B+C. Combined WR ~68%, June ROI +45.6%.

**"SELL YES" = BUY NO tokens** on Polymarket. Economically equivalent, mechanically different.

### Moneyness Convention

- **Reach markets**: negative = OTM (BTC below target), positive = ITM
- **Dip markets**: negative = OTM (BTC above target), positive = ITM (BTC already dipped past)

## Background Loops (8 total)

| Loop | Interval | What it does |
|------|----------|--------------|
| `btc_price_loop` | 5s | Binance REST spot price → `state.btc_price` |
| `btc_candle_sync_loop` | 5min | Backfill 1-min candles from Binance (60 days back) → `btc_candles` table |
| `market_discovery_loop` | 30min | Gamma API slug-based monthly event discovery → `markets` table |
| `polymarket_price_loop` | 60s | CLOB order book for all markets → `price_snapshots` table |
| `price_history_backfill_loop` | startup + 1h | CLOB prices-history gap-fill (fetches from last stored timestamp) → `price_history` table |
| `paper_trading_loop` | 60s | Strategy evaluation, entry/exit checks, equity snapshots |
| `ws_broadcast_loop` | 5s | Push BTC + market prices to all WebSocket clients |
| `daily_backup_loop` | 1h check, 1x/day action | Copy full DB to `data/backups/{YYYY-MM}/{date}.db` |

## API Routes

### Charts Tab
- `GET /api/btc-price` — current BTC price
- `GET /api/btc-candles?range=1d|1w|1m` — BTC candle data (downsampled to 500 points)
- `GET /api/markets` — all discovered markets with live data
- `GET /api/months` — available months in DB
- `GET /api/market-history?market_id=X&range=Y` — combined backfill + snapshot history
- `GET /api/strikes?month=X` — grouped strikes with market list
- `POST /api/import-btc-from-trading` — import BTC candles from legacy Trading app DB

### Strategy & Backtest Tab
- `GET /api/strategies` — strategy definitions from config
- `POST /api/backtest/run` — run backtest (month, balance, risk_pct, max_positions)
- `GET /api/backtest/runs` — last 20 backtest results
- `GET /api/backtest/data-status` — per-month data availability

### Paper Trading Tab
- `GET /api/paper/state` — current metrics (balance, equity, ROI, WR, per-strategy)
- `POST /api/paper/start?balance=X` — start paper trading
- `POST /api/paper/stop` — stop paper trading
- `POST /api/paper/reset?balance=X` — reset all trades and balance
- `PUT /api/paper/config` — update risk_pct and max_positions
- `GET /api/paper/trades?status=open|closed` — trade list
- `GET /api/paper/equity` — equity curve data

### WebSocket
- `ws://host/ws` — real-time BTC price + market updates (5s interval), ping/pong keepalive

## Critical Technical Notes

### Bid/Ask Bug (CLOB API)
Polymarket CLOB `/book` returns bids sorted **ascending** (worst-first) and asks sorted **descending** (worst-first). Must use `max(bids)` for best bid, `min(asks)` for best ask. Using index `[0]` gives worst prices.

### Gamma API vs CLOB Price
Website shows Gamma API model prices (analytical estimates). CLOB shows actual tradeable order book. These can differ significantly.

### Dual Token IDs
Each market has two tokens: `clobTokenIds[0]` = YES, `clobTokenIds[1]` = NO. SELL YES strategies need the NO token for order book and fill simulation.

### CLOB prices-history Endpoint
```
GET /prices-history?market={token_id}&interval=max&fidelity=60
```
Pass **token_id** (not market_id/conditionId). Returns `{"history": [{"t": unix_sec, "p": price}]}`.

### Monthly Slug Format
Market discovery uses: `what-price-will-bitcoin-hit-in-{month}-{year}`

### Startup Grace Period
No trades within 120 seconds of server start (`STARTUP_GRACE_SEC`). CLOB prices may be stale.

### Key Constants (config.py)
- `DEFAULT_BALANCE`: $1,000
- `RISK_PER_TRADE`: 3% of balance
- `MAX_CONCURRENT_POSITIONS`: 5
- `SPREAD_COST`: $0.01 per trade
- `MIN_DTE`: 3 days (don't trade markets expiring within 3 days)
- `MIN_GAP_HOURS`: 2h between entries per strategy
- `MIN_TRADE_USD`: $1.00

## Frontend Notes

- 3-tab SPA: Charts, Strategy & Backtest, Paper Trading
- Chart.js with crosshair plugin, dual Y-axis (YES price left, BTC right)
- WebSocket auto-reconnect on disconnect (3s retry)
- Paper trading tab polls every 10s when active
- Strike selector supports multi-select for chart overlay
- Dark theme with monospace font, responsive grid layout

## Development Commands

```bash
pip install -r requirements.txt
python run.py
# Open http://localhost:8000
```

---

## Changelog

All changes to the codebase are tracked here in reverse chronological order.

### [2026-06-29] — New all-weather strategies (8 total)
- Replaced 4 old strategies (A-D) with 8 new all-weather strategies validated across UP/DOWN/FLAT regimes
- New strategy format: dual RSI + BTC 1h range triggers, entry price filter (replaces old single-trigger system)
- Updated `config.py`, `strategies.py` (find_eligible_trades), and `backtest.py` to use new format
- Backtest results (June 2026, 5% risk): +44.8% ROI, 64.3% WR, 10.9% max DD, Sharpe 0.455
- Core SELL reach (A-D): sell YES on reach strikes under various BTC conditions (momentum, weakness, surge)
- SELL dip (E-G): sell YES on dip strikes on BTC surge and BTC flat conditions
- BUY reach (H): buy YES on reach OTM when BTC is positive — provides long exposure for bull markets
- All strategies profitable in UP, DOWN, and FLAT BTC regimes

### [2026-06-28] — Project onboarded to new machine
- Cloned repo from `https://github.com/JPSV-git/PolyBot` to new Windows workstation
- Created `CLAUDE.md` with full project documentation and changelog
- Fixed Python 3.14 compatibility: upgraded FastAPI 0.111.0 → 0.138.1 (Starlette 1.3.1 dropped `on_startup` kwarg)
- Updated `requirements.txt` to use flexible version pins (`>=` instead of `==`), dropped unused `pandas`
- Fixed SSL cert verification failure on Python 3.14 Windows: `ssl.create_default_context()` rejects certs with non-critical Basic Constraints; switched to `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` + `load_default_certs()` in both `binance.py` and `polymarket.py`
- Fixed Binance API geo-restriction: switched from `api.binance.com` (blocked in EU) to `data-api.binance.vision` (public data API, no geo-restrictions) in `config.py`
- Fixed chart tooltip: rewrote to show ALL visible datasets (BTC + all Polymarket lines) at hovered timestamp instead of only the single nearest dataset
- Fixed strike selector: filters out markets with no order book data and no historical prices (dead/resolved), deduplicates same strike+type combos
- Increased Polymarket price history resolution: `fidelity=60` (1pt/hr) → `fidelity=1` (1pt/10min, 6x more data)
- Rewrote backfill loop to gap-fill: fetches only missing data from last stored timestamp instead of skipping markets with any history
- Added daily backup loop: copies full DB to `data/backups/{YYYY-MM}/{YYYY-MM-DD}.db` once per calendar day, WAL checkpoint before copy
- Added `db.get_latest_price_history_ts()` helper for gap-filling
- Added `db.backup_db()` function with WAL checkpoint + file copy
- Updated `polymarket.fetch_price_history()` to accept optional `start_ts` for incremental fetches

### [Initial commits] — Base application (pre-onboarding)
- `ad01d20` — Fix chart: crosshair, Y-axis scaling, strike selector, data filtering
- `172b241` — Fix chart, market history, and backtest data issues
- `8205eca` — Fix static file paths and Binance error handling
- `8595851` — Fix backfill loop to handle monthly rotation
- `a51d6ce` — Initial release: PolyBot trading app
