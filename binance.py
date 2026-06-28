"""
Binance BTC price client — REST for candles, spot price.
Also computes RSI and momentum indicators.
"""

import time
from typing import Optional, List

import httpx
import numpy as np

from config import BINANCE_API

_TIMEOUT = httpx.Timeout(10.0)


async def get_btc_price() -> Optional[float]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        try:
            resp = await c.get(f"{BINANCE_API}/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
            return float(resp.json()["price"])
        except Exception:
            return None


async def get_klines(start_ms: int, end_ms: int = None, interval: str = "1m", limit: int = 1000) -> List[dict]:
    candles = []
    cursor = start_ms
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        while True:
            params = {"symbol": "BTCUSDT", "interval": interval, "startTime": cursor, "limit": limit}
            if end_ms:
                params["endTime"] = end_ms
            try:
                resp = await c.get(f"{BINANCE_API}/api/v3/klines", params=params)
                data = resp.json()
            except Exception as e:
                print(f"[binance] klines error: {e}")
                break
            if not data or not isinstance(data, list):
                break
            for k in data:
                candles.append({
                    "ts": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            if len(data) < limit:
                break
            cursor = int(data[-1][0]) + 60_000
            if end_ms and cursor > end_ms:
                break
    return candles


def compute_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def compute_1h_return(closes_1m: list) -> Optional[float]:
    if len(closes_1m) < 60:
        return None
    current = closes_1m[-1]
    past = closes_1m[-60]
    if past == 0:
        return None
    return round((current - past) / past * 100, 4)
