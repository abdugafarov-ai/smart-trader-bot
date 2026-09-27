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
    bridge_runner = None
    
    async def on_startup():
        nonlocal bridge_runner
        # Инициализация базы данных
        await init_db()
        await init_users_table()
        await auto_approve_admin(config.ADMIN_ID)
        logging.info("Database initialized. Admin ID: %d", config.ADMIN_ID)
        logging.info("Bot started. Scanning %d pairs every %d min.",
                      len(config.ALL_PAIRS), config.SCAN_INTERVAL_MINUTES)

        # Запуск MT5 Local Bridge HTTP сервера
        try:
            from webapp.server import start_webapp_server
            bridge_runner = await start_webapp_server(config.WEBAPP_HOST, config.WEBAPP_PORT, bot=bot)
            logging.info("MT5 Bridge server started on %s:%d", config.WEBAPP_HOST, config.WEBAPP_PORT)
        except Exception as e:
            logging.error("Failed to start MT5 Bridge server: %s", e)

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
                    f"🔌 MT5 Bridge: http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}\n\n"
                    "Отправь /start для начала работы."
                )
            except Exception as e:
                logging.error(f"Error sending startup msg: {e}")

        # Регистрация меню команд и описания в Telegram
        try:
            from aiogram.types import BotCommand
            await bot.set_my_commands([
                BotCommand(command="start", description="🏛 Главное меню терминала"),
                BotCommand(command="terminal", description="🖥 Пульт управления MetaTrader 5"),
                BotCommand(command="account", description="💼 Баланс и открытые позиции MT5"),
                BotCommand(command="stats", description="📊 Статистика & Win-Rate брокера"),
                BotCommand(command="history", description="📜 Журнал последних сделок"),
                BotCommand(command="autotrade", description="⚙️ Автопилот советника MT5"),
                BotCommand(command="lot", description="🔹 Задать лот: /lot 0.01"),
                BotCommand(command="risk", description="⚖️ Задать риск: /risk 1.0"),
                BotCommand(command="sessions", description="⏰ Расписание торговых сессий"),
                BotCommand(command="news", description="📰 Календарь важных новостей"),
                BotCommand(command="reset_stats", description="🧹 Сбросить статистику на 0"),
                BotCommand(command="help", description="📖 Справочник и документация"),
            ])

            await bot.set_my_short_description(
                "Автономный торговый комплекс: SMC/ICT анализ 17 пар и авто-торговля в MetaTrader 5 со строгим риск-менеджментом."
            )

            await bot.set_my_description(
                "Автономный торговый комплекс Smart Trader с интеграцией в MetaTrader 5:\n\n"
                "• 17 торговых инструментов (Forex мажоры, кроссы и Золото XAUUSD)\n"
                "• Институциональная стратегия Smart Money / ICT (BOS, OB, FVG, OTE)\n"
                "• Авто-выставление лимитных ордеров со Stop Loss и Take Profit (R:R 1:2.5 – 1:4.0)\n"
                "• Режим Pure Swing: сделки дышат без копеечных выбиваний по безубытку\n"
                "• Режимы счёта: «Микро-депозит» (лот 0.01 от $12) и «Институционал»\n"
                "• Онлайн-пульт MT5: баланс, эквити, открытые позиции и кнопка «Паника»."
            )

            logging.info("Bot commands and descriptions registered successfully in Telegram.")
        except Exception as e:
            logging.error("Failed to set bot commands/descriptions: %s", e)

        # Фоновые задачи
        global _background_tasks
        
        def _on_task_done(task: asyncio.Task):
            """Обработка падения фоновых задач."""
            _background_tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                logging.critical("🔴 Фоновая задача упала: %s", exc, exc_info=exc)
                
        for coro in [scanner.start(), tracker.start(), reporter.start()]:
            task = asyncio.create_task(coro)
            _background_tasks.add(task)
            task.add_done_callback(_on_task_done)
        
    async def on_shutdown():
        logging.info("Bot shutting down.")
        await scanner.stop()
        await tracker.stop()
        await reporter.stop()
        if bridge_runner:
            try:
                await bridge_runner.cleanup()
                logging.info("MT5 Bridge server cleaned up.")
            except Exception as e:
                logging.error(f"Error cleaning up Bridge server: {e}")
        
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
