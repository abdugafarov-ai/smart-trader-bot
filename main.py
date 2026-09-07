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

_background_tasks = set()

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

        # Регистрация меню команд в Telegram
        try:
            from aiogram.types import BotCommand
            await bot.set_my_commands([
                BotCommand(command="start", description="Главное меню терминала"),
                BotCommand(command="signals", description="Радар актуальных сигналов (4-5★)"),
                BotCommand(command="analyze", description="Анализ пары: /analyze EURUSD"),
                BotCommand(command="autotrade", description="Управление авто-торговлей MT5"),
                BotCommand(command="lot", description="Задать рабочий лот: /lot 0.01"),
                BotCommand(command="risk", description="Задать риск: /risk 1.0"),
                BotCommand(command="stats", description="Статистика винрейта и PnL"),
                BotCommand(command="history", description="Журнал последних сделок"),
                BotCommand(command="equity", description="График кривой капитала PnL"),
                BotCommand(command="sessions", description="Торговые сессии и Kill Zones"),
                BotCommand(command="news", description="Экономический календарь новостей"),
                BotCommand(command="webapp", description="Интерактивный терминал TradingView"),
                BotCommand(command="help", description="Справочник по всем командам"),
            ])
            logging.info("Bot commands registered successfully in Telegram Menu.")
        except Exception as e:
            logging.error("Failed to set bot commands: %s", e)

        # Фоновые задачи
        global _background_tasks
        for coro in [scanner.start(), tracker.start(), reporter.start()]:
            task = asyncio.create_task(coro)
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        
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
