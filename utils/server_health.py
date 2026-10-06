"""
Smart Trader Bot — Server Health & Maintenance Manager.
Модуль мониторинга состояния VPS (CPU, RAM, Disk, Uptime, DB, MT5)
и безопасной очистки кэша, временных файлов и мусора.
"""

import os
import sys
import gc
import time
import shutil
import logging
import platform
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple
from datetime import datetime, timezone, timedelta

import config

logger = logging.getLogger(__name__)

# Фиксируем время старта модуля/бота
BOT_START_TIME = time.time()


def _format_bytes(bytes_count: int) -> str:
    """Форматирует байты в удобные B, KB, MB, GB."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def _format_seconds(seconds: float) -> str:
    """Форматирует секунды в дни, часы, минуты."""
    sec = int(seconds)
    days = sec // 86400
    hours = (sec % 86400) // 3600
    minutes = (sec % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0 or days > 0:
        parts.append(f"{hours} ч.")
    parts.append(f"{minutes} мин.")
    return " ".join(parts)


def get_system_uptime() -> float:
    """Возвращает аптайм сервера в секундах."""
    # На Linux читаем /proc/uptime
    try:
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime", "r", encoding="utf-8") as f:
                return float(f.readline().split()[0])
    except Exception:
        pass
    # Фолбэк: время работы процесса
    return time.time() - BOT_START_TIME


def get_ram_info() -> Dict[str, Any]:
    """Считывает данные по оперативной памяти (Linux /proc/meminfo или psutil/shutil)."""
    total = 0
    available = 0
    used = 0
    percent = 0.0

    if os.path.exists("/proc/meminfo"):
        try:
            mem = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val_str = parts[1].strip().split()[0]
                        mem[key] = int(val_str) * 1024  # kB -> Bytes

            total = mem.get("MemTotal", 0)
            available = mem.get("MemAvailable", mem.get("MemFree", 0))
            used = max(0, total - available)
            if total > 0:
                percent = round((used / total) * 100.0, 1)
        except Exception as e:
            logger.debug("Error reading /proc/meminfo: %s", e)

    # Процесс бота (RSS)
    proc_rss = 0
    try:
        if os.path.exists("/proc/self/status"):
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        proc_rss = int(line.split()[1]) * 1024
                        break
    except Exception:
        pass

    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": percent,
        "process_rss": proc_rss,
    }


def get_cpu_info() -> Dict[str, Any]:
    """Считывает нагрузку процессора (Load Average и ядра)."""
    cores = os.cpu_count() or 1
    load1, load5, load15 = (0.0, 0.0, 0.0)
    try:
        if hasattr(os, "getloadavg"):
            load1, load5, load15 = os.getloadavg()
        elif os.path.exists("/proc/loadavg"):
            with open("/proc/loadavg", "r", encoding="utf-8") as f:
                parts = f.readline().split()
                load1, load5, load15 = float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        pass

    return {
        "cores": cores,
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
    }


def get_disk_info() -> Dict[str, Any]:
    """Считывает использование диска."""
    try:
        usage = shutil.disk_usage("/")
    except Exception:
        usage = shutil.disk_usage(".")

    pct = round((usage.used / usage.total) * 100.0, 1) if usage.total > 0 else 0.0
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": pct,
    }


async def get_database_info() -> Dict[str, Any]:
    """Возвращает размер и статистику SQLite базы данных."""
    from db.database import DB_PATH
    import aiosqlite

    db_path = Path(DB_PATH)
    db_size = db_path.stat().st_size if db_path.exists() else 0

    wal_path = Path(f"{DB_PATH}-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0

    signals_count = 0
    users_count = 0

    try:
        async with aiosqlite.connect(str(DB_PATH), timeout=10.0) as db:
            async with db.execute("SELECT COUNT(*) FROM signals") as cur:
                row = await cur.fetchone()
                if row:
                    signals_count = row[0]
            try:
                async with db.execute("SELECT COUNT(*) FROM users") as cur:
                    row = await cur.fetchone()
                    if row:
                        users_count = row[0]
            except Exception:
                pass
    except Exception as e:
        logger.debug("Database stats query error: %s", e)

    return {
        "db_size": db_size,
        "wal_size": wal_size,
        "signals_count": signals_count,
        "users_count": users_count,
    }


async def get_server_health_dashboard() -> str:
    """Генерирует полную карточку состояния сервера в стиле Bloomberg Terminal."""
    # 1. Uptime
    sys_uptime_sec = get_system_uptime()
    bot_uptime_sec = time.time() - BOT_START_TIME

    # 2. CPU
    cpu = get_cpu_info()

    # 3. RAM
    ram = get_ram_info()
    ram_total_str = _format_bytes(ram["total"]) if ram["total"] > 0 else "—"
    ram_used_str = _format_bytes(ram["used"]) if ram["total"] > 0 else "—"
    ram_free_str = _format_bytes(ram["available"]) if ram["total"] > 0 else "—"
    proc_rss_str = _format_bytes(ram["process_rss"]) if ram["process_rss"] > 0 else "—"

    # 4. Disk
    disk = get_disk_info()
    disk_total_str = _format_bytes(disk["total"])
    disk_used_str = _format_bytes(disk["used"])
    disk_free_str = _format_bytes(disk["free"])

    # Индикатор заполненности диска
    if disk["percent"] > 90:
        disk_badge = f"🔴 <b>{disk['percent']}% (КРИТИЧЕСКИ ЗАПОЛНЕН!)</b>"
    elif disk["percent"] > 75:
        disk_badge = f"🟡 <b>{disk['percent']}% (Внимание)</b>"
    else:
        disk_badge = f"🟢 <b>{disk['percent']}% (В норме)</b>"

    # 5. База данных
    db_info = await get_database_info()
    db_size_str = _format_bytes(db_info["db_size"])
    wal_size_str = _format_bytes(db_info["wal_size"])

    # 6. MT5 Bridge
    from trading.execution_bridge import bridge_manager
    is_online, ping = bridge_manager.is_mt5_online()
    if is_online:
        bridge_badge = f"🟢 <b>В СЕТИ</b> (задержка {ping}с)"
        t = bridge_manager.mt5_telemetry
        broker_str = t.get("broker", "—")
        bal_val = float(t.get("balance", 0.0))
        acc_str = t.get("account", "—")
        mt5_info = f"   • Брокер: <code>{broker_str}</code> (#{acc_str}) | Баланс: <code>${bal_val:,.2f}</code>"
    else:
        bridge_badge = "🔴 <b>ОФФЛАЙН</b> (нет пинга)"
        mt5_info = "   • Терминал MetaTrader 5 на сервере оффлайн"

    # 7. Логи
    log_size = 0
    base_dir = Path(__file__).resolve().parent.parent
    for log_p in [base_dir / "bot.log", base_dir / "bot.log.1.gz"]:
        if log_p.exists():
            log_size += log_p.stat().st_size
    log_size_str = _format_bytes(log_size)

    lines = [
        "🎛️ <b>МОНИТОРИНГ СЕРВЕРА & СИСТЕМЫ (VPS HEALTH)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🖥 <b>Процессор (CPU):</b> <code>{cpu['cores']} ядра</code> | Load: <code>{cpu['load1']}, {cpu['load5']}, {cpu['load15']}</code>",
        f"🧠 <b>Память (RAM):</b> <code>{ram_used_str} / {ram_total_str} ({ram['percent']}%)</code>",
        f"   └ <i>Процесс бота:</i> <code>{proc_rss_str} RSS</code> | Свободно: <code>{ram_free_str}</code>",
        "",
        f"💾 <b>Диск (SSD /dev/sda1):</b> {disk_badge}",
        f"   └ Занято: <code>{disk_used_str} / {disk_total_str}</code> | Свободно: <b>{disk_free_str}</b>",
        "",
        f"⏱ <b>Uptime VPS:</b> <code>{_format_seconds(sys_uptime_sec)}</code>",
        f"🤖 <b>Uptime Бота:</b> <code>{_format_seconds(bot_uptime_sec)}</code> (PID: <code>{os.getpid()}</code>)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🗄 <b>База данных (SQLite):</b> <code>{db_size_str}</code> (WAL: <code>{wal_size_str}</code>)",
        f"   └ Сигналов: <code>{db_info['signals_count']}</code> | Клиентов CRM: <code>{db_info['users_count']}</code>",
        f"📝 <b>Журнал логов бота:</b> <code>{log_size_str}</code>",
        "",
        f"📡 <b>Связь с MetaTrader 5:</b> {bridge_badge}",
        mt5_info,
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 <i>Нажмите «🧹 Очистить кэш», чтобы удалить временный мусор, оптимизировать базу данных и освободить память.</i>"
    ]
    return "\n".join(lines)


async def execute_cache_cleanup() -> Dict[str, Any]:
    """
    Выполняет комплексную и безопасную очистку сервера:
    1. Удаляет временные сгенерированные графики (*.png) и остатки тестов.
    2. Очищает каталоги __pycache__ и *.pyc файлы.
    3. Выполняет WAL Checkpoint и VACUUM для SQLite базы данных (сжимает файл).
    4. Очищает кэш пакетов Linux (apt-get clean) и срезает старые системные логи (journalctl).
    5. Принудительно запускает сборщик мусора Python (gc.collect()).
    """
    base_dir = Path(__file__).resolve().parent.parent
    files_removed = 0
    bytes_freed = 0
    actions_taken = []

    # 1. Удаление тестовых и временных PNG графиков в корне проекта
    for f in base_dir.glob("test_*.png"):
        try:
            sz = f.stat().st_size
            f.unlink()
            files_removed += 1
            bytes_freed += sz
        except Exception:
            pass

    # 2. Очистка временных файлов графиков в data/ и временных директориях
    for folder in [base_dir / "data" / "charts", Path("/tmp")]:
        if folder.exists():
            for p in folder.glob("chart_*.png"):
                try:
                    if time.time() - p.stat().st_mtime > 3600:
                        sz = p.stat().st_size
                        p.unlink()
                        files_removed += 1
                        bytes_freed += sz
                except Exception:
                    pass

    # 3. Очистка __pycache__ и .pyc
    for pyc_dir in base_dir.rglob("__pycache__"):
        try:
            for pyc_file in pyc_dir.glob("*.pyc"):
                sz = pyc_file.stat().st_size
                pyc_file.unlink()
                files_removed += 1
                bytes_freed += sz
            pyc_dir.rmdir()
        except Exception:
            pass

    # 4. Удаление старых пустых 0-байтовых файлов баз
    for empty_f in [base_dir / "signals.db", base_dir / "smart_trader.db", base_dir / "data" / "smart_trader.db"]:
        try:
            if empty_f.exists() and empty_f.stat().st_size == 0:
                empty_f.unlink()
                files_removed += 1
        except Exception:
            pass

    actions_taken.append(f"Удалено {files_removed} временных файлов")

    # 5. Оптимизация базы данных SQLite (WAL Checkpoint + VACUUM)
    db_vacuumed = False
    try:
        from db.database import DB_PATH
        import sqlite3
        import asyncio

        def _vacuum_db():
            con = sqlite3.connect(str(DB_PATH), timeout=30.0, isolation_level=None)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("VACUUM")
            finally:
                con.close()

        await asyncio.to_thread(_vacuum_db)
        db_vacuumed = True
        actions_taken.append("Оптимизирована база SQLite (WAL сжат, VACUUM выполнен)")
    except Exception as db_err:
        logger.warning("DB vacuum during cleanup error: %s", db_err)

    # 6. Очистка системного кэша Linux (если бот на сервере с правами root)
    if platform.system().lower() == "linux":
        try:
            subprocess.run(["apt-get", "clean"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            subprocess.run(["journalctl", "--vacuum-size=15M"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            actions_taken.append("Очищен системный кэш APT и сжаты системные журналы")
        except Exception:
            pass

    # 7. Принудительный Garbage Collection Python
    unreachable = gc.collect()
    actions_taken.append(f"Освобождено объектов памяти Python: {unreachable}")

    # Замеряем свободное место после очистки
    disk_after = get_disk_info()
    free_str = _format_bytes(disk_after["free"])

    return {
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "bytes_freed_str": _format_bytes(bytes_freed),
        "db_vacuumed": db_vacuumed,
        "disk_free_str": free_str,
        "actions": actions_taken,
    }
