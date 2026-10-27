# 🤖 Smart Trader Bot — Intraday XAUUSD (`XAUUSD_INTRADAY`)

Автоматизированная алгоритмическая система анализа и внутридневной торговли золотом (**XAUUSD**) через **MetaTrader 5** с управлением и мониторингом в **Telegram**.

---

## 🎯 Основной торговый профиль (`XAUUSD_INTRADAY`)

После проведения технического аудита и количественного исследования система сконфигурирована вокруг единого институционального профиля **`XAUUSD_INTRADAY`**:

- **Торгуемый инструмент:** `XAUUSD` (Золото, 1 лот = 100 тройских унций; нормализация брокерских символов `XAUUSD`, `XAUUSD.pro`, `XAUUSDm`, `GOLD`).
- **Мульти-таймфреймовая иерархия:** `D1/H1` (направление и Dealing Range) $\to$ `M15` (зона интереса POI: немитигированный Order Block / FVG в зоне Discount `< 45%` или Premium `> 55%`) $\to$ `M5` (подтверждение слома структуры MSS + Displacement).
- **Торговые модели:**
  - **Модель B (Основная):** `Trend Pullback -> Unmitigated M15 Order Block in Discount/Premium -> M5 Reversal Confirmation` (не более 1 сделки на один Order Block).
  - **Модель A (Вторичная):** `Liquidity Sweep -> Displacement -> MSS -> FVG`.
  - **Модель C (Breakout + Retest):** отключена (отрицательное математическое ожидание на исторических данных XAUUSD).
- **Единый риск-менеджмент:**
  - По умолчанию реальная автоторговля **ВЫКЛЮЧЕНА** (`AUTOTRADE_ENABLED=false`).
  - Дефолтный риск на сделку: **`0.25%`** от меньшего из баланса и эквити (`$12.50` на счёте `$5,000`).
  - Максимально допустимый риск на сделку: **`0.50%`** (`$25.00` на счёте `$5,000`).
  - Минимальный лот: **`0.01`**. Если структурный стоп-лосс при лоте `0.01` превышает денежный бюджет риска сделки, возвращается **`0.0` лота (`NO TRADE`)**. Искусственное сжатие стоп-лосса под бюджет риска и принудительное повышение лота запрещены.
  - Минимальный R:R: **`1:2.0`**.
  - Сопровождение позиции: перевод стоп-лосса в безубыток с покрытием спреда (**Auto-BE**) при достижении **`+1.0R`**; частичное закрытие на лоте `0.01` отключено (`InpUsePartialClose=false`).
- **Внутридневные временные рамки и DST:**
  - Торговые окна **London Open Kill Zone** (`08:00–11:00 Europe/London`) и **New York Open Kill Zone** (`08:00–11:30 America/New_York`) рассчитываются динамически через `zoneinfo` с учётом переходов на летнее/зимнее время (DST).
  - **20:30 UTC (Пн–Чт) / 19:00 UTC (Пт):** запрет открытия новых сделок (`EOD_NEW_TRADES_CUTOFF_UTC`).
  - **20:40 UTC (Пн–Чт) / 19:30 UTC (Пт):** принудительное закрытие всех внутридневных позиций и снятие отложенных ордеров до банковского ролловера и выходных (`EOD_FLAT_CLOSE_UTC`).

> **Статус устаревших модулей (`LEGACY_MULTI_ASSET`):** Валютные пары Forex (`BANNED_PAIRS`) и альтернативные стратегии (`supply_demand.py`, `wyckoff.py`, `breakout_retest.py`, `scalping.py`, `volume_analysis.py`) отключены в основном профиле и переведены в статус **экспериментальных / неподтверждённых**. Заявления старой версии документации о «Pure Swing без Б/У», «микро-депозитах от $12» и «риске 1.0% на 17 парах» аннулированы.

---

## 📁 Архитектура и структура проекта

```text
smart_trader_bot/
├── main.py                        # Точка входа, валидация конфига и запуск сервисов
├── config.py                      # Профиль XAUUSD_INTRADAY, риск-лимиты, DST-сессии, validate_config()
├── challenge_config.json          # Параметры проп-профиля (загружаются динамически с маскированием ID)
├── requirements.txt               # Зависимости Python
├── .env.example                   # Шаблон переменных окружения (без секретов)
│
├── market/
│   ├── data_fetcher.py            # Чтение строго закрытых баров MT5 (closed_only=True), OHLC-валидация, DXY
│   └── indicators.py              # Технические индикаторы (ATR, EMA, ADX)
│
├── sessions/
│   └── trading_sessions.py        # DST-осведомлённое расписание сессий (London / New York / Tokyo / Sydney)
│
├── strategies/
│   ├── base.py                    # Единый контракт сигнала StrategySignal (setup_id, TTL, risk, rejection_reasons)
│   ├── ict_smc.py                 # Формализованная стратегия ICT/SMC для XAUUSD (Модели A и B)
│   └── [supply_demand, wyckoff, breakout_retest, scalping, volume_analysis].py  # Экспериментальные (отключены)
│
├── trading/
│   ├── execution_bridge.py        # Единый источник истины по риску/лоту, 10-факторная защита, EOD Flat
│   └── ea/
│       ├── SmartTraderBridge.mq5  # Исходный код советника MQL5
│       └── SmartTraderBridge.ex5  # Скомпилированный исполняемый файл советника MT5
│
├── webapp/
│   └── server.py                  # Локальный HTTP-мост MT5 (/api/v1/bridge/orders) с проверкой X-Bridge-Key
│
├── db/
│   ├── database.py                # Асинхронное хранилище SQLite (data/signals.db), идемпотентный учёт сделок
│   ├── signal_tracker.py          # Отслеживание закрытых свечей и жизненных циклов сигналов
│   └── users.py                   # Управление ролями и доступом пользователей
│
├── bot/
│   ├── handlers.py                # Команды Telegram, пульт MT5, мульти-ТФ анализ (D1 -> H1 -> M15 -> M5)
│   ├── keyboards.py               # Клавиатуры интерфейса
│   └── middleware.py              # Контроль доступа
│
├── scripts/
│   └── intraday_gold_engine.py    # Воспроизводимый векторный движок бэктестинга XAUUSD (Модели A/B/C, WF, стресс-тесты)
│
├── tests/                         # Набор автоматических модульных и интеграционных тестов (unittest)
└── docs/
    ├── final_master_audit_xauusd.md  # Полный технический отчёт (Разделы A–J)
    └── intraday_suite_results.json   # Результаты количественного тестирования
```

---

## 🛠️ Эксплуатационные инструкции (Раздел 27)

### 1. Установка окружения
Требуется **Python 3.11+ / 3.12+**. Создайте и активируйте изолированное виртуальное окружение:
```bash
git clone https://github.com/abdugafarov-ai/smart-trader-bot.git
cd smart-trader-bot

# Linux / macOS:
python3 -m venv venv
source venv/bin/activate

# Windows (PowerShell):
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Проверка и установка зависимостей
Установите зависимости из `requirements.txt` и проверьте корректность импортов и конфигурации:
```bash
pip install --upgrade pip
pip install -r requirements.txt
python -c "import config; print(config.validate_config())"
```
Ожидаемый вывод: `{'valid': True, 'profile': 'XAUUSD_INTRADAY', 'autotrade_enabled': False, 'errors': []}`.

### 3. Конфигурация и порядок приоритета настроек
Скопируйте шаблон `.env.example` в `.env`:
```bash
cp .env.example .env
chmod 600 .env   # На Linux VPS
```
Заполните обязательные параметры в `.env`:
- `BOT_TOKEN` — токен Telegram-бота;
- `ADMIN_ID` — числовой Telegram ID администратора;
- `BRIDGE_API_KEY` — криптографический ключ авторизации между Python HTTP-мостом и советником MT5;
- `AUTOTRADE_ENABLED=false` — **обязательное безопасное значение по умолчанию**;
- `AUTOTRADE_DEFAULT_RISK=0.25` — риск на сделку в процентах (допустимый диапазон `0.05`–`0.50`);
- `AUTOTRADE_MAX_RISK=0.50` — жёсткий верхний потолок риска;
- `AUTOTRADE_DEFAULT_LOT=0.01` — базовый минимальный объём.

**Явный порядок приоритета настроек безопасности:**
1. **Жёсткие инварианты ядра (`config.validate_config` и `ExecutionBridge`):** потолок риска `0.50%`, запрет превышения бюджета риска на лоте `0.01`, блокировка при срабатывании `equity_hard_stop_triggered` или `daily_loss_locked` — **не могут быть переопределены ни БД, ни командами Telegram, ни советником MQL5**.
2. **Переменные окружения (`.env`):** при старте системы `AUTOTRADE_ENABLED=false` в `.env` блокирует самопроизвольное включение автоторговли из SQLite.
3. **Состояние SQLite (`bot_settings`) и команды администратора Telegram (`/risk`, `/lot`, `/autotrade`):** действуют только внутри разрешённых границ ядра (`0.05% <= risk <= 0.50%`, `0.01 <= lot <= 0.50`, с обязательной проверкой денежного риска на каждом ордере).
4. **Параметры советника MT5 (`SmartTraderBridge.mq5`):** советник выполняет финальную независимую проверку `CalculateRiskLot(symbol, entry, sl)` и понижает объём либо отклоняет ордер, если расчётный риск превышает лимит.

### 4. Запуск Telegram-бота
```bash
python main.py
```
На продакшн/исследовательском Linux VPS запуск осуществляется через `systemd`:
```bash
sudo systemctl status smart-trader-bot.service
sudo systemctl restart smart-trader-bot.service
sudo journalctl -u smart-trader-bot.service -f
```

### 5. Подключение MetaTrader 5 и компиляция советника
1. Скопируйте [`trading/ea/SmartTraderBridge.mq5`](file:///C:/Users/user/.gemini/antigravity/scratch/smart_trader_bot/trading/ea/SmartTraderBridge.mq5) в каталог `MQL5/Experts/SmartTraderBridge.mq5` терминала MetaTrader 5.
2. Скомпилируйте советник через MetaEditor (или из командной строки Wine на VPS):
   ```bash
   sudo -u trader WINEPREFIX="/home/trader/.wine" DISPLAY=:1 wine \
     "C:\\PROG~FBU\\META~QGP\\metaeditor64.exe" \
     /compile:"C:\\PROG~FBU\\META~QGP\\MQL5\\Experts\\SmartTraderBridge.mq5" /log
   ```
   Убедитесь, что в `SmartTraderBridge.log` указано `0 errors, 0 warnings`.
3. В настройках терминала MT5 (`Tools -> Options -> Expert Advisors`):
   - Добавьте `http://127.0.0.1:8080` в список разрешённых URL для `WebRequest`.
   - Прикрепите `SmartTraderBridge` к графику `XAUUSD` (`M15`) и укажите `InpApiKey`, совпадающий с `BRIDGE_API_KEY` из `.env`.
   - Проверьте, что советник начал экспортировать закрытые свечи `candles_XAUUSD_M5.csv`, `candles_XAUUSD_M15.csv`, `candles_XAUUSD_H1.csv`, `candles_XAUUSD_D1.csv` в `MQL5/Files/`.

### 6. Запуск автоматических тестов
Перед любым деплоем или включением демо-режима запустите полный набор модульных и интеграционных тестов:
```bash
python -m unittest discover -s tests -v
```
Все тесты должны завершиться со статусом `OK`.

### 7. Исторический бэктестинг
Для воспроизводимого запуска количественного симулятора на исторических барах `XAUUSD` (`M5`/`M15`/`H1`) выполните:
```bash
python scripts/intraday_gold_engine.py
```
Скрипт выполняет сравнение моделей A, B, C, базовой унаследованной версии и контрольного варианта без сделок, 3-этапное разбиение (`Train / Validation / Out-of-Sample`), Walk-Forward анализ, исследование вклада отдельных фильтров (Ablation Study) и стресс-тесты издержек, сохраняя полный JSON-отчёт в [`docs/intraday_suite_results.json`](file:///C:/Users/user/.gemini/antigravity/scratch/smart_trader_bot/docs/intraday_suite_results.json) и `data/intraday_suite_results.json`.

### 8. Протокол демо-forward-тестирования
1. Подключите терминал MT5 к **демо-счёту** того же брокера с идентичной спецификацией `XAUUSD` (`contract_size=100`, `volume_min=0.01`).
2. Запустите систему сначала в режиме `AUTOTRADE_ENABLED=false` (Signals Only) на 1–2 сессии и убедитесь, что телеметрия MT5 (`/account`), экспорт CSV-баров и расчёт лота работают без ошибок.
3. Переключите автоторговлю на демо-счёте (`/autotrade on`) при риске `0.25%` (`/risk 0.25`).
4. Проводите непрерывный демо-forward-тест **не менее 20 торговых сессий** (или минимум 50 закрытых сделок), сверяя фактический спред, проскальзывание, срабатывание Auto-BE на `+1.0R` и принудительное закрытие EOD Flat в `20:40 UTC`.

### 9. Разблокировка после аварийной остановки (Kill-Switch / Hard Stop / Daily Loss Lock)
При срабатывании защитных блокировок (`Equity Hard Stop`, `Daily Drawdown Limit 2.5%` или `Consecutive SL Stop`) автоторговля принудительно выключается, а флаг блокировки сохраняется в SQLite (`bot_settings`) и **не сбрасывается простым перезапуском бота**.
Для штатной разблокировки после проверки счёта администратором:
1. Проверьте причину остановки в пульте `/terminal` или `/autotrade`.
2. Если сработал `Equity Hard Stop`, выполните команду администратора:
   ```text
   /autotrade unlock
   ```
3. Если сработал дневной лимит просадки или анти-тильт после серии стоп-лоссов, выполните:
   ```text
   /reset_drawdown
   ```
4. После снятия блокировки автопилот остаётся в безопасном состоянии `ВЫКЛ` — при необходимости включите его вручную через `/autotrade on`.

### 10. Безопасное отключение автоторговли
- **Штатное отключение новых входов (с сохранением сопровождения открытых сделок):**
  Отправьте команду `/autotrade off` в Telegram (или нажмите кнопку **«🔴 ВЫКЛЮЧИТЬ АВТОПИЛОТ»** в `/terminal`) и установите `AUTOTRADE_ENABLED=false` в `.env`.
- **Экстренная остановка с немедленным закрытием всех позиций (Panic Kill-Switch):**
  В меню `/terminal` нажмите **«🚨 ПАНИКА: ЗАКРЫТЬ ВСЕ СДЕЛКИ»**. Бот немедленно выключит автопилот и передаст команду `PANIC_CLOSE_ALL` советнику MT5 для закрытия всех активных позиций и удаления отложенных ордеров.

---

## 📱 Основные команды Telegram

| Команда | Роль | Описание |
|---|---|---|
| `/start` | Все | Главное меню терминала |
| `/terminal` | Админ | Пульт управления MT5 (статус моста, пинг, риски, кнопка «Паника») |
| `/account` | Админ | Фактический баланс, эквити, свободная маржа и открытые позиции MT5 |
| `/autotrade [on\|off\|unlock]` | Админ | Управление автопилотом и разблокировка после Hard Stop |
| `/risk 0.25` | Админ | Установка риска на сделку (`0.05%` – `0.50%`) |
| `/lot 0.01` | Админ | Установка базового лота (`0.01` – `0.50`, с проверкой потолка риска) |
| `/reset_drawdown` | Админ | Ручной сброс дневной блокировки просадки и счётчика SL-серии |
| `/stats` | Одобренные | Статистика исполненных сделок и сигналов |
| `/history` | Одобренные | Журнал последних сделок |
| `/sessions` | Одобренные | Текущие торговые сессии и статус Kill Zones (DST-aware) |
| `/news` | Одобренные | Календарь макроэкономических новостей |

---

## ⚠️ Дисклеймер

Программный комплекс предназначен для алгоритмических исследований и автоматизации исполнения по строго заданным правилам риска. Исторические бэктесты и краткосрочные демо-тесты не гарантируют будущей прибыли или прохождения проп-челленджа. Торговля маржинальными деривативами (`XAUUSD`) сопряжена с высоким финансовым риском.
