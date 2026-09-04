import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

import config
from bot.handlers import router
from bot.middleware import AccessControlMiddleware
from notifications.auto_signals import AutoSignalScanner
from notifications.weekly_report import WeeklyReporter
from db.signal_tracker import SignalTracker
from db.database import init_db
from db.users import init_users_table, auto_approve_admin

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties())
    dp = Dispatcher()

    # Middleware: контроль доступа (только одобренные пользователи)
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
    webapp_runner = None
    
    async def on_startup():
        nonlocal webapp_runner
        # Инициализация базы данных
        await init_db()
        await init_users_table()
        await auto_approve_admin(config.ADMIN_ID)
        logging.info("Database initialized. Admin ID: %d", config.ADMIN_ID)
        logging.info("Bot started. Scanning %d pairs every %d min.",
                      len(config.ALL_PAIRS), config.SCAN_INTERVAL_MINUTES)

        # Запуск WebApp & Bridge HTTP сервера
        try:
            from webapp.server import start_webapp_server
            webapp_runner = await start_webapp_server(config.WEBAPP_HOST, config.WEBAPP_PORT)
            logging.info("WebApp & Bridge server started on %s:%d", config.WEBAPP_HOST, config.WEBAPP_PORT)
        except Exception as e:
            logging.error("Failed to start WebApp server: %s", e)

        # Уведомление админа о запуске
        if config.ADMIN_ID:
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
                    f"🌐 WebApp & Bridge: http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}\n\n"
                    "Отправь /start для начала работы."
                )
            except Exception as e:
                logging.error(f"Error sending startup msg: {e}")

        # Фоновые задачи
        asyncio.create_task(scanner.start())
        asyncio.create_task(tracker.start())
        asyncio.create_task(reporter.start())
        
    async def on_shutdown():
        logging.info("Bot shutting down.")
        await scanner.stop()
        await tracker.stop()
        await reporter.stop()
        if webapp_runner:
            try:
                await webapp_runner.cleanup()
                logging.info("WebApp server cleaned up.")
            except Exception as e:
                logging.error(f"Error cleaning up WebApp server: {e}")
        
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Critical error: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
