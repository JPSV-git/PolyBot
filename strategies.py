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
    For 'reach' markets: negative = OTM (BTC below target), positive = ITM
    For 'dip' markets:   negative = OTM (BTC above target), positive = ITM (BTC already dipped past)
    This matches the analysis convention used in strategy discovery.
    """
    if btc_price == 0:
        return 0
    if market_type == "reach":
        return (btc_price - target_price) / target_price * 100
    else:
        return (target_price - btc_price) / btc_price * 100


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

            if rsi is None or btc_1h is None:
                continue
            if not (strat["rsi_min"] <= rsi < strat["rsi_max"]):
                continue
            if not (strat["btc_1h_min"] <= btc_1h < strat["btc_1h_max"]):
                continue
            if not (strat["entry_price_min"] <= yes_mid <= strat["entry_price_max"]):
                continue

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

    candidates.sort(key=lambda x: x["priority"])
    return candidates
