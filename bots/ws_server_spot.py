#!/usr/bin/env python3
"""
Bybit Signal Bot - SPOT:
- #COIN bullish         -> Market Buy на сумму в USDT + TP Limit (+15%)
- Short #COIN1 #COIN2   -> Market Sell на сумму в USDT без TP
"""

import asyncio
import logging
import os
import re
import signal
import time
import random
from datetime import datetime
from pathlib import Path

import websockets
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

load_dotenv()

# Настройка логирования
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("spot_bot")
logger.setLevel(logging.DEBUG)

# Консольный обработчик
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    fmt="[SPOT %(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(console_formatter)

# Файловый обработчик с ротацией
file_handler = logging.FileHandler(LOG_DIR / "spot.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Graceful shutdown
shutdown_event = asyncio.Event()

def handle_shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    shutdown_event.set()

signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ---------- Обработка ошибок API ----------

RETRYABLE_CODES = {10002, 10003, 10004, 10005, 10006, 10016}  # Временные ошибки
MAX_RETRIES = 3
BASE_DELAY = 1.0  # секунды

class APIError(Exception):
    """Ошибка API Bybit."""
    def __init__(self, ret_code: int, ret_msg: str, retryable: bool = False):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.retryable = retryable
        super().__init__(f"API Error {ret_code}: {ret_msg}")

def with_retry(func):
    """Декоратор для повторных попыток при временных ошибках API."""
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                result = func(*args, **kwargs)
                # Проверка кода ответа
                if isinstance(result, dict):
                    ret_code = result.get("retCode", 0)
                    ret_msg = result.get("retMsg", "")
                    if ret_code != 0:
                        retryable = ret_code in RETRYABLE_CODES
                        error = APIError(ret_code, ret_msg, retryable)
                        logger.warning(f"API error (attempt {attempt + 1}/{MAX_RETRIES}): {ret_code} - {ret_msg}")
                        if retryable and attempt < MAX_RETRIES - 1:
                            delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                            logger.info(f"Retrying in {delay:.2f}s...")
                            time.sleep(delay)
                            continue
                        raise error
                return result
            except APIError:
                raise
            except Exception as e:
                last_error = e
                logger.error(f"API call error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                else:
                    raise APIError(-1, str(last_error), retryable=True)
        return None
    return wrapper

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TRADE_AMOUNT_USD = float(os.getenv("TRADE_AMOUNT_USD", "5"))
WS_PORT = 8765
TP_PCT = 0.15  # +15%
DEMO = os.getenv("BYBIT_DEMO", "true").lower() == "true"

logger.info(f"Bybit Spot Bot started | amount={TRADE_AMOUNT_USD} USDT, demo={DEMO}, TP={TP_PCT*100:.0f}%")

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

# ---------- Валидация ----------

TICKER_PATTERN = re.compile(r'^[A-Z]{1,10}$')  # 1-10 заглавных букв

def validate_ticker_format(ticker: str) -> bool:
    """Проверка формата тикера (только буквы, 1-10 символов)."""
    return bool(TICKER_PATTERN.match(ticker))

def validate_ticker_exists(session: HTTP, ticker: str, category: str) -> bool:
    """Проверка существования тикера на бирже."""
    try:
        r = session.get_instruments_info(category=category, symbol=f"{ticker}USDT")
        lst = r.get("result", {}).get("list", [])
        return len(lst) > 0
    except Exception as e:
        logger.error(f"validate_ticker_exists {ticker}: {e}")
        return False

def validate_tickers(session: HTTP, tickers: list[str], category: str) -> list[str]:
    """Валидация списка тикеров. Возвращает только корректные."""
    valid = []
    for ticker in tickers:
        if not validate_ticker_format(ticker):
            logger.warning(f"Invalid ticker format: {ticker}")
            continue
        if not validate_ticker_exists(session, ticker, category):
            logger.warning(f"Ticker not found on {category}: {ticker}")
            continue
        valid.append(ticker)
        logger.info(f"Ticker validated: {ticker}")
    return valid

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
            logger.warning(f"No ticker {symbol}USDT in get_tickers")
            return None
        return float(lst[0]["lastPrice"])
    except APIError as e:
        logger.error(f"get_tickers {symbol}: {e.ret_code} - {e.ret_msg}")
        return None
    except Exception as e:
        logger.error(f"get_tickers {symbol}: {e}")
        return None

def get_instrument_info(session, symbol: str):
    try:
        r = session.get_instruments_info(category="spot", symbol=f"{symbol}USDT")
        lst = r.get("result", {}).get("list", [])
        if not lst:
            logger.warning(f"No instruments-info for {symbol}USDT")
            return None
        info = lst[0]
        price_filter = info.get("priceFilter", {})
        lot_filter = info.get("lotSizeFilter", {})
        tick_size = float(price_filter.get("tickSize", "0.01"))
        qty_step = float(lot_filter.get("qtyStep", "0.0001"))
        min_qty = float(lot_filter.get("minOrderQty", "0.0001"))
        return {"tickSize": tick_size, "qtyStep": qty_step, "minQty": min_qty}
    except APIError as e:
        logger.error(f"get_instruments_info {symbol}: {e.ret_code} - {e.ret_msg}")
        return None
    except Exception as e:
        logger.error(f"get_instruments_info {symbol}: {e}")
        return None

def round_down(value: float, step: float) -> float:
    return (value // step) * step

@with_retry
def place_spot_market_order(session, symbol: str, side: str):
    params = {
        "category": "spot",
        "symbol": f"{symbol}USDT",
        "side": side,
        "orderType": "Market",
        "marketUnit": "quoteCoin",
        "qty": str(TRADE_AMOUNT_USD),
    }
    logger.info(f"Market {side} request: {params}")
    resp = session.place_order(**params)
    logger.info(f"Market response: {resp.get('retCode')} {resp.get('retMsg')}")
    base_qty = None
    last = get_last_price(session, symbol)
    if last:
        base_qty = TRADE_AMOUNT_USD / last
    return resp, base_qty

@with_retry
def place_spot_tp_limit_order(session, symbol: str, base_qty: float):
    info = get_instrument_info(session, symbol)
    if not info:
        logger.warning("No instrument info, skip TP")
        return None
    last = get_last_price(session, symbol)
    if not last:
        logger.warning("No last price, skip TP")
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
    logger.info(f"TP Limit request: {params}")
    resp = session.place_order(**params)
    logger.info(f"TP Limit response: {resp.get('retCode')} {resp.get('retMsg')}")
    return resp

# ---------- Обработка сигналов ----------

async def handle_signal(signal_text: str):
    action, symbols = parse_signal(signal_text)
    if not action:
        logger.warning(f"IGNORE unknown signal: {signal_text}")
        return

    try:
        session = create_session()
    except Exception as e:
        logger.error(f"create_session: {e}")
        return

    # Валидация тикеров
    valid_symbols = validate_tickers(session, symbols, category="spot")
    if not valid_symbols:
        logger.error(f"No valid tickers in signal: {symbols}")
        return

    side = "Buy" if action == "buy" else "Sell"
    logger.info(f"SIGNAL: {signal_text} → {action.upper()} {valid_symbols}, amount={TRADE_AMOUNT_USD} USDT")

    for ticker in valid_symbols:
        logger.info(f"Working {ticker}USDT ...")
        try:
            resp, base_qty_est = place_spot_market_order(session, ticker, side)
            if resp.get("retCode") != 0:
                logger.error(f"Market order rejected: {resp}")
                continue

            action_word = "куплено" if side == "Buy" else "продано"
            base_log = f"{action_word} {ticker} на {TRADE_AMOUNT_USD}$"

            if side == "Buy" and base_qty_est:
                tp_resp = place_spot_tp_limit_order(session, ticker, base_qty_est)
                if tp_resp and tp_resp.get("retCode") == 0:
                    logger.info(f"{base_log} + TP-limit set")
                else:
                    logger.info(f"{base_log}, TP-limit not set")
            else:
                logger.info(f"{base_log}, TP not set (Sell or no qty)")
        except APIError as e:
            if e.retryable:
                logger.error(f"Temporary API error for {ticker}: {e.ret_code} - {e.ret_msg}")
            else:
                logger.error(f"Critical API error for {ticker}: {e.ret_code} - {e.ret_msg}")
        except Exception as e:
            logger.error(f"Processing {ticker}: {e}")

    logger.info("-" * 50)

# ---------- WebSocket ----------

async def websocket_handler(*args, **kwargs):
    websocket = args[0]
    logger.info("Client connected (spot)")
    try:
        async for message in websocket:
            logger.info(f"FROM TG: {message.strip()}")
            await handle_signal(message)
    except Exception as e:
        logger.error(f"WS error: {e}")

async def main():
    logger.info(f"WebSocket server: ws://localhost:{WS_PORT}")
    server = await websockets.serve(websocket_handler, "localhost", WS_PORT)
    logger.info("Spot bot ready, waiting for signals...")
    
    # Ждём сигнала остановки
    await shutdown_event.wait()
    
    # Graceful shutdown
    logger.info("Closing WebSocket server...")
    server.close()
    await server.wait_closed()
    logger.info("Spot bot stopped gracefully")

if __name__ == "__main__":
    asyncio.run(main())
