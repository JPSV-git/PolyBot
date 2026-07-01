"""
Paper trading engine — live simulation of the 4 combined strategies.
"""

import time
from datetime import datetime, timezone
from typing import List, Dict

from config import (STRATEGIES, SPREAD_COST, MIN_TRADE_USD, MIN_GAP_HOURS,
                     STARTUP_GRACE_SEC, MIN_DTE)
from strategies import compute_indicators, compute_moneyness, find_eligible_trades
import db


_start_ts = time.time()
_last_entry_by_strat: Dict[str, float] = {}


def reset_state():
    global _last_entry_by_strat
    _last_entry_by_strat = {}


def check_exits(markets_live: Dict[str, Dict]) -> List[Dict]:
    """Check open positions for hold-time expiry. Returns list of closed trade summaries."""
    open_trades = db.get_paper_trades(status="open")
    closed = []
    now = datetime.now(timezone.utc)

    for trade in open_trades:
        raw = trade["created_at"] or ""
        try:
            if "+" in raw or raw.endswith("Z"):
                created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                created = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        except Exception:
            created = now
        hold_elapsed = (now - created).total_seconds() / 3600

        if hold_elapsed < trade["hold_hours_target"]:
            continue

        # Get current price for exit
        market_data = markets_live.get(trade["market_id"])
        if market_data:
            if trade["token_side"] == "yes":
                exit_price = market_data.get("yes_bid", market_data.get("yes_mid", trade["entry_price"]))
            else:
                exit_price = 1.0 - market_data.get("yes_ask", 1.0 - trade["entry_price"])
        else:
            exit_price = trade["entry_price"]

        shares = trade["shares"]
        if trade["action"] == "SELL":
            raw_pnl = shares * (trade["entry_price"] - exit_price)
        else:
            raw_pnl = shares * (exit_price - trade["entry_price"])

        pnl = raw_pnl - SPREAD_COST * shares
        pnl_pct = round(pnl / trade["amount"] * 100, 2) if trade["amount"] > 0 else 0

        db.close_paper_trade(trade["id"], round(exit_price, 4), round(pnl, 4), pnl_pct, "hold_timeout")

        # Return capital
        ps = db.get_paper_state()
        new_bal = round(ps["balance"] + trade["amount"] + pnl, 4)
        db.set_paper_state(balance=new_bal)

        closed.append({
            "id": trade["id"],
            "strategy": trade["strategy"],
            "pnl": round(pnl, 4),
            "pnl_pct": pnl_pct,
        })

    return closed


def check_entries(indicators: Dict, markets_enriched: List[Dict]) -> List[Dict]:
    """Evaluate strategies and open new positions. Returns list of new trade summaries."""
    # Startup grace
    if (time.time() - _start_ts) < STARTUP_GRACE_SEC:
        return []

    ps = db.get_paper_state()
    if not ps["running"]:
        return []

    balance = ps["balance"]
    risk_pct = ps.get("risk_pct", 0.03)
    max_pos = ps.get("max_positions", 5)

    open_trades = db.get_paper_trades(status="open")
    if len(open_trades) >= max_pos:
        return []

    candidates = find_eligible_trades(indicators, markets_enriched)
    now_ts = time.time()
    gap_sec = MIN_GAP_HOURS * 3600
    new_trades = []

    open_market_ids = {t["market_id"] for t in open_trades}

    for cand in candidates:
        if len(open_trades) + len(new_trades) >= max_pos:
            break

        sid = cand["strategy"]
        if (now_ts - _last_entry_by_strat.get(sid, 0)) < gap_sec:
            continue

        if cand["market_id"] in open_market_ids:
            continue

        # Liquidity check
        depth = cand.get("ask_depth_usd", 0) or 0
        amount = round(balance * risk_pct, 2)
        if amount < MIN_TRADE_USD:
            continue
        if depth > 0 and depth < amount * 0.3:
            continue

        # Entry price
        if cand["action"] == "BUY":
            entry_price = cand.get("yes_ask", cand["yes_mid"])
        else:
            entry_price = cand["yes_mid"]

        if entry_price <= 0.02 or entry_price >= 0.98:
            continue

        shares = amount / entry_price

        trade = {
            "strategy": sid,
            "market_id": cand["market_id"],
            "market_title": cand["market_title"],
            "target_price": cand["target_price"],
            "market_type": cand["market_type"],
            "action": cand["action"],
            "token_side": cand["token_side"],
            "entry_price": round(entry_price, 4),
            "amount": amount,
            "shares": round(shares, 4),
            "btc_at_entry": indicators["btc_price"],
            "moneyness_pct": cand["moneyness_pct"],
            "rsi_at_entry": indicators.get("rsi_14"),
            "btc_1h_ret_at_entry": indicators.get("btc_1h_pct"),
            "hold_hours_target": cand["hold_hours"],
        }

        trade_id = db.add_paper_trade(trade)
        balance -= amount
        db.set_paper_state(balance=round(balance, 4))
        _last_entry_by_strat[sid] = now_ts
        open_market_ids.add(cand["market_id"])

        new_trades.append({"id": trade_id, "strategy": sid, "amount": amount,
                           "entry_price": entry_price, "market_title": cand["market_title"]})

    return new_trades


def get_open_pnl(markets_live: Dict) -> List[Dict]:
    """Return open positions enriched with current price and unrealized P&L."""
    open_trades = db.get_paper_trades(status="open")
    now = datetime.now(timezone.utc)
    result = []

    for trade in open_trades:
        market_data = markets_live.get(trade["market_id"])

        # Current mark price (same logic as exit pricing)
        if market_data:
            if trade["token_side"] == "yes":
                current_price = market_data.get("yes_bid") or market_data.get("yes_mid") or trade["entry_price"]
            else:
                ask = market_data.get("yes_ask")
                current_price = (1.0 - ask) if ask else trade["entry_price"]
        else:
            current_price = trade["entry_price"]

        shares = trade["shares"] or 0
        if trade["action"] == "SELL":
            unrealized_pnl = shares * (trade["entry_price"] - current_price) - SPREAD_COST * shares
        else:
            unrealized_pnl = shares * (current_price - trade["entry_price"]) - SPREAD_COST * shares

        unrealized_pct = round(unrealized_pnl / trade["amount"] * 100, 2) if trade["amount"] else 0

        # Time remaining
        raw = trade["created_at"] or ""
        try:
            if "+" in raw or raw.endswith("Z"):
                created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            else:
                created = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        except Exception:
            created = now
        hold_elapsed_h = (now - created).total_seconds() / 3600
        hold_remaining_h = max(0, trade["hold_hours_target"] - hold_elapsed_h)

        result.append({
            **trade,
            "current_price": round(current_price, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "unrealized_pct": unrealized_pct,
            "hold_elapsed_h": round(hold_elapsed_h, 1),
            "hold_remaining_h": round(hold_remaining_h, 1),
        })

    return result


def get_metrics() -> Dict:
    """Compute current paper trading metrics."""
    ps = db.get_paper_state()
    open_trades = db.get_paper_trades(status="open")
    closed_trades = db.get_paper_trades(status="closed")

    initial = ps.get("initial_balance", 1000)
    balance = ps["balance"]
    open_value = sum(t["amount"] for t in open_trades)
    equity = balance + open_value

    total_closed = len(closed_trades)
    wins = sum(1 for t in closed_trades if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in closed_trades if (t.get("pnl") or 0) < 0)
    total_pnl = sum(t.get("pnl") or 0 for t in closed_trades)

    per_strategy = {}
    for sid in STRATEGIES:
        st = [t for t in closed_trades if t["strategy"] == sid]
        if st:
            sw = sum(1 for t in st if (t.get("pnl") or 0) > 0)
            sl = sum(1 for t in st if (t.get("pnl") or 0) < 0)
            sp = sum(t.get("pnl") or 0 for t in st)
            per_strategy[sid] = {
                "name": STRATEGIES[sid]["name"],
                "trades": len(st), "wins": sw, "losses": sl,
                "wr": round(sw / max(sw + sl, 1) * 100, 1),
                "pnl": round(sp, 2),
            }

    return {
        "running": bool(ps["running"]),
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "initial_balance": initial,
        "roi_pct": round((equity - initial) / initial * 100, 2) if initial > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "open_positions": len(open_trades),
        "total_trades": total_closed,
        "wins": wins, "losses": losses,
        "wr": round(wins / max(wins + losses, 1) * 100, 1),
        "per_strategy": per_strategy,
    }
