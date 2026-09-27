"""
Smart Trader Bot — MT5 Screenshot Utility.
Делает мгновенный снимок экрана терминала MetaTrader 5 (DISPLAY=:11.0) напрямую в память.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

import config

SCREENSHOT_CMD = (
    f"XAUTHORITY={config.WINE_XAUTHORITY} DISPLAY={config.WINE_DISPLAY} "
    f"xwd -display {config.WINE_DISPLAY} -root -silent | xwdtopnm 2>/dev/null | pnmtopng 2>/dev/null"
)


async def capture_mt5_screenshot() -> Optional[bytes]:
    """
    Захватывает снимок экрана терминала MetaTrader 5 в формате PNG.
    Возвращает байты изображения или None в случае ошибки.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            SCREENSHOT_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if stdout and len(stdout) > 2000:
            return stdout
        else:
            err_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Empty output"
            logger.warning("MT5 screenshot capture returned small/empty data: %s", err_msg)
            return None
    except Exception as e:
        logger.error("Failed to capture MT5 screenshot: %s", e)
        return None
