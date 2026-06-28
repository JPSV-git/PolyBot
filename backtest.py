"""
Backtest engine — runs the 4 combined strategies on historical data.
"""

import bisect
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from config import STRATEGIES, SPREAD_COST, DEFAULT_BALANCE, MAX_CONCURRENT_POSITIONS, RISK_PER_TRADE, MIN_DTE
from strategies import compute_moneyness
from binance import compute_rsi, compute_1h_return
import db


class PriceLookup:
    """Bisect-based fast price lookup for historical YES prices."""

    def __init__(self, history: List[dict]):
        self.timestamps = [h["timestamp"] for h in history]
        self.prices = [h["yes_price"] for h in history]

    def get(self, ts: int) -> Optional[float]:
        if not self.timestamps:
            return None
        idx = bisect.bisect_right(self.timestamps, ts) - 1
        if idx < 0:
            return None
        return self.prices[idx]

    def get_nearest(self, ts: int, tolerance_sec: int = 7200) -> Optional[float]:
        if not self.timestamps:
            return None
        idx = bisect.bisect_left(self.timestamps, ts)
        best = None
        best_dist = tolerance_sec + 1
        for i in [idx - 1, idx]:
            if 0 <= i < len(self.timestamps):
                dist = abs(self.timestamps[i] - ts)
                if dist < best_dist:
                    best_dist = dist
                    best = self.prices[i]
        return best


def run_backtest(
    month: str,
    initial_balance: float = DEFAULT_BALANCE,
    risk_pct: float = RISK_PER_TRADE,
    max_positions: int = MAX_CONCURRENT_POSITIONS,
    spread_cost: float = SPREAD_COST,
) -> Dict:
    markets = db.get_markets(month=month)
    if not markets:
        return {"error": f"No markets for {month}"}

    all_history = db.get_price_history(month=month)
    if not all_history:
        return {"error": f"No price history for {month}"}

    candles = db.get_btc_candles()
    if not candles:
        return {"error": "No BTC candles"}

    # Build price lookups per market
    by_market = {}
    for h in all_history:
        mid = h["market_id"]
        if mid not in by_market:
            by_market[mid] = []
        by_market[mid].append(h)
    lookups = {mid: PriceLookup(hist) for mid, hist in by_market.items()}

    # Build BTC close array indexed by unix ms timestamp
    btc_ts = [c["timestamp"] for c in candles]
    btc_closes = [c["close"] for c in candles]

    # Determine month time range
    yr, mo = int(month[:4]), int(month[5:7])
    import calendar
    last_day = calendar.monthrange(yr, mo)[1]
    start_ts = int(datetime(yr, mo, 1, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(yr, mo, last_day, 23, 59, tzinfo=timezone.utc).timestamp())

    capital = initial_balance
    open_positions = []
    trade_log = []
    equity_curve = []
    hour_step = 3600

    for ts in range(start_ts, end_ts, hour_step):
        ts_ms = ts * 1000

        # Get trailing BTC closes for indicators
        idx = bisect.bisect_right(btc_ts, ts_ms)
        if idx < 60:
            continue
        trailing_closes = btc_closes[max(0, idx - 120):idx]
        btc_price = trailing_closes[-1]
        rsi = compute_rsi(trailing_closes, 14)
        btc_1h_pct = compute_1h_return(trailing_closes)

        # DTE calculation
        dte = max(0, (end_ts - ts) / 86400)

        # Close expired positions
        still_open = []
        for pos in open_positions:
            hold_elapsed = (ts - pos["entry_ts"]) / 3600
            if hold_elapsed >= pos["hold_hours"]:
                exit_price = None
                lookup = lookups.get(pos["market_id"])
                if lookup:
                    exit_price = lookup.get_nearest(ts)
                if exit_price is None:
                    exit_price = pos["entry_price"]

                if pos["action"] == "SELL":
                    raw_pnl = pos["shares"] * (pos["entry_price"] - exit_price)
                else:
                    raw_pnl = pos["shares"] * (exit_price - pos["entry_price"])

                pnl = raw_pnl - spread_cost * pos["shares"]
                pnl_pct = round(pnl / pos["amount"] * 100, 2) if pos["amount"] > 0 else 0
                capital += pos["amount"] + pnl

                trade_log.append({
                    "strategy": pos["strategy"],
                    "market_title": pos["market_title"],
                    "target_price": pos["target_price"],
                    "market_type": pos["market_type"],
                    "action": pos["action"],
                    "entry_price": pos["entry_price"],
                    "exit_price": round(exit_price, 4),
                    "amount": pos["amount"],
                    "pnl": round(pnl, 4),
                    "pnl_pct": pnl_pct,
                    "entry_ts": pos["entry_ts"],
                    "exit_ts": ts,
                    "hold_hours": pos["hold_hours"],
                })
            else:
                still_open.append(pos)
        open_positions = still_open

        # Evaluate new entries
        if len(open_positions) < max_positions and capital > 5:
            for market in markets:
                if len(open_positions) >= max_positions:
                    break
                tp = market["target_price"]
                mtype = market.get("market_type", "reach")
                moneyness = compute_moneyness(tp, btc_price, mtype)

                lookup = lookups.get(market["market_id"])
                if not lookup:
                    continue
                yes_price = lookup.get_nearest(ts)
                if not yes_price or yes_price <= 0.02 or yes_price >= 0.98:
                    continue

                if dte < MIN_DTE:
                    continue

                # Check each strategy
                for sid, strat in sorted(STRATEGIES.items(), key=lambda x: x[1]["priority"]):
                    if strat["market_type"] != mtype:
                        continue
                    if not (strat["moneyness_min"] <= moneyness <= strat["moneyness_max"]):
                        continue

                    triggered = False
                    if strat["trigger_type"] == "btc_momentum" and btc_1h_pct is not None:
                        triggered = btc_1h_pct > strat["btc_1h_threshold"]
                    elif strat["trigger_type"] == "rsi" and rsi is not None:
                        if strat["rsi_direction"] == "below":
                            triggered = rsi < strat["rsi_threshold"]

                    if not triggered:
                        continue

                    # Don't double up on same market
                    if any(p["market_id"] == market["market_id"] for p in open_positions):
                        continue

                    amount = round(capital * risk_pct, 2)
                    if amount < 1:
                        continue

                    shares = amount / yes_price
                    capital -= amount

                    open_positions.append({
                        "strategy": sid,
                        "market_id": market["market_id"],
                        "market_title": market.get("title", ""),
                        "target_price": tp,
                        "market_type": mtype,
                        "action": strat["action"],
                        "entry_price": yes_price,
                        "amount": amount,
                        "shares": shares,
                        "entry_ts": ts,
                        "hold_hours": strat["hold_hours"],
                    })
                    break  # one entry per market per tick

        # Record equity
        open_value = sum(p["amount"] for p in open_positions)
        equity_curve.append({"ts": ts, "equity": round(capital + open_value, 2)})

    # Force-close remaining
    for pos in open_positions:
        exit_price = pos["entry_price"]
        lookup = lookups.get(pos["market_id"])
        if lookup and lookup.timestamps:
            exit_price = lookup.prices[-1]

        if pos["action"] == "SELL":
            raw_pnl = pos["shares"] * (pos["entry_price"] - exit_price)
        else:
            raw_pnl = pos["shares"] * (exit_price - pos["entry_price"])
        pnl = raw_pnl - spread_cost * pos["shares"]
        pnl_pct = round(pnl / pos["amount"] * 100, 2) if pos["amount"] > 0 else 0
        capital += pos["amount"] + pnl

        trade_log.append({
            "strategy": pos["strategy"],
            "market_title": pos["market_title"],
            "target_price": pos["target_price"],
            "market_type": pos["market_type"],
            "action": pos["action"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 4),
            "amount": pos["amount"],
            "pnl": round(pnl, 4),
            "pnl_pct": pnl_pct,
            "entry_ts": pos["entry_ts"],
            "exit_ts": end_ts,
            "hold_hours": pos["hold_hours"],
        })

    # Compute stats
    total_trades = len(trade_log)
    wins = sum(1 for t in trade_log if t["pnl"] > 0)
    losses = sum(1 for t in trade_log if t["pnl"] < 0)
    flat = sum(1 for t in trade_log if t["pnl"] == 0)
    total_pnl = sum(t["pnl"] for t in trade_log)
    deployed = sum(t["amount"] for t in trade_log)

    per_strategy = {}
    for sid in STRATEGIES:
        st = [t for t in trade_log if t["strategy"] == sid]
        if st:
            sw = sum(1 for t in st if t["pnl"] > 0)
            sl = sum(1 for t in st if t["pnl"] < 0)
            sp = sum(t["pnl"] for t in st)
            per_strategy[sid] = {
                "name": STRATEGIES[sid]["name"],
                "trades": len(st),
                "wins": sw, "losses": sl,
                "wr": round(sw / max(sw + sl, 1) * 100, 1),
                "pnl": round(sp, 2),
            }

    # Max drawdown
    peak = initial_balance
    max_dd = 0
    for eq in equity_curve:
        if eq["equity"] > peak:
            peak = eq["equity"]
        dd = (peak - eq["equity"]) / peak * 100
        if dd > max_dd:
            max_dd = dd

    pnls = [t["pnl"] for t in trade_log]
    sharpe = round(np.mean(pnls) / np.std(pnls), 4) if pnls and np.std(pnls) > 0 else 0

    return {
        "month": month,
        "initial_balance": initial_balance,
        "final_balance": round(capital, 2),
        "roi_pct": round((capital - initial_balance) / initial_balance * 100, 2),
        "total_trades": total_trades,
        "wins": wins, "losses": losses, "flat": flat,
        "wr": round(wins / max(wins + losses, 1) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "deployed": round(deployed, 2),
        "sharpe": sharpe,
        "max_drawdown_pct": round(max_dd, 2),
        "per_strategy": per_strategy,
        "equity_curve": equity_curve,
        "trades": trade_log,
    }
