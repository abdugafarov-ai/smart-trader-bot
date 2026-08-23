# 🤖 Smart Trader Bot

Профессиональный Telegram-бот для анализа финансовых рынков с мульти-стратегийным подходом.

---

## ✨ Возможности

### 📊 Торговые стратегии (6 штук)
| Стратегия | Описание |
|---|---|
| 🧠 **ICT / SMC** | BOS, CHoCH, Order Blocks, FVG, OTE зоны, Fibonacci |
| 📦 **Supply & Demand** | Зоны спроса/предложения, импульсные движения |
| 📈 **Wyckoff** | Фазы рынка, Spring, UTAD, SOS/SOW, Effort vs Result |
| 🔓 **Breakout + Retest** | Пробои уровней, ретесты, качество пробоя |
| ⚡ **Scalping** | EMA кроссоверы, RSI дивергенции, VWAP отскоки |
| 📊 **Volume Analysis** | Volume Profile, кумулятивная дельта, кульминации |

### 📈 Анализ
- 🔄 **Мульти-таймфрейм** — M15, H1, H4, D1
- 📍 **Entry / 🛑 Stop / 🎯 TP1 & TP2 / 📐 R:R** в каждом сигнале
- ⭐ **Фильтр уверенности** — уведомления только для ⭐⭐⭐⭐ и ⭐⭐⭐⭐⭐
- 💱 **24 торговые пары** — Мажоры, Кроссы, Металлы, Индексы, Крипто

### 🔔 Автоматические уведомления
- Сканирование каждые 15 минут
- Предупреждения о важных новостях (за 30 и 5 мин)
- Блокировка сигналов при предстоящих новостях
- На выходных — только крипто-пары

### 📊 Отчётность
- 🏆 **Win-Rate трекер** — отслеживает TP/SL для каждого сигнала
- 📊 `/stats` — полная статистика: win rate, PnL, топ пары
- 📜 `/history` — история последних 15 сигналов
- 📊 **Еженедельный отчёт** — каждую субботу в 10:00

### 🔒 Система доступа
- Новые пользователи подают заявку через `/request`
- Админ одобряет/отклоняет через inline-кнопки
- `/users` — управление пользователями (только для админа)

---

## 🚀 Установка

### 1. Клонируйте репозиторий
```bash
git clone https://github.com/YOUR_USERNAME/smart-trader-bot.git
cd smart-trader-bot
```

### 2. Установите зависимости
```bash
pip install -r requirements.txt
```

### 3. Настройте конфигурацию
```bash
cp .env.example .env
```
Заполните `.env` файл:
- `BOT_TOKEN` — получите у [@BotFather](https://t.me/BotFather)
- `ADMIN_ID` — узнайте у [@userinfobot](https://t.me/userinfobot)
- `NOTIFY_USER_IDS` — ваш Telegram ID

### 4. Запустите бота
```bash
python main.py
```

---

## 📁 Структура проекта

```
smart_trader_bot/
├── main.py                    # Точка входа
├── config.py                  # Конфигурация
├── requirements.txt           # Зависимости
├── .env.example               # Шаблон настроек
│
├── bot/                       # Telegram-интерфейс
│   ├── handlers.py            # Обработчики команд
│   ├── keyboards.py           # Inline-клавиатуры
│   ├── guide.py               # Интерактивный гайд
│   └── middleware.py          # Контроль доступа
│
├── strategies/                # Торговые стратегии
│   ├── base.py                # Базовый класс + dataclasses
│   ├── ict_smc.py             # ICT / Smart Money Concepts
│   ├── supply_demand.py       # Supply & Demand
│   ├── wyckoff.py             # Wyckoff Method
│   ├── breakout_retest.py     # Breakout + Retest
│   ├── scalping.py            # Scalping
│   └── volume_analysis.py     # Volume Analysis
│
├── market/                    # Рыночные данные
│   ├── data_fetcher.py        # OHLCV (yfinance + ccxt)
│   └── indicators.py          # Технические индикаторы
│
├── notifications/             # Уведомления
│   ├── auto_signals.py        # Авто-сканер сигналов
│   └── weekly_report.py       # Еженедельный отчёт
│
├── news/                      # Экономический календарь
│   └── economic_calendar.py   # Forex Factory API
│
├── sessions/                  # Торговые сессии
│   └── trading_sessions.py    # Лондон/Нью-Йорк/Токио
│
├── db/                        # База данных
│   ├── database.py            # SQLite менеджер
│   ├── signal_tracker.py      # Отслеживание TP/SL
│   └── users.py               # Управление пользователями
│
└── utils/                     # Утилиты
    └── formatters.py          # Форматирование сообщений
```

---

## 📱 Команды бота

| Команда | Описание |
|---|---|
| `/start` | Запуск + интерактивный гайд |
| `/analyze EURUSD` | Мульти-ТФ анализ пары |
| `/signals` | Сводка по всем 24 парам |
| `/stats` | Статистика win-rate |
| `/history` | История последних сигналов |
| `/news` | Экономический календарь |
| `/sessions` | Торговые сессии |
| `/request` | Подать заявку на доступ |
| `/users` | Управление пользователями (админ) |

---

## ⚙️ Технологии

- **Python 3.12+**
- **aiogram 3.x** — Telegram Bot API
- **yfinance** — Forex/Commodities/Indices данные
- **ccxt** — Crypto данные (Binance)
- **ta** — Технические индикаторы
- **aiosqlite** — SQLite для хранения сигналов
- **aiohttp** — HTTP для Forex Factory

---

## ⚠️ Дисклеймер

Бот предназначен **только для образовательных целей**. Торговля на финансовых рынках сопряжена с риском потери капитала. Автор не несёт ответственности за финансовые убытки.

---

## 📄 Лицензия

MIT License
