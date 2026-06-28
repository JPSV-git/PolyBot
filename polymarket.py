"""
Polymarket API client — Gamma API for discovery, CLOB API for prices.
CRITICAL: CLOB /book returns bids ascending, asks descending (worst-first).
Always use max(bids) for best bid, min(asks) for best ask.
"""

import re
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List

import httpx

from config import GAMMA_API, CLOB_API, MONTHLY_SLUG_TEMPLATE

_TIMEOUT = httpx.Timeout(10.0)
_FAST_TIMEOUT = httpx.Timeout(5.0)

_PRICE_PATTERNS = [
    r"\$(\d{1,3}(?:,\d{3})+)",
    r"\$(\d+)k\b",
    r"\$(\d{5,6})\b",
    r"(\d{2,3}),000\b",
]


def _extract_price(text: str) -> Optional[float]:
    for pattern in _PRICE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            val = float(raw)
            if "k" in m.group(0).lower():
                val *= 1000
            if 10_000 <= val <= 1_000_000:
                return val
    return None


def classify_market(title: str) -> str:
    lower = title.lower()
    if any(w in lower for w in ["dip", "fall", "below", "drop", "less"]):
        return "dip"
    return "reach"


def _parse_market(market: dict, event: dict, month: str) -> Optional[Dict]:
    question = market.get("question") or event.get("title") or ""
    target_price = _extract_price(question)
    if not target_price:
        return None

    condition_id = market.get("conditionId") or ""
    market_type = classify_market(question)

    outcome_prices = market.get("outcomePrices") or []
    try:
        yes_price = float(outcome_prices[0]) if outcome_prices else None
    except (ValueError, TypeError):
        yes_price = None
    if yes_price is None:
        yes_price = float(market.get("bestAsk") or market.get("lastTradePrice") or 0.5)

    raw_clob = market.get("clobTokenIds") or []
    if isinstance(raw_clob, str):
        try:
            raw_clob = json.loads(raw_clob)
        except Exception:
            raw_clob = []

    yes_token = raw_clob[0] if len(raw_clob) > 0 else ""
    no_token = raw_clob[1] if len(raw_clob) > 1 else ""
    slug = event.get("slug") or ""

    return {
        "market_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "title": question,
        "target_price": target_price,
        "market_type": market_type,
        "month": month,
        "yes_price": round(yes_price, 4),
        "volume": float(market.get("volumeNum") or market.get("volume") or 0),
        "liquidity": float(market.get("liquidityNum") or market.get("liquidity") or 0),
        "best_bid": float(market.get("bestBid") or 0),
        "best_ask": float(market.get("bestAsk") or 0),
        "end_date": event.get("endDate") or "",
        "url": f"https://polymarket.com/event/{slug}",
    }


async def discover_monthly_markets(month_name: str, year: int) -> List[Dict]:
    slug = MONTHLY_SLUG_TEMPLATE.format(month=month_name.lower(), year=year)
    month_tag = f"{year}-{_month_number(month_name):02d}"

    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        for closed in ["false", "true"]:
            try:
                resp = await c.get(f"{GAMMA_API}/events", params={"slug": slug, "closed": closed})
                events = resp.json()
                if events:
                    event = events[0]
                    markets = []
                    for m in (event.get("markets") or []):
                        parsed = _parse_market(m, event, month_tag)
                        if parsed:
                            markets.append(parsed)
                    print(f"[polymarket] Discovered {len(markets)} markets for {month_tag} (slug={slug})")
                    return markets
            except Exception as e:
                print(f"[polymarket] discover error ({slug}, closed={closed}): {e}")

    print(f"[polymarket] No event found for slug: {slug}")
    return []


def _month_number(name: str) -> int:
    months = ["january", "february", "march", "april", "may", "june",
              "july", "august", "september", "october", "november", "december"]
    try:
        return months.index(name.lower()) + 1
    except ValueError:
        return 1


async def discover_current_month_markets() -> List[Dict]:
    now = datetime.now(timezone.utc)
    month_name = now.strftime("%B")
    return await discover_monthly_markets(month_name, now.year)


async def fetch_order_book(token_id: str) -> Optional[Dict]:
    if not token_id:
        return None
    async with httpx.AsyncClient(timeout=_FAST_TIMEOUT) as c:
        try:
            resp = await c.get(f"{CLOB_API}/book", params={"token_id": token_id})
            resp.raise_for_status()
            book = resp.json()
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                return None
            # CRITICAL: CLOB sorts bids ascending, asks descending (worst-first)
            best_bid = max(float(b["price"]) for b in bids)
            best_ask = min(float(a["price"]) for a in asks)
            if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                return None
            mid = round((best_bid + best_ask) / 2, 4)
            ask_depth = round(sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks), 2)
            bid_depth = round(sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids), 2)
            return {
                "best_bid": round(best_bid, 4),
                "best_ask": round(best_ask, 4),
                "mid": mid,
                "ask_depth_usd": ask_depth,
                "bid_depth_usd": bid_depth,
                "bids": bids,
                "asks": asks,
            }
        except Exception as e:
            return None


async def fetch_all_order_books(markets: List[Dict]) -> Dict[str, Dict]:
    async with httpx.AsyncClient(timeout=_FAST_TIMEOUT) as c:
        async def _fetch_one(market):
            token_id = market.get("yes_token_id") or ""
            market_id = market.get("market_id") or ""
            if not token_id:
                return market_id, None
            try:
                resp = await c.get(f"{CLOB_API}/book", params={"token_id": token_id})
                resp.raise_for_status()
                book = resp.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                if not bids or not asks:
                    return market_id, None
                best_bid = max(float(b["price"]) for b in bids)
                best_ask = min(float(a["price"]) for a in asks)
                if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
                    return market_id, None
                mid = round((best_bid + best_ask) / 2, 4)
                ask_depth = round(sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks), 2)
                bid_depth = round(sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids), 2)
                return market_id, {
                    "best_bid": round(best_bid, 4),
                    "best_ask": round(best_ask, 4),
                    "mid": mid,
                    "ask_depth_usd": ask_depth,
                    "bid_depth_usd": bid_depth,
                }
            except Exception:
                return market_id, None

        results = await asyncio.gather(*[_fetch_one(m) for m in markets])
        return {mid: data for mid, data in results if data is not None}


async def fetch_price_history(token_id: str) -> List[Dict]:
    if not token_id:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        try:
            resp = await c.get(f"{CLOB_API}/prices-history", params={
                "market": token_id, "interval": "max", "fidelity": 60
            })
            data = resp.json()
            return data.get("history", []) if isinstance(data, dict) else []
        except Exception as e:
            print(f"[polymarket] price history error: {e}")
            return []
