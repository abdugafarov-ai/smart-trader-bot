# SMART TRADER BOT — PHASE 0 DEPENDENCY MANIFEST

**Audit Date:** 2026-11-01 14:00 UTC  
**Environment:** Windows 11 (10.0.26300-SP0) 64-bit  
**Python Executable:** `C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe`  
**Python Version:** Python 3.12.2  
**Pip Version:** pip 26.0.1  

---

## 1. System & Runtime Environment

| Property | Value | Notes |
|---|---|---|
| **Operating System** | Windows 11 Home/Pro (Build 26300) | 64-bit WindowsPE |
| **Python Version** | 3.12.2 | Official CPython 64-bit build |
| **Pip Version** | 26.0.1 | Upgraded to latest package installer |
| **Execution Context** | System Python Installation | No active virtual environment in directory |
| **MetaTrader 5 Python Package** | **NOT INSTALLED** | Bot uses HTTP WebRequest bridge (`SmartTraderBridge.mq5`), not Python MT5 DLL |
| **MetaEditor / Terminal 64** | **NOT DETECTED LOCALLY** | EA compiled file `SmartTraderBridge.ex5` present; terminal runs on VPS/Wine |

---

## 2. Requirements.txt vs Installed Environment Comparison

The repository contains `requirements.txt` (last updated 2026-09-27). A strict audit against the active Python 3.12 environment reveals substantial version drift and multiple undeclared core dependencies:

| Package | `requirements.txt` | Installed Version | Project Component / Usage | Status / Drift |
|---|---|---|---|---|
| `aiogram` | `==3.15.0` | `3.30.0` | `bot/handlers.py`, `bot/middleware.py`, `main.py` | ⚠️ Major Version Drift (+15 minor releases) |
| `ccxt` | `==4.4.25` | `4.5.67` | `market/data_fetcher.py` (crypto fallback, disabled) | ⚠️ Version Drift (+0.1.42 releases) |
| `pandas` | `==2.2.2` | `3.0.2` | `strategies/ict_smc.py`, `scripts/`, backtests | ⚠️ Major Version Drift (Pandas 2.x -> 3.x) |
| `ta` | `==0.11.0` | `0.11.0` | `market/indicators.py` | ✅ Exact Match |
| `python-dotenv` | `==1.0.1` | `1.2.2` | `config.py` (.env loader) | ⚠️ Minor Drift |
| `aiohttp` | `==3.10.11` | `3.14.1` | `webapp/server.py`, news scrapers | ⚠️ Version Drift |
| `aiosqlite` | `==0.20.0` | `0.22.1` | `db/database.py`, `db/users.py` | ⚠️ Version Drift |
| `mplfinance` | `==0.12.10b0`| `0.12.10b0`| `utils/chart_generator.py` | ✅ Exact Match |
| `matplotlib` | `==3.8.4` | `3.11.1` | `utils/chart_generator.py` | ⚠️ Major Version Drift (3.8 -> 3.11) |
| `pillow` | `==10.3.0` | `12.3.0` | `utils/chart_generator.py` (chart imaging) | ⚠️ Major Version Drift (10.x -> 12.x) |
| `python-dateutil` | `==2.9.0.post0`| `2.9.0.post0`| Date parsing across modules | ✅ Exact Match |

---

## 3. Critical Undeclared Dependencies

The following packages are actively imported and required by the bot's core services, yet are completely missing from `requirements.txt`:

| Package | Installed Version | Imported In | Impact if Missing |
|---|---|---|---|
| `fastapi` | `0.139.2` | `webapp/server.py` | 🔴 **CRITICAL**: MT5 HTTP Bridge server cannot start |
| `uvicorn` | `0.51.0` | `webapp/server.py`, `main.py` | 🔴 **CRITICAL**: ASGI web server fails to launch |
| `pydantic` | `2.13.4` | `webapp/server.py`, data models | 🔴 **CRITICAL**: Web request serialization fails |
| `numpy` | `2.4.4` | `strategies/ict_smc.py`, `scripts/intraday_gold_engine.py` | 🔴 **CRITICAL**: ICT indicators & backtest engine crash |
| `reportlab` | `5.0.0` | Audit report generators (Phase 2 & Phase 3 PDFs) | 🟡 **MEDIUM**: PDF generation utilities crash |
| `pymupdf` | `1.28.2` | Document extraction / inspection tools | 🟡 **MEDIUM**: PDF parsing utilities fail |
| `websockets` | `16.1.1` | Real-time socket telemetries | 🟡 **MEDIUM**: Streaming data channels fail |
| `paramiko` | `5.0.0` | `scratch/deploy_*.py`, VPS remote sync | 🟢 **LOW**: Automated VPS deployment scripts fail |
| `yfinance` | `1.5.1` | Market data fallback fetcher | 🟢 **LOW**: Auxiliary market data fetchers fail |

---

## 4. Complete Installed Package Inventory (`pip list`)

```text
Package                   Version
------------------------- -----------
aiofiles                  25.1.0
aiogram                   3.30.0
aiohappyeyeballs          2.7.1
aiohttp                   3.14.1
aiohttp-fast-zlib         0.3.0
aiosignal                 1.4.0
aiosqlite                 0.22.1
altgraph                  0.17.5
annotated-doc             0.0.4
annotated-types           0.7.0
anyio                     4.14.2
attrs                     26.1.0
bcrypt                    5.0.0
beautifulsoup4            4.15.0
ccxt                      4.5.67
certifi                   2026.6.17
cffi                      2.0.0
charset-normalizer        3.4.7
click                     8.4.2
coincurve                 21.0.0
colorama                  0.4.6
contourpy                 1.3.3
cryptography              49.0.0
curl_cffi                 0.15.0
customtkinter             6.0.0
cycler                    0.12.1
darkdetect                0.8.0
fastapi                   0.139.2
fonttools                 4.63.0
frozenlist                1.8.0
h11                       0.16.0
httpcore                  1.0.9
httpx                     0.28.1
idna                      3.18
invoke                    3.0.3
iri2016                   1.11.1
kiwisolver                1.5.0
magic-filter              1.0.12
markdown-it-py            4.2.0
matplotlib                3.11.1
mdurl                     0.1.2
meson                     1.10.2
mplfinance                0.12.10b0
multidict                 6.7.1
multitasking              0.0.13
ninja                     1.13.0
numpy                     2.4.4
orjson                    3.11.9
packaging                 26.0
pandas                    3.0.2
paramiko                  5.0.0
peewee                    4.2.6
pefile                    2024.8.26
pillow                    12.3.0
ping3                     5.1.5
pip                       26.0.1
platformdirs              4.10.1
propcache                 0.5.2
protobuf                  7.35.1
psutil                    7.2.2
pycparser                 3.0
pydantic                  2.13.4
pydantic_core             2.46.4
Pygments                  2.20.0
pyinstaller               6.21.0
pyinstaller-hooks-contrib 2026.6
pymupdf                   1.28.2
PyNaCl                    1.6.2
pyparsing                 3.3.2
python-dateutil           2.9.0.post0
python-dotenv             1.2.2
python-telegram-bot       22.8
pytz                      2026.2
pywin32-ctypes            0.2.3
reportlab                 5.0.0
requests                  2.34.2
rich                      15.0.0
setuptools                82.0.1
six                       1.17.0
soupsieve                 2.9
starlette                 1.3.1
ta                        0.11.0
typing_extensions         4.16.0
typing-inspection         0.4.2
tzdata                    2026.1
urllib3                   2.7.0
uvicorn                   0.51.0
websockets                16.1.1
winloop                   0.6.3
xarray                    2026.2.0
yarl                      1.24.2
yfinance                  1.5.1
zlib-ng                   1.0.0
```

---

## 5. Reproducibility Assessment

- **Reproducibility Status:** `HIGHLY COMPROMISED FROM requirements.txt ALONE`
- **Root Cause:** Attempting to `pip install -r requirements.txt` on a clean machine will result in an unusable installation: `fastapi`, `uvicorn`, `pydantic`, and `numpy` are omitted, causing immediate import failure on startup.
- **Remediation Target:** In Phase 1, generate a comprehensive `requirements.txt` and an exact `requirements.lock` reflecting the proven Python 3.12 stack.
