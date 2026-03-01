#!/usr/bin/env python3
"""
Telegram → WebSocket шлюз с режимами:
/spot    - сигналы идут на spot-бот (ws://localhost:8765)
/futures - сигналы идут на futures-бот (ws://localhost:8766)
"""

import logging
import os
from pathlib import Path

import websockets
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# Настройка логирования
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("telegram_gateway")
logger.setLevel(logging.DEBUG)

# Консольный обработчик
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    fmt="[GATEWAY %(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(console_formatter)

# Файловый обработчик
file_handler = logging.FileHandler(LOG_DIR / "gateway.log", encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOT_WS_URL = os.getenv("SPOT_WS_URL", "ws://localhost:8765")
FUTURES_WS_URL = os.getenv("FUTURES_WS_URL", "ws://localhost:8766")

CURRENT_MODE = "spot"

def current_ws_url() -> str:
    return SPOT_WS_URL if CURRENT_MODE == "spot" else FUTURES_WS_URL

# ---------- Команды ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я шлюз сигналов.\n"
        "/spot - отправлять сигналы на спотового бота\n"
        "/futures - отправлять сигналы на фьючерсного бота\n\n"
        "Сигналы вида '#ETH bullish' или 'Short #ETH #BTC' просто пиши в этот чат."
    )

async def cmd_spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_MODE
    CURRENT_MODE = "spot"
    logger.info("Mode switched to SPOT")
    await update.message.reply_text(f"Режим: SPOT\nWebSocket: {SPOT_WS_URL}")

async def cmd_futures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_MODE
    CURRENT_MODE = "futures"
    logger.info("Mode switched to FUTURES")
    await update.message.reply_text(f"Режим: FUTURES\nWebSocket: {FUTURES_WS_URL}")

# ---------- Пересылка сигналов ----------

async def forward_to_ws(text: str):
    ws_url = current_ws_url()
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(text)
            logger.info("Sent signal '%s' to %s", text, ws_url)
    except Exception as e:
        logger.error("WebSocket error (%s): %s", ws_url, e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    logger.info(f"Received signal from Telegram: {text}")
    await forward_to_ws(text)
    await update.message.reply_text(f"Сигнал отправлен в {CURRENT_MODE.upper()} бот.")

# ---------- Запуск ----------

def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("spot", cmd_spot))
    app.add_handler(CommandHandler("futures", cmd_futures))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram gateway started. Use /spot or /futures.")
    print("🚀 Telegram gateway started. Use /spot or /futures.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
