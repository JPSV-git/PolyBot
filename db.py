"""
SQLite database layer — WAL mode for concurrent reads/writes.
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH


def get_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS btc_candles (
                timestamp INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL, volume REAL
            );

            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                yes_token_id TEXT,
                no_token_id TEXT,
                title TEXT,
                target_price REAL,
                market_type TEXT,
                month TEXT,
                end_date TEXT,
                volume REAL DEFAULT 0,
                liquidity REAL DEFAULT 0,
                url TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                yes_bid REAL, yes_ask REAL, yes_mid REAL,
                ask_depth_usd REAL, bid_depth_usd REAL,
                timestamp INTEGER,
                UNIQUE(market_id, timestamp)
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                yes_price REAL,
                timestamp INTEGER,
                UNIQUE(market_id, timestamp)
            );

            CREATE TABLE IF NOT EXISTS paper_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                running INTEGER DEFAULT 0,
                balance REAL DEFAULT 1000.0,
                initial_balance REAL DEFAULT 1000.0,
                risk_pct REAL DEFAULT 0.03,
                max_positions INTEGER DEFAULT 5,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                market_id TEXT,
                market_title TEXT,
                target_price REAL,
                market_type TEXT,
                action TEXT,
                token_side TEXT,
                entry_price REAL,
                amount REAL,
                shares REAL,
                btc_at_entry REAL,
                moneyness_pct REAL,
                rsi_at_entry REAL,
                btc_1h_ret_at_entry REAL,
                status TEXT DEFAULT 'open',
                exit_price REAL,
                pnl REAL,
                pnl_pct REAL,
                close_reason TEXT,
                hold_hours_target REAL,
                created_at TEXT DEFAULT (datetime('now')),
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS paper_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equity REAL,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT,
                params TEXT,
                results TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute("INSERT OR IGNORE INTO paper_state (id) VALUES (1)")
        conn.commit()


# ── BTC candles ──────────────────────────────────────────────────────────────

def store_btc_candles(candles: list):
    if not candles:
        return
    with get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO btc_candles (timestamp, open, high, low, close, volume) VALUES (?,?,?,?,?,?)",
            [(c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"]) for c in candles]
        )
        conn.commit()


def get_btc_candles(start_ms=None, end_ms=None, limit=None):
    with get_db() as conn:
        q = "SELECT * FROM btc_candles"
        params = []
        clauses = []
        if start_ms:
            clauses.append("timestamp >= ?")
            params.append(start_ms)
        if end_ms:
            clauses.append("timestamp <= ?")
            params.append(end_ms)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY timestamp"
        if limit:
            q += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_latest_btc_ts():
    with get_db() as conn:
        row = conn.execute("SELECT MAX(timestamp) as ts FROM btc_candles").fetchone()
        return row["ts"] if row and row["ts"] else None


# ── Markets ──────────────────────────────────────────────────────────────────

def store_markets(markets: list):
    if not markets:
        return
    with get_db() as conn:
        for m in markets:
            conn.execute("""
                INSERT INTO markets (market_id, yes_token_id, no_token_id, title, target_price,
                    market_type, month, end_date, volume, liquidity, url, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(market_id) DO UPDATE SET
                    yes_token_id=excluded.yes_token_id, no_token_id=excluded.no_token_id,
                    title=excluded.title, volume=excluded.volume, liquidity=excluded.liquidity,
                    last_seen=datetime('now')
            """, (m["market_id"], m.get("yes_token_id"), m.get("no_token_id"),
                  m["title"], m["target_price"], m["market_type"], m["month"],
                  m.get("end_date", ""), m.get("volume", 0), m.get("liquidity", 0),
                  m.get("url", "")))
        conn.commit()


def get_markets(month=None):
    with get_db() as conn:
        if month:
            rows = conn.execute("SELECT * FROM markets WHERE month=? ORDER BY target_price", (month,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM markets ORDER BY month, target_price").fetchall()
        return [dict(r) for r in rows]


def get_available_months():
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT month FROM markets ORDER BY month").fetchall()
        return [r["month"] for r in rows]


# ── Price snapshots ──────────────────────────────────────────────────────────

def store_price_snapshot(market_id: str, yes_bid: float, yes_ask: float, yes_mid: float,
                         ask_depth: float, bid_depth: float, ts: int):
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO price_snapshots
                (market_id, yes_bid, yes_ask, yes_mid, ask_depth_usd, bid_depth_usd, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (market_id, yes_bid, yes_ask, yes_mid, ask_depth, bid_depth, ts))
        conn.commit()


def store_price_snapshots_bulk(snapshots: list):
    if not snapshots:
        return
    with get_db() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO price_snapshots
                (market_id, yes_bid, yes_ask, yes_mid, ask_depth_usd, bid_depth_usd, timestamp)
            VALUES (:market_id, :yes_bid, :yes_ask, :yes_mid, :ask_depth, :bid_depth, :ts)
        """, snapshots)
        conn.commit()


def get_price_snapshots(market_id: str, start_ts=None, end_ts=None):
    with get_db() as conn:
        q = "SELECT * FROM price_snapshots WHERE market_id=?"
        params = [market_id]
        if start_ts:
            q += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts:
            q += " AND timestamp <= ?"
            params.append(end_ts)
        q += " ORDER BY timestamp"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ── Price history (from CLOB backfill) ───────────────────────────────────────

def store_price_history_bulk(market_id: str, history: list):
    if not history:
        return 0
    rows = []
    for h in history:
        try:
            rows.append((market_id, float(h["p"]), int(h["t"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    with get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO price_history (market_id, yes_price, timestamp) VALUES (?,?,?)",
            rows
        )
        conn.commit()
    return len(rows)


def get_price_history(market_id: str = None, month: str = None):
    with get_db() as conn:
        if market_id:
            rows = conn.execute(
                "SELECT * FROM price_history WHERE market_id=? ORDER BY timestamp",
                (market_id,)
            ).fetchall()
        elif month:
            rows = conn.execute("""
                SELECT ph.* FROM price_history ph
                JOIN markets m ON ph.market_id = m.market_id
                WHERE m.month = ? ORDER BY ph.timestamp
            """, (month,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM price_history ORDER BY timestamp").fetchall()
        return [dict(r) for r in rows]


def get_price_history_count():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) as c FROM price_history").fetchone()["c"]


# ── Paper state ──────────────────────────────────────────────────────────────

def get_paper_state():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM paper_state WHERE id=1").fetchone()
        return dict(row) if row else {"running": 0, "balance": 1000.0, "initial_balance": 1000.0,
                                       "risk_pct": 0.03, "max_positions": 5}


def set_paper_state(**kwargs):
    with get_db() as conn:
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"UPDATE paper_state SET {sets}, updated_at=datetime('now') WHERE id=1", vals)
        conn.commit()


# ── Paper trades ─────────────────────────────────────────────────────────────

def add_paper_trade(trade: dict) -> int:
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO paper_trades (strategy, market_id, market_title, target_price, market_type,
                action, token_side, entry_price, amount, shares, btc_at_entry, moneyness_pct,
                rsi_at_entry, btc_1h_ret_at_entry, hold_hours_target)
            VALUES (:strategy, :market_id, :market_title, :target_price, :market_type,
                :action, :token_side, :entry_price, :amount, :shares, :btc_at_entry, :moneyness_pct,
                :rsi_at_entry, :btc_1h_ret_at_entry, :hold_hours_target)
        """, trade)
        conn.commit()
        return cur.lastrowid


def close_paper_trade(trade_id: int, exit_price: float, pnl: float, pnl_pct: float, reason: str):
    with get_db() as conn:
        conn.execute("""
            UPDATE paper_trades SET status='closed', exit_price=?, pnl=?, pnl_pct=?,
                close_reason=?, closed_at=datetime('now')
            WHERE id=?
        """, (exit_price, pnl, pnl_pct, reason, trade_id))
        conn.commit()


def get_paper_trades(status=None):
    with get_db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM paper_trades WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM paper_trades ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def delete_paper_trades():
    with get_db() as conn:
        conn.execute("DELETE FROM paper_trades")
        conn.commit()


# ── Paper equity ─────────────────────────────────────────────────────────────

def add_paper_equity(equity: float):
    with get_db() as conn:
        conn.execute("INSERT INTO paper_equity (equity) VALUES (?)", (equity,))
        conn.commit()


def get_paper_equity():
    with get_db() as conn:
        rows = conn.execute("SELECT equity, timestamp FROM paper_equity ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def delete_paper_equity():
    with get_db() as conn:
        conn.execute("DELETE FROM paper_equity")
        conn.commit()


# ── Backtest runs ────────────────────────────────────────────────────────────

def store_backtest_run(month: str, params: dict, results: dict) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_runs (month, params, results) VALUES (?, ?, ?)",
            (month, json.dumps(params), json.dumps(results))
        )
        conn.commit()
        return cur.lastrowid


def get_backtest_runs():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 20").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"]) if d["params"] else {}
            d["results"] = json.loads(d["results"]) if d["results"] else {}
            result.append(d)
        return result
