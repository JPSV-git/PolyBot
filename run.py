#!/usr/bin/env python3
"""PolyBot — Polymarket BTC Binary Options Trading Bot"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
