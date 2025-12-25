# Bybit Signal Bot

Автоматическое исполнение торговых сигналов из Telegram на бирже Bybit: спотовый бот, фьючерсный бот (USDT Perpetual) и Telegram‑шлюз для переключения режимов `/spot` и `/futures`.[web:13][web:77]

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Telegram](https://img.shields.io/badge/Telegram-Bot-blue)

---

## Оглавление

- [Возможности](#возможности)
- [Архитектура](#архитектура)
- [Формат сигналов](#формат-сигналов)
- [Настройка](#настройка)
- [Установка и запуск](#установка-и-запуск)
- [Поведение ботов](#поведение-ботов)
- [Идеи для расширения](#идеи-для-расширения)

---

## Возможности

- Приём сигналов из Telegram (формат `#ETH bullish`, `Short #ETH #BTC`).  

- Исполнение сигналов на **споте** и **фьючерсах (USDT perpetual)** через Bybit V5 API.[web:13][web:77]  

- Автоматическая установка **тейк‑профита +15%**:
  - на споте — отдельный Limit TP‑ордер;  
  - на фьючерсах — TP через `set-trading-stop` (TP/SL во вкладке Bybit).[web:38][web:82]  
- Переключение режима прямо в Telegram: `/spot` и `/futures`.  

- Учёт торговых правил Bybit (`tickSize`, `qtyStep`, `minOrderQty`) для корректных объёма и цены.[web:37][web:38]

---

Компоненты:

- **Spot‑бот** (`ws_server_spot.py`)  
  Поднимает WebSocket‑сервер, принимает сигналы от шлюза и торгует спотом по API V5 (`category=spot`). 

- **Futures‑бот** (`ws_server_linear.py`)  
  Аналогично, но работает с USDT perpetual контрактами (`category=linear`), устанавливает плечо и TP через `set-trading-stop`.  

- **Telegram‑шлюз** (`telegram_gateway.py`)  
  Telegram‑бот на `python-telegram-bot`, который пересылает текстовые сигналы по WebSocket на нужного торгового бота.

---

## Формат сигналов

Поддерживаются два типа сигналов (регистр не важен):

- **Лонг**:

#ETH bullish

- **Шорт (несколько тикеров)**:

Short #ETH #BTC

Парсер:

- `#COIN bullish` → действие `LONG/Buy` по одному тикеру.  

- `Short #COIN1 #COIN2` → действие `SHORT/Sell` по каждому перечисленному тикеру.

---

## Настройка

Все параметры задаются в `.env` в корне проекта:

Bybit API
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret

Использовать demo (api-demo.bybit.com). Для боевого mainnet-ключа поставить false
BYBIT_DEMO=true

Общая сумма сделки (в USDT), применяется и к spot, и к futures
TRADE_AMOUNT_USD=5

Плечо для фьючерсов (USDT perpetual)
LEVERAGE=5

Telegram
TELEGRAM_BOT_TOKEN=xxxx:yyyy

WebSocket адреса локальных ботов
SPOT_WS_URL=ws://localhost:8765
FUTURES_WS_URL=ws://localhost:8766

Ключи и токены не хранятся в коде и читаются только из `.env` через `python-dotenv`.

---

## Установка и запуск

### 1. Установка зависимостей

git clone <repo-url> bybit-signal-bot
cd bybit-signal-bot

pip install -r requirements.txt


### 2. Запуск спотового бота

- Поднимается `ws://localhost:8765`.  
- Обрабатываются сигналы на спот‑рынке.

### 3. Запуск фьючерсного бота

В отдельном терминале:

cd bybit-signal-bot
python3 bots/ws_server_linear.py

- Поднимается `ws://localhost:8766`. 

- Открываются long/short позиции по USDT perpetual контрактам с плечом `LEVERAGE` и TP +15%.

### 4. Запуск Telegram‑шлюза

В третьем терминале:

cd bybit-signal-bot
python3 gateway/telegram_gateway.py

Доступные команды:

- `/start` — краткая справка.  
- `/spot` — отправлять сигналы на спотовый бот (`SPOT_WS_URL`).  
- `/futures` — отправлять сигналы на фьючерсный бот (`FUTURES_WS_URL`).  
- Любой текст (например, `#ETH bullish` или `Short #ETH #BTC`) пересылается в текущий режим и подтверждается ответом “Сигнал отправлен в SPOT/FUTURES бот.

---

## Поведение ботов

### Spot‑бот

Для каждого тикера:

- `#ETH bullish`  
  - Отправляется `Market Buy` по `ETHUSDT` на сумму `TRADE_AMOUNT_USD` (через V5 `/v5/order/create`). 
  - Затем бот запрашивает `instruments-info` и `get_tickers`, рассчитывает TP‑цену `lastPrice * 1.15`, округляет её по `tickSize` и ставит отдельный **Limit Sell TP** ордер.[web:38]

- `Short #ETH #BTC`  
  - Для каждого указанного тикера отправляется `Market Sell` на сумму `TRADE_AMOUNT_USD`.  
  - TP не ставится (согласно требованиям ТЗ).

### Futures‑бот (USDT Perpetual)

Для каждого тикера:

- `#ETH bullish`  
  - Через `get_tickers` берётся текущая цена.  
  - Рассчитывается начальный объём: `qty = TRADE_AMOUNT_USD / lastPrice`.  
  - Объём приводится к минимальному и кратному шагу по данным `instruments-info` (`minOrderQty`, `qtyStep`). 
  - Выставляется **Market Buy** (LONG) с `category=linear`, `positionIdx=0`, и предварительно устанавливается плечо `set_leverage`. 
  - После открытия позиции вызывается `/v5/position/set-trading-stop` с `takeProfit = entryPrice * 1.15`, `tpTriggerBy = LastPrice`, что создаёт TP во вкладке TP/SL на Bybit. 

- `Short #ETH #BTC`  
  - Аналогично, но вместо Market Buy отправляется **Market Sell** (SHORT).  
  - TP ставится на `entryPrice * 0.85` (−15%), закрывая всю позицию при достижении цели.

---

## Идеи для расширения

- **Анти‑дубликаты**: не открывать новую позицию, если по символу уже есть активная (через `/v5/position/list` или `/v5/order/realtime`). 
- **Логирование в файл**: дублировать логи в `logs/spot.log` и `logs/futures.log`.  
- **Гибкие уровни TP/SL**: задавать проценты через команду в Telegram или по данным из сигнала.  
- **Поддержка других типов контрактов** (inverse, опционы) по аналогичной схеме.

---

> Проект спроектирован так, чтобы его можно было легко расширять: спотовый и фьючерсный боты изолированы, а Telegram‑шлюз отвечает только за приём и маршрутизацию сигналов.