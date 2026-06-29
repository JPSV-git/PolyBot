"""
FastAPI server — background data loops, API routes, WebSocket.
"""

import asyncio
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent

import db
import binance
import polymarket as poly
import paper_trader
import backtest as bt
from config import STRATEGIES, DEFAULT_BALANCE
from strategies import compute_indicators

# ── Global state ─────────────────────────────────────────────────────────────

class AppState:
    btc_price: float = 0.0
    btc_closes_1m: list = []
    markets: list = []
    markets_live: dict = {}      # market_id -> {yes_bid, yes_ask, yes_mid, ...}
    ws_clients: list = []
    active_month: str = ""

state = AppState()

# ── Background loops ─────────────────────────────────────────────────────────

async def btc_price_loop():
    while True:
        try:
            price = await binance.get_btc_price()
            if price:
                state.btc_price = price
        except Exception as e:
            print(f"[btc_price] {e}")
        await asyncio.sleep(5)


async def btc_candle_sync_loop():
    await asyncio.sleep(5)
    while True:
        try:
            latest = db.get_latest_btc_ts()
            if latest:
                start = latest + 60_000
            else:
                now = int(time.time() * 1000)
                start = now - 60 * 24 * 3600 * 1000  # 60 days back for full backtest coverage

            end = int(time.time() * 1000)
            if start < end:
                candles = await binance.get_klines(start, end)
                if candles:
                    db.store_btc_candles(candles)
                    print(f"[btc_sync] Stored {len(candles)} candles")

            # Update in-memory closes for indicators
            recent = db.get_btc_candles(
                start_ms=int(time.time() * 1000) - 2 * 3600 * 1000  # last 2h
            )
            state.btc_closes_1m = [c["close"] for c in recent]
        except Exception as e:
            print(f"[btc_sync] {e}")
        await asyncio.sleep(300)


async def market_discovery_loop():
    await asyncio.sleep(3)
    while True:
        try:
            markets = await poly.discover_current_month_markets()
            if markets:
                db.store_markets(markets)
                state.markets = markets
                state.active_month = markets[0].get("month", "")
                print(f"[markets] {len(markets)} markets for {state.active_month}")
        except Exception as e:
            print(f"[markets] {e}")
        await asyncio.sleep(1800)  # every 30 min


async def polymarket_price_loop():
    await asyncio.sleep(10)
    while True:
        try:
            if state.markets:
                books = await poly.fetch_all_order_books(state.markets)
                ts = int(time.time())
                snapshots = []
                for m in state.markets:
                    mid = m["market_id"]
                    if mid in books:
                        bk = books[mid]
                        m["yes_bid"] = bk["best_bid"]
                        m["yes_ask"] = bk["best_ask"]
                        m["yes_mid"] = bk["mid"]
                        m["ask_depth_usd"] = bk["ask_depth_usd"]
                        m["bid_depth_usd"] = bk["bid_depth_usd"]
                        state.markets_live[mid] = bk
                        snapshots.append({
                            "market_id": mid,
                            "yes_bid": bk["best_bid"],
                            "yes_ask": bk["best_ask"],
                            "yes_mid": bk["mid"],
                            "ask_depth": bk["ask_depth_usd"],
                            "bid_depth": bk["bid_depth_usd"],
                            "ts": ts,
                        })
                if snapshots:
                    db.store_price_snapshots_bulk(snapshots)
        except Exception as e:
            print(f"[poly_prices] {e}")
        await asyncio.sleep(60)


async def price_history_backfill_loop():
    """Gap-filling backfill: on startup fetches all missing history, then periodically fills gaps."""
    await asyncio.sleep(15)
    while True:
        try:
            markets = db.get_markets()
            if not markets:
                print("[backfill] No markets yet, waiting...")
                await asyncio.sleep(300)
                continue
            total = 0
            for m in markets:
                token = m.get("yes_token_id")
                if not token:
                    continue
                latest_ts = db.get_latest_price_history_ts(m["market_id"])
                history = await poly.fetch_price_history(token, start_ts=latest_ts)
                if history:
                    new_points = [h for h in history if int(h.get("t", 0)) > latest_ts]
                    if new_points:
                        stored = db.store_price_history_bulk(m["market_id"], new_points)
                        total += stored
                await asyncio.sleep(0.3)
            if total:
                print(f"[backfill] Stored {total} new price history points")
        except Exception as e:
            print(f"[backfill] {e}")
        await asyncio.sleep(3600)


async def paper_trading_loop():
    await asyncio.sleep(30)
    while True:
        try:
            ps = db.get_paper_state()
            if ps["running"] and state.btc_closes_1m and state.markets:
                # Enrich markets with live data + DTE
                now = datetime.now(timezone.utc)
                enriched = []
                for m in state.markets:
                    em = dict(m)
                    live = state.markets_live.get(m["market_id"])
                    if live:
                        em.update(live)
                    try:
                        end = datetime.fromisoformat(m.get("end_date", "").replace("Z", "+00:00"))
                        em["dte"] = max(0, (end - now).total_seconds() / 86400)
                    except Exception:
                        em["dte"] = 15
                    enriched.append(em)

                # Check exits
                closed = paper_trader.check_exits(state.markets_live)
                for c in closed:
                    print(f"[paper] Closed trade #{c['id']} [{c['strategy']}] PnL=${c['pnl']:+.2f} ({c['pnl_pct']:+.1f}%)")

                # Check entries
                indicators = compute_indicators(state.btc_closes_1m)
                entries = paper_trader.check_entries(indicators, enriched)
                for e in entries:
                    print(f"[paper] Opened trade #{e['id']} [{e['strategy']}] {e['market_title'][:40]} @{e['entry_price']:.3f}")

                # Equity snapshot
                metrics = paper_trader.get_metrics()
                db.add_paper_equity(metrics["equity"])
        except Exception as e:
            print(f"[paper] {e}")
        await asyncio.sleep(60)


async def ws_broadcast_loop():
    while True:
        await asyncio.sleep(5)
        if not state.ws_clients:
            continue
        msg = json.dumps({
            "type": "price_update",
            "btc_price": state.btc_price,
            "active_month": state.active_month,
            "markets": [
                {
                    "market_id": m["market_id"],
                    "title": m.get("title", ""),
                    "target_price": m.get("target_price", 0),
                    "market_type": m.get("market_type", ""),
                    "yes_mid": m.get("yes_mid", 0),
                    "yes_bid": m.get("yes_bid", 0),
                    "yes_ask": m.get("yes_ask", 0),
                }
                for m in state.markets if m.get("yes_mid")
            ],
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        dead = []
        for ws in state.ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.ws_clients.remove(ws)


async def daily_backup_loop():
    """Daily DB backup — one copy per calendar day into data/backups/{YYYY-MM}/."""
    await asyncio.sleep(60)
    last_backup_date = None
    while True:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != last_backup_date:
                path = db.backup_db()
                last_backup_date = today
                print(f"[backup] Daily backup saved: {path}")
        except Exception as e:
            print(f"[backup] {e}")
        await asyncio.sleep(3600)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    tasks = [
        asyncio.create_task(btc_price_loop()),
        asyncio.create_task(btc_candle_sync_loop()),
        asyncio.create_task(market_discovery_loop()),
        asyncio.create_task(polymarket_price_loop()),
        asyncio.create_task(price_history_backfill_loop()),
        asyncio.create_task(paper_trading_loop()),
        asyncio.create_task(ws_broadcast_loop()),
        asyncio.create_task(daily_backup_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="PolyBot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ── Routes: Pages ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))

# ── Routes: Charts tab ───────────────────────────────────────────────────────

@app.get("/api/btc-price")
async def api_btc_price():
    return {"price": state.btc_price}


@app.get("/api/btc-candles")
async def api_btc_candles(range: str = "1d"):
    now_ms = int(time.time() * 1000)
    ranges = {"1d": 86400, "1w": 7 * 86400, "1m": 30 * 86400}
    seconds = ranges.get(range, 86400)
    start = now_ms - seconds * 1000
    candles = db.get_btc_candles(start_ms=start)
    # Downsample for performance
    step = max(1, len(candles) // 500)
    return [{"ts": c["timestamp"], "close": c["close"]} for c in candles[::step]]


@app.get("/api/markets")
async def api_markets():
    return state.markets


@app.get("/api/months")
async def api_months():
    return db.get_available_months()


@app.get("/api/market-history")
async def api_market_history(market_id: str, range: str = "1m"):
    now = int(time.time())
    ranges = {"1d": 86400, "1w": 7 * 86400, "1m": 30 * 86400}
    seconds = ranges.get(range, 30 * 86400)
    start = now - seconds

    # Combine both sources: backfill history + live snapshots, filter bad values
    points = {}
    history = db.get_price_history(market_id=market_id)
    for h in history:
        if h["timestamp"] >= start and h["yes_price"] and h["yes_price"] > 0:
            points[h["timestamp"]] = h["yes_price"]
    snapshots = db.get_price_snapshots(market_id, start_ts=start)
    for s in snapshots:
        if s["yes_mid"] and s["yes_mid"] > 0:
            points[s["timestamp"]] = s["yes_mid"]

    sorted_pts = sorted(points.items())
    return [{"ts": ts, "price": round(price, 4)} for ts, price in sorted_pts]


@app.get("/api/strikes")
async def api_strikes(month: Optional[str] = None):
    m = month or state.active_month
    markets = db.get_markets(month=m)
    # Filter to markets with live order book data or historical prices
    active = []
    seen = set()
    for mk in markets:
        mid = mk["market_id"]
        has_live = mid in state.markets_live
        has_history = bool(db.get_price_history(market_id=mid))
        if not has_live and not has_history:
            continue
        key = (mk["target_price"], mk["market_type"])
        if key in seen:
            continue
        seen.add(key)
        active.append(mk)
    strikes = sorted(set(mk["target_price"] for mk in active))
    return [{"target_price": s, "markets": [
        {"market_id": mk["market_id"], "title": mk["title"], "market_type": mk["market_type"]}
        for mk in active if mk["target_price"] == s
    ]} for s in strikes]


@app.post("/api/import-btc-from-trading")
async def api_import_btc():
    """Import BTC candles from the original Trading app's DB if available."""
    import sqlite3 as _sql
    src = Path.home() / "Desktop" / "Trading" / "data" / "trading.db"
    if not src.exists():
        return {"error": "Trading app DB not found"}
    def _import():
        conn_src = _sql.connect(str(src))
        rows = conn_src.execute("SELECT timestamp, open, high, low, close, volume FROM historical_btc ORDER BY timestamp").fetchall()
        conn_src.close()
        if not rows:
            return {"imported": 0}
        candles = [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]} for r in rows]
        db.store_btc_candles(candles)
        return {"imported": len(candles)}
    result = await asyncio.get_event_loop().run_in_executor(None, _import)
    return result


# ── Routes: Strategy & Backtest tab ──────────────────────────────────────────

@app.get("/api/strategies")
async def api_strategies():
    return STRATEGIES


class BacktestParams(BaseModel):
    month: str
    initial_balance: float = DEFAULT_BALANCE
    risk_pct: float = 0.03
    max_positions: int = 5


@app.post("/api/backtest/run")
async def api_backtest_run(params: BacktestParams):
    results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: bt.run_backtest(
            params.month, params.initial_balance, params.risk_pct, params.max_positions
        )
    )
    if "error" not in results:
        db.store_backtest_run(params.month, params.model_dump(), results)
    return results


@app.get("/api/backtest/runs")
async def api_backtest_runs():
    return db.get_backtest_runs()


@app.get("/api/backtest/data-status")
async def api_backtest_data_status():
    months = db.get_available_months()
    status = {}
    for m in months:
        markets = db.get_markets(month=m)
        history = db.get_price_history(month=m)
        status[m] = {
            "markets": len(markets),
            "price_points": len(history),
            "ready": len(history) > 50,
        }
    btc_count = len(db.get_btc_candles())
    return {"months": status, "btc_candles": btc_count}


# ── Routes: Paper Trading tab ───────────────────────────────────────────────

@app.get("/api/paper/state")
async def api_paper_state():
    return paper_trader.get_metrics()


@app.post("/api/paper/start")
async def api_paper_start(balance: Optional[float] = None):
    ps = db.get_paper_state()
    if balance and not ps["running"]:
        db.set_paper_state(balance=balance, initial_balance=balance)
    db.set_paper_state(running=1)
    paper_trader.reset_state()
    return {"ok": True, "message": "Paper trading started"}


@app.post("/api/paper/stop")
async def api_paper_stop():
    db.set_paper_state(running=0)
    return {"ok": True, "message": "Paper trading stopped"}


@app.post("/api/paper/reset")
async def api_paper_reset(balance: float = DEFAULT_BALANCE):
    db.set_paper_state(running=0, balance=balance, initial_balance=balance)
    db.delete_paper_trades()
    db.delete_paper_equity()
    paper_trader.reset_state()
    return {"ok": True, "message": f"Reset to ${balance}"}


class PaperConfig(BaseModel):
    risk_pct: Optional[float] = None
    max_positions: Optional[int] = None


@app.put("/api/paper/config")
async def api_paper_config(cfg: PaperConfig):
    updates = {}
    if cfg.risk_pct is not None:
        updates["risk_pct"] = cfg.risk_pct
    if cfg.max_positions is not None:
        updates["max_positions"] = cfg.max_positions
    if updates:
        db.set_paper_state(**updates)
    return {"ok": True}


@app.get("/api/paper/trades")
async def api_paper_trades(status: Optional[str] = None):
    return db.get_paper_trades(status=status)


@app.get("/api/paper/equity")
async def api_paper_equity():
    return db.get_paper_equity()


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    state.ws_clients.append(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "init",
            "btc_price": state.btc_price,
            "active_month": state.active_month,
        }))
        while True:
            msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            if msg == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        if websocket in state.ws_clients:
            state.ws_clients.remove(websocket)
