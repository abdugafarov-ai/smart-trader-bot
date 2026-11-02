import asyncio
import logging
import os
import sys
import argparse
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.methods import GetUpdates
from aiogram.utils.backoff import Backoff, BackoffConfig
from aiogram.dispatcher.dispatcher import DEFAULT_BACKOFF_CONFIG
from aiogram.exceptions import TelegramConflictError

import config
from bot.handlers import router
from bot.middleware import AccessControlMiddleware
from notifications.auto_signals import AutoSignalScanner
from notifications.weekly_report import WeeklyReporter
from db.signal_tracker import SignalTracker
from db.database import init_db
from db.users import init_users_table, auto_approve_admin

_background_tasks = set()
logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Smart Trader Bot & MT5 Execution Bridge")
    parser.add_argument("--no-polling", action="store_true", help="Disable Telegram getUpdates polling (run in broadcast-only / bridge mode)")
    parser.add_argument("--no-bridge", action="store_true", help="Disable embedded MT5 REST Bridge server")
    parser.add_argument("--headless", action="store_true", help="Run without Telegram interactive bot")
    return parser.parse_known_args()[0]


async def _install_safe_polling_listener(dp: Dispatcher, max_conflict_retries: int = 2):
    """
    Устанавливает отказоустойчивый слушатель getUpdates в aiogram.
    Если обнаружен конфликт с production-инстансом (например, на внешнем VPS),
    опрос мягко завершается после max_conflict_retries, чтобы не нарушать работу production-бота.
    При этом весь остальной функционал (MT5 Bridge, сканер, алерты) продолжает работать.
    """
    @classmethod
    async def _safe_listen_updates(
        cls,
        bot: Bot,
        polling_timeout: int = 20,
        backoff_config: BackoffConfig = DEFAULT_BACKOFF_CONFIG,
        allowed_updates: list[str] | None = None,
    ):
        backoff = Backoff(config=backoff_config)
        get_updates = GetUpdates(timeout=polling_timeout, allowed_updates=allowed_updates)
        kwargs = {}
        if bot.session.timeout:
            kwargs["request_timeout"] = int(bot.session.timeout + polling_timeout)
        failed = False
        consecutive_conflicts = 0

        while True:
            try:
                updates = await bot(get_updates, **kwargs)
                consecutive_conflicts = 0
            except TelegramConflictError as e:
                consecutive_conflicts += 1
                if consecutive_conflicts >= max_conflict_retries:
                    logger.warning(
                        "⚠️ Telegram Polling Conflict: Another bot instance is actively polling @%s on a remote server. "
                        "Local incoming command polling paused to avoid disrupting production. "
                        "AutoSignalScanner, MT5 Bridge REST server, and outgoing alerts remain 100%% active.",
                        getattr(bot, "_bot_username", "Centurion")
                    )
                    break
                logger.warning(
                    "Telegram polling conflict (%d/%d): %s. Backing off...",
                    consecutive_conflicts, max_conflict_retries, e
                )
                await backoff.asleep()
                continue
            except Exception as e:
                failed = True
                logger.error("Failed to fetch updates - %s: %s", type(e).__name__, e)
                logger.warning("Sleep for %f seconds and try again...", backoff.next_delay)
                await backoff.asleep()
                continue

            if failed:
                logger.info("Telegram connection established (bot id = %d)", bot.id)
                backoff.reset()
                failed = False

            for update in updates:
                yield update
                get_updates.offset = update.update_id + 1

    Dispatcher._listen_updates = _safe_listen_updates


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    args = parse_arguments()

    # 1. Проверка целостности и безопасности конфигурации при старте
    config.validate_config(raise_on_error=True)

    # 2. Проверка и верификация токена Telegram
    bot: Optional[Bot] = None
    has_token = bool(getattr(config, "BOT_TOKEN", "").strip())
    
    if not has_token or args.headless:
        logger.warning(
            "⚠️ %s. Running in Signals-Only Headless / MT5 Bridge mode.",
            "Headless mode requested via CLI" if args.headless else "No BOT_TOKEN configured in .env"
        )
    else:
        try:
            bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(protect_content=True))
            me = await asyncio.wait_for(bot.get_me(), timeout=10.0)
            bot._bot_username = me.username
            logger.info("✅ Telegram Bot connected: @%s (%s, ID=%d)", me.username, me.full_name, me.id)
        except Exception as e:
            logger.warning("⚠️ Telegram Bot connectivity verification failed: %s. Falling back to Headless mode.", e)
            if bot and hasattr(bot, "session") and bot.session:
                try:
                    await bot.session.close()
                except Exception:
                    pass
            bot = None

    dp = Dispatcher() if bot else None
    if dp:
        router.message.middleware(AccessControlMiddleware())
        router.callback_query.middleware(AccessControlMiddleware())
        dp.include_router(router)

    scanner = AutoSignalScanner(
        bot=bot,
        interval_minutes=config.SCAN_INTERVAL_MINUTES,
        user_ids=config.NOTIFY_USER_IDS,
        symbols=config.ALL_PAIRS,
        timeframe=config.DEFAULT_TIMEFRAME
    )
    
    tracker = SignalTracker(bot=bot, check_interval_minutes=5)
    reporter = WeeklyReporter(bot=bot)
    bridge_runner = None
    stop_event = asyncio.Event()

    # 3. Инициализация базы данных и настроек моста
    await init_db()
    await init_users_table()
    if config.ADMIN_ID:
        await auto_approve_admin(config.ADMIN_ID)

    try:
        from trading.execution_bridge import bridge_manager
        await bridge_manager.load_settings_from_db()
    except Exception as e:
        logger.error("Failed to load bridge settings on startup: %s", e)

    logger.info(
        "Database initialized. Profile: %s | Admin ID: %s",
        config.ACTIVE_TRADING_PROFILE, str(config.ADMIN_ID)
    )

    # 4. Запуск MT5 Local Bridge HTTP сервера
    if not args.no_bridge:
        bridge_already_running = False
        try:
            import urllib.request
            health_url = f"http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}/health"
            with urllib.request.urlopen(health_url, timeout=1.0) as resp:
                if resp.status == 200:
                    bridge_already_running = True
                    logger.info(
                        "ℹ️ MT5 Bridge server is already running and active on http://%s:%d. Reusing existing bridge service.",
                        config.WEBAPP_HOST, config.WEBAPP_PORT
                    )
        except Exception:
            bridge_already_running = False

        if not bridge_already_running:
            try:
                from webapp.server import start_webapp_server
                bridge_runner = await start_webapp_server(config.WEBAPP_HOST, config.WEBAPP_PORT, bot=bot)
                logger.info("MT5 Bridge server started on %s:%d", config.WEBAPP_HOST, config.WEBAPP_PORT)
            except Exception as e:
                logger.error("Failed to start MT5 Bridge server: %s", e)

    # 5. Уведомление админа о запуске
    if bot and config.ADMIN_ID:
        try:
            await bot.send_message(
                config.ADMIN_ID,
                "🤖 Бот запущен и готов к работе!\n"
                f"📊 Отслеживаю {len(config.ALL_PAIRS)} торговых пар\n"
                f"⏱ Сканирование каждые {config.SCAN_INTERVAL_MINUTES} мин\n"
                f"🔔 Уведомления: только ⭐⭐⭐⭐ и ⭐⭐⭐⭐⭐\n"
                f"📰 Предупреждения о новостях: включены\n"
                f"🔒 Контроль доступа: включён\n"
                f"📊 Еженедельный отчёт: суббота 10:00\n"
                f"🔌 MT5 Bridge: http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}\n\n"
                "Отправь /start для начала работы."
            )
        except Exception as e:
            logger.error("Error sending startup msg: %s", e)

    # 6. Регистрация меню команд и описания в Telegram
    if bot:
        try:
            from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

            client_commands = [
                BotCommand(command="start", description="🏛 Главное меню"),
                BotCommand(command="request", description="💎 Тарифы и подписка"),
                BotCommand(command="stats", description="📊 Статистика сигналов"),
                BotCommand(command="history", description="📜 Журнал сделок"),
                BotCommand(command="sessions", description="⏰ Торговые сессии"),
                BotCommand(command="news", description="📰 Календарь новостей"),
                BotCommand(command="help", description="📖 Справка и поддержка"),
            ]
            await bot.set_my_commands(client_commands, scope=BotCommandScopeDefault())

            if config.ADMIN_ID:
                admin_commands = [
                    BotCommand(command="start", description="🏛 Главное меню"),
                    BotCommand(command="terminal", description="🖥 Пульт MetaTrader 5"),
                    BotCommand(command="crm", description="👥 Управление клиентами (CRM)"),
                    BotCommand(command="account", description="💼 Баланс и позиции MT5"),
                    BotCommand(command="broadcast", description="📢 Рассылка клиентам"),
                    BotCommand(command="autotrade", description="⚙️ Автопилот советника"),
                    BotCommand(command="lot", description="🔹 Задать лот: /lot 0.01"),
                    BotCommand(command="risk", description="⚖️ Задать риск: /risk 0.25"),
                    BotCommand(command="stats", description="📊 Статистика сигналов"),
                    BotCommand(command="history", description="📜 Журнал сделок"),
                    BotCommand(command="sessions", description="⏰ Торговые сессии"),
                    BotCommand(command="news", description="📰 Календарь новостей"),
                    BotCommand(command="server", description="🎛️ Состояние VPS"),
                    BotCommand(command="clean", description="🧹 Очистить кэш / мусор"),
                    BotCommand(command="reset_drawdown", description="🛡️ Сбросить просадку"),
                    BotCommand(command="help", description="📖 Справочник"),
                ]
                await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=config.ADMIN_ID))

            await bot.set_my_short_description(
                "Институциональный терминал XAUUSD Intraday (H1->M15->M5, риск 0.25%, мин. лот 0.01, R:R >= 1:2.0)."
            )
            await bot.set_my_description(
                "Автономный торговый комплекс Smart Trader (FundingPips $5K · XAUUSD Intraday):\n\n"
                "• Строгий Intraday фокус на XAUUSD (H1 контекст -> M15 POI -> M5 триггер)\n"
                "• Риск на сделку 0.25% ($12.50) | Мин. лот 0.01 | Строгий контроль стоп-лосса\n"
                "• Закрытие позиций до банковского ролловера (EOD Flat 20:40 UTC)\n"
                "• Авто-Б/У при 1.0R | Анти-тильт и контроль дневной просадки\n"
                "• Пульт MT5: баланс, эквити, открытые сделки и кнопка «Паника»."
            )
            logger.info("Bot commands and descriptions registered successfully in Telegram.")
        except Exception as e:
            logger.error("Failed to set bot commands/descriptions: %s", e)

    # 7. Фоновый воркер удержания подписчиков
    async def run_subscription_retention_worker():
        if not bot:
            return
        from db.users import get_reminder_candidates, mark_reminder_sent, check_and_expire_subscriptions
        from bot.keyboards import guest_welcome_keyboard
        while True:
            try:
                expired_users = await check_and_expire_subscriptions()
                for exp_u in expired_users:
                    exp_uid = exp_u.get("telegram_id")
                    exp_tariff = exp_u.get("tariff") or "PRO"
                    msg_exp = (
                        "🔒 <b>СРОК ДЕЙСТВИЯ ПОДПИСКИ ИСТЁК</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"Уважаемый трейдер! Действие вашего тарифа <b>{exp_tariff}</b> завершилось.\n\n"
                        "Доступ к институциональным сигналам Smart Trader приостановлен.\n\n"
                        "Чтобы продолжить торговлю и не пропускать рыночные сетапы, выберите тариф для продления ниже 👇"
                    )
                    try:
                        await bot.send_message(exp_uid, msg_exp, reply_markup=guest_welcome_keyboard(), parse_mode="HTML")
                        logger.info("Sent subscription expiration notice to user %d", exp_uid)
                    except Exception as e_exp:
                        logger.warning("Failed to notify user %d about expiration: %s", exp_uid, e_exp)

                candidates = await get_reminder_candidates()
                for u in candidates:
                    uid = u.get("telegram_id")
                    rem_type = u.get("reminder_type")
                    days_left = u.get("days_left", 1)
                    tariff = u.get("tariff") or "PRO"

                    if rem_type == "3d":
                        msg = (
                            "⏳ <b>НАПОМИНАНИЕ О ПОДПИСКЕ SMART TRADER</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Уважаемый трейдер! До окончания действия вашего тарифа <b>{tariff}</b> осталось <b>{days_left} дня</b>.\n\n"
                            "Чтобы не потерять доступ к сигналам и аналитике, рекомендуем заблаговременно продлить подписку.\n\n"
                            "💳 Для продления перейдите в раздел <b>«Моя Подписка»</b> или напишите администратору."
                        )
                    else:
                        msg = (
                            "⚠️ <b>ВНИМАНИЕ: ПОСЛЕДНИЙ ДЕНЬ ПОДПИСКИ</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Срок действия вашего тарифа <b>{tariff}</b> истекает менее чем через <b>24 часа</b>!\n\n"
                            "После истечения доступ к сигналам будет автоматически приостановлен.\n\n"
                            "💳 Нажмите <b>«Моя Подписка»</b> в главном меню для продления."
                        )

                    try:
                        await bot.send_message(uid, msg, parse_mode="HTML")
                        await mark_reminder_sent(uid, rem_type)
                        logger.info("Subscription reminder (%s) sent to user %d", rem_type, uid)
                    except Exception as e:
                        logger.warning("Failed to send subscription reminder to %d: %s", uid, e)
            except Exception as err:
                logger.error("Subscription retention worker error: %s", err)

            await asyncio.sleep(3600)

    # 8. Запуск фоновых аналитических служб
    global _background_tasks
    def _on_task_done(task: asyncio.Task):
        _background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical("🔴 Фоновая задача упала: %s", exc, exc_info=exc)

    coros = [scanner.start(), tracker.start(), reporter.start()]
    if bot:
        coros.append(run_subscription_retention_worker())

    for coro in coros:
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_on_task_done)

    # 9. Управление циклом опроса Telegram
    env_polling = os.getenv("TELEGRAM_POLLING", "true").lower() in ("1", "true", "yes")
    polling_allowed = (
        bot is not None
        and not args.no_polling
        and not args.headless
        and getattr(config, "TELEGRAM_POLLING_ENABLED", True)
        and env_polling
    )

    try:
        if polling_allowed:
            await _install_safe_polling_listener(dp, max_conflict_retries=2)
            logger.info("Starting Telegram event polling for @%s...", getattr(bot, "_bot_username", "Centurion"))
            await dp.start_polling(bot, handle_signals=False)
            logger.info("Telegram polling loop returned. Keeping analytical engine and MT5 Bridge active.")
        else:
            logger.info("🚀 Smart Trader Bot & MT5 Bridge running in Headless/Background mode.")

        # Ожидаем сигнала остановки (если polling завершился из-за конфликта или был отключен)
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutdown requested.")
    finally:
        logger.info("Shutting down Smart Trader services...")
        await scanner.stop()
        await tracker.stop()
        await reporter.stop()
        if bridge_runner:
            try:
                await bridge_runner.cleanup()
                logger.info("MT5 Bridge server cleaned up.")
            except Exception as e:
                logger.error("Error cleaning up Bridge server: %s", e)
        if bot and hasattr(bot, "session") and bot.session:
            try:
                await bot.session.close()
            except Exception:
                pass
        logger.info("All services stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
