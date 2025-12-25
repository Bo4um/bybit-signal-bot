#!/usr/bin/env python3
"""
Bybit Signal Bot - SPOT:
- #COIN bullish         -> Market Buy на сумму в USDT + TP Limit (+15%)
- Short #COIN1 #COIN2   -> Market Sell на сумму в USDT без TP
"""

import asyncio
import os
import re
from datetime import datetime

import websockets
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

load_dotenv()

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TRADE_AMOUNT_USD = float(os.getenv("TRADE_AMOUNT_USD", "5"))
WS_PORT = 8765
TP_PCT = 0.15  # +15%
DEMO = os.getenv("BYBIT_DEMO", "true").lower() == "true"

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[SPOT {ts}] {msg}")

log(f"Bybit Spot Bot started | amount={TRADE_AMOUNT_USD} USDT, demo={DEMO}, TP={TP_PCT*100:.0f}%")

# ---------- Парсинг сигналов ----------

def parse_signal(message: str):
    message = message.strip().lower()
    bullish_match = re.search(r'#(\w+)\s+bullish', message)
    if bullish_match:
        return "buy", [bullish_match.group(1).upper()]
    short_match = re.search(r'short\s+((?:#\w+\s+)*#\w+)', message)
    if short_match:
        tickers = re.findall(r'#(\w+)', short_match.group(1))
        return "sell", [t.upper() for t in tickers]
    return None, []

# ---------- Bybit helpers ----------

def create_session() -> HTTP:
    if not API_KEY or not API_SECRET:
        raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET не заданы в .env")
    session = HTTP(
        api_key=API_KEY,
        api_secret=API_SECRET,
        testnet=False,
        demo=DEMO,
    )
    return session

def get_last_price(session, symbol: str):
    try:
        r = session.get_tickers(category="spot", symbol=f"{symbol}USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            log(f"WARNING no ticker {symbol}USDT in get_tickers")
            return None
        return float(lst[0]["lastPrice"])
    except Exception as e:
        log(f"ERROR get_tickers {symbol}: {e}")
        return None

def get_instrument_info(session, symbol: str):
    try:
        r = session.get_instruments_info(category="spot", symbol=f"{symbol}USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            log(f"WARNING no instruments-info for {symbol}USDT")
            return None
        info = lst[0]
        price_filter = info.get("priceFilter", {})
        lot_filter = info.get("lotSizeFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.01"))
        qty_step = float(lot_filter.get("qtyStep", "0.0001"))
        min_qty = float(lot_filter.get("minOrderQty", "0.0001"))
        return {"tickSize": tick_size, "qtyStep": qty_step, "minQty": min_qty}
    except Exception as e:
        log(f"ERROR get_instruments_info {symbol}: {e}")
        return None

def round_down(value: float, step: float) -> float:
    return (value // step) * step

async def place_spot_market_order(session, symbol: str, side: str):
    params = {
        "category": "spot",
        "symbol": f"{symbol}USDT",
        "side": side,
        "orderType": "Market",
        "marketUnit": "quoteCoin",
        "qty": str(TRADE_AMOUNT_USD),
    }
    log(f"Market {side} request: {params}")
    resp = session.place_order(**params)
    log(f"Market response: {resp.get('retCode')} {resp.get('retMsg')}")
    base_qty = None
    last = get_last_price(session, symbol)
    if last:
        base_qty = TRADE_AMOUNT_USD / last
    return resp, base_qty

async def place_spot_tp_limit_order(session, symbol: str, base_qty: float):
    info = get_instrument_info(session, symbol)
    if not info:
        log("WARNING no instrument info, skip TP")
        return None
    last = get_last_price(session, symbol)
    if not last:
        log("WARNING no last price, skip TP")
        return None

    tick = info["tickSize"]
    step = info["qtyStep"]
    min_qty = info["minQty"]

    tp_raw = last * (1 + TP_PCT)
    tp_price = round_down(tp_raw, tick)
    qty_raw = max(base_qty, min_qty)
    qty = round_down(qty_raw, step)

    price_str = f"{tp_price:.8f}".rstrip("0").rstrip(".")
    qty_str = f"{qty:.8f}".rstrip("0").rstrip(".")

    params = {
        "category": "spot",
        "symbol": f"{symbol}USDT",
        "side": "Sell",
        "orderType": "Limit",
        "price": price_str,
        "qty": qty_str,
        "timeInForce": "GTC",
    }
    log(f"TP Limit request: {params}")
    resp = session.place_order(**params)
    log(f"TP Limit response: {resp.get('retCode')} {resp.get('retMsg')}")
    return resp

# ---------- Обработка сигналов ----------

async def handle_signal(signal_text: str):
    action, symbols = parse_signal(signal_text)
    if not action:
        log(f"IGNORE unknown signal: {signal_text}")
        return

    side = "Buy" if action == "buy" else "Sell"
    log(f"SIGNAL: {signal_text} → {action.upper()} {symbols}, amount={TRADE_AMOUNT_USD} USDT")

    try:
        session = create_session()
    except Exception as e:
        log(f"ERROR create_session: {e}")
        return

    for ticker in symbols:
        log(f"Working {ticker}USDT ...")
        try:
            resp, base_qty_est = await place_spot_market_order(session, ticker, side)
            if resp.get("retCode") != 0:
                log(f"ERROR market order rejected: {resp}")
                continue

            action_word = "куплено" if side == "Buy" else "продано"
            base_log = f"{action_word} {ticker} на {TRADE_AMOUNT_USD}$"

            if side == "Buy" and base_qty_est:
                tp_resp = await place_spot_tp_limit_order(session, ticker, base_qty_est)
                if tp_resp and tp_resp.get("retCode") == 0:
                    log(base_log + " + TP-limit set")
                else:
                    log(base_log + ", TP-limit not set")
            else:
                log(base_log + ", TP not set (Sell or no qty)")
        except Exception as e:
            log(f"ERROR processing {ticker}: {e}")

    log("-" * 50)

# ---------- WebSocket ----------

async def websocket_handler(*args, **kwargs):
    websocket = args[0]
    log("Client connected (spot)")
    try:
        async for message in websocket:
            log(f"FROM TG: {message.strip()}")
            await handle_signal(message)
    except Exception as e:
        log(f"WS error: {e}")

async def main():
    log(f"WebSocket server: ws://localhost:{WS_PORT}")
    server = await websockets.serve(websocket_handler, "localhost", WS_PORT)
    log("Spot bot ready, waiting for signals...")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
