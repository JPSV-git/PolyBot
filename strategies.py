"""
Strategy engine — evaluates the 4 combined strategies against current market conditions.
"""

from typing import List, Dict, Optional
from config import STRATEGIES, MIN_DTE
from binance import compute_rsi, compute_1h_return


def compute_indicators(closes_1m: List[float]) -> Dict:
    return {
        "btc_price": closes_1m[-1] if closes_1m else 0,
        "rsi_14": compute_rsi(closes_1m, 14),
        "btc_1h_pct": compute_1h_return(closes_1m),
    }


def compute_moneyness(target_price: float, btc_price: float, market_type: str) -> float:
    """
    Same formula for both market types: (btc_price - target_price) / target_price * 100
    Positive = BTC is above target, Negative = BTC is below target.
    This matches the original analysis convention exactly.
    """
    if btc_price == 0 or target_price == 0:
        return 0
    return (btc_price - target_price) / target_price * 100


def find_eligible_trades(indicators: Dict, markets: List[Dict]) -> List[Dict]:
    if not indicators.get("btc_price") or indicators["btc_price"] <= 0:
        return []

    btc = indicators["btc_price"]
    rsi = indicators.get("rsi_14")
    btc_1h = indicators.get("btc_1h_pct")
    candidates = []

    for market in markets:
        tp = market.get("target_price", 0)
        mtype = market.get("market_type", "")
        dte = market.get("dte", 0)
        yes_mid = market.get("yes_mid") or market.get("yes_price", 0)

        if dte < MIN_DTE or yes_mid <= 0.02 or yes_mid >= 0.98:
            continue

        moneyness = compute_moneyness(tp, btc, mtype)

        for sid, strat in STRATEGIES.items():
            if strat["market_type"] != mtype:
                continue
            if not (strat["moneyness_min"] <= moneyness <= strat["moneyness_max"]):
                continue

            triggered = False
            if strat["trigger_type"] == "btc_momentum" and btc_1h is not None:
                triggered = btc_1h > strat["btc_1h_threshold"]
            elif strat["trigger_type"] == "rsi" and rsi is not None:
                if strat["rsi_direction"] == "below":
                    triggered = rsi < strat["rsi_threshold"]

            if triggered:
                candidates.append({
                    "strategy": sid,
                    "market_id": market["market_id"],
                    "market_title": market.get("title", ""),
                    "target_price": tp,
                    "market_type": mtype,
                    "action": strat["action"],
                    "token_side": strat["token_side"],
                    "hold_hours": strat["hold_hours"],
                    "moneyness_pct": round(moneyness, 2),
                    "priority": strat["priority"],
                    "yes_mid": yes_mid,
                    "yes_bid": market.get("yes_bid", 0),
                    "yes_ask": market.get("yes_ask", 0),
                    "ask_depth_usd": market.get("ask_depth_usd", 0),
                    "bid_depth_usd": market.get("bid_depth_usd", 0),
                })

    # For each strategy, pick only the best candidate (closest to ATM)
    best_per_strat = {}
    for c in candidates:
        sid = c["strategy"]
        max_per = STRATEGIES[sid].get("max_entries_per_signal", 99)
        if sid not in best_per_strat:
            best_per_strat[sid] = []
        best_per_strat[sid].append(c)

    final = []
    for sid, cands in best_per_strat.items():
        max_per = STRATEGIES[sid].get("max_entries_per_signal", 99)
        cands.sort(key=lambda x: abs(x["moneyness_pct"]))
        final.extend(cands[:max_per])

    final.sort(key=lambda x: x["priority"])
    return final
