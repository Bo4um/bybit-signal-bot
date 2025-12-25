#!/usr/bin/env python3
"""
Bybit Signal Bot - USDT Perpetual (linear):
- #COIN bullish         -> LONG с плечом, TP +15%
- Short #COIN1 #COIN2   -> SHORT с плечом, TP +15%
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
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
WS_PORT = 8766
TP_PCT = 0.15
DEMO = os.getenv("BYBIT_DEMO", "true").lower() == "true"

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[FUTURES {ts}] {msg}")

log(f"Bybit Futures Bot started | amount={TRADE_AMOUNT_USD} USDT, lev=x{LEVERAGE}, demo={DEMO}, TP={TP_PCT*100:.0f}%")

# ---------- Парсинг сигналов ----------

def parse_signal(message: str):
    message = message.strip().lower()
    bullish_match = re.search(r'#(\w+)\s+bullish', message)
    if bullish_match:
        return "long", [bullish_match.group(1).upper()]
    short_match = re.search(r'short\s+((?:#\w+\s+)*#\w+)', message)
    if short_match:
        tickers = re.findall(r'#(\w+)', short_match.group(1))
        return "short", [t.upper() for t in tickers]
    return None, []

# ---------- Bybit helpers (linear) ----------

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
        r = session.get_tickers(category="linear", symbol=f"{symbol}USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            log(f"WARNING no ticker {symbol}USDT (linear)")
            return None
        return float(lst[0]["lastPrice"])
    except Exception as e:
        log(f"ERROR get_tickers linear {symbol}: {e}")
        return None

def get_linear_instrument_info(session, symbol: str):
    try:
        r = session.get_instruments_info(category="linear", symbol=f"{symbol}USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            log(f"WARNING no instruments-info for {symbol}USDT linear")
            return None
        info = lst[0]
        lot = info.get("lotSizeFilter", {})
        min_qty = float(lot.get("minOrderQty", "0.001"))
        qty_step = float(lot.get("qtyStep", "0.001"))
        return {"minQty": min_qty, "qtyStep": qty_step}
    except Exception as e:
        log(f"ERROR get_instruments_info linear {symbol}: {e}")
        return None

def round_up_to_step(value: float, step: float) -> float:
    import math
    return math.ceil(value / step) * step

def set_leverage(session, symbol: str):
    try:
        resp = session.set_leverage(
            category="linear",
            symbol=f"{symbol}USDT",
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
        log(f"set_leverage: {resp.get('retCode')} {resp.get('retMsg')}")
    except Exception as e:
        log(f"ERROR set_leverage {symbol}: {e}")

def calc_qty_usdt(session, symbol: str, last_price: float) -> float:
    info = get_linear_instrument_info(session, symbol)
    if not info:
        return TRADE_AMOUNT_USD / last_price
    raw = TRADE_AMOUNT_USD / last_price
    min_qty = info["minQty"]
    step = info["qtyStep"]
    qty = max(raw, min_qty)
    qty = round_up_to_step(qty, step)
    return qty

async def open_linear_position(session, symbol: str, direction: str):
    last = get_last_price(session, symbol)
    if not last:
        log("WARNING no price, skip position")
        return None

    qty = calc_qty_usdt(session, symbol, last)
    side = "Buy" if direction == "long" else "Sell"

    set_leverage(session, symbol)

    params = {
        "category": "linear",
        "symbol": f"{symbol}USDT",
        "side": side,
        "orderType": "Market",
        "qty": f"{qty:.4f}",
        "positionIdx": 0,
    }

    log(f"Market {direction.upper()} request: {params}")
    resp = session.place_order(**params)
    log(f"Market response: {resp.get('retCode')} {resp.get('retMsg')}")
    if resp.get("retCode") != 0:
        return None

    return {"side": side, "entry_price": last, "qty": qty}

def set_tp_for_position(session, symbol: str, entry_price: float, side: str):
    if side == "Buy":
        tp_price = entry_price * (1 + TP_PCT)
    else:
        tp_price = entry_price * (1 - TP_PCT)

    params = {
        "category": "linear",
        "symbol": f"{symbol}USDT",
        "takeProfit": f"{tp_price:.2f}",
        "tpTriggerBy": "LastPrice",
    }

    log(f"set_trading_stop TP request: {params}")
    resp = session.set_trading_stop(**params)
    log(f"set_trading_stop response: {resp.get('retCode')} {resp.get('retMsg')}")
    return resp

# ---------- Обработка сигналов ----------

async def handle_signal(signal_text: str):
    action, symbols = parse_signal(signal_text)
    if not action:
        log(f"IGNORE unknown signal: {signal_text}")
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    direction = "LONG" if action == "long" else "SHORT"
    log(f"SIGNAL: {signal_text} → {direction} {symbols}, amount={TRADE_AMOUNT_USD} USDT, lev=x{LEVERAGE}")

    try:
        session = create_session()
    except Exception as e:
        log(f"ERROR create_session: {e}")
        return

    for ticker in symbols:
        log(f"Working {ticker}USDT linear ...")
        try:
            pos = await open_linear_position(session, ticker, action)
            if not pos:
                log(f"ERROR position not opened for {ticker}")
                continue

            side = pos["side"]
            entry = pos["entry_price"]
            tp_resp = set_tp_for_position(session, ticker, entry, side)
            if tp_resp.get("retCode") == 0:
                log(f"{ts} {direction} {ticker} with TP set")
            else:
                log(f"{ts} {direction} {ticker}, TP not set")
        except Exception as e:
            log(f"ERROR processing {ticker}: {e}")

    log("-" * 50)

# ---------- WebSocket ----------

async def websocket_handler(*args, **kwargs):
    websocket = args[0]
    log("Client connected (futures)")
    try:
        async for message in websocket:
            log(f"FROM TG: {message.strip()}")
            await handle_signal(message)
    except Exception as e:
        log(f"WS error: {e}")

async def main():
    log(f"WebSocket server: ws://localhost:{WS_PORT}")
    server = await websockets.serve(websocket_handler, "localhost", WS_PORT)
    log("Futures bot ready, waiting for signals...")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
