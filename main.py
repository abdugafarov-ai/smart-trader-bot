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
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(protect_content=True))
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

        # Загрузка сохранённых настроек лота, риска и автопилота из БД
        try:
            from trading.execution_bridge import bridge_manager
            await bridge_manager.load_settings_from_db()
        except Exception as e:
            logging.error("Failed to load bridge settings on startup: %s", e)

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

        # Регистрация меню команд и описания в Telegram со строгой изоляцией ролей
        try:
            from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

            # Публичные команды для обычных клиентов (БЕЗ админских пультов!)
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

            # Персональные команды администратора (со всеми пультами управления)
            if config.ADMIN_ID:
                admin_commands = [
                    BotCommand(command="start", description="🏛 Главное меню"),
                    BotCommand(command="terminal", description="🖥 Пульт MetaTrader 5"),
                    BotCommand(command="crm", description="👥 Управление клиентами (CRM)"),
                    BotCommand(command="account", description="💼 Баланс и позиции MT5"),
                    BotCommand(command="broadcast", description="📢 Рассылка клиентам"),
                    BotCommand(command="autotrade", description="⚙️ Автопилот советника"),
                    BotCommand(command="lot", description="🔹 Задать лот: /lot 0.01"),
                    BotCommand(command="risk", description="⚖️ Задать риск: /risk 1.0"),
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
                "Автономный торговый комплекс: SMC/ICT анализ 16 Forex пар и авто-торговля в MetaTrader 5 со строгим риск-менеджментом."
            )

            await bot.set_my_description(
                "Автономный торговый комплекс Smart Trader с интеграцией в MetaTrader 5:\n\n"
                "• 16 торговых инструментов (Forex мажоры и кроссы, Золото исключено)\n"
                "• Институциональная стратегия Smart Money / ICT (BOS, OB, FVG, OTE)\n"
                "• Авто-выставление лимитных ордеров со Stop Loss и Take Profit (R:R 1:2.5 – 1:4.0)\n"
                "• Режим Pure Swing: сделки дышат без копеечных выбиваний по безубытку\n"
                "• Режимы счёта: «Микро-депозит» (лот 0.01 от $12) и «Институционал»\n"
                "• Онлайн-пульт MT5: баланс, эквити, открытые позиции и кнопка «Паника»."
            )

            logging.info("Bot commands and descriptions registered successfully in Telegram.")
        except Exception as e:
            logging.error("Failed to set bot commands/descriptions: %s", e)

        # Фоновый воркер удержания (напоминания об окончании подписки за 3 дня и 1 день)
        async def run_subscription_retention_worker():
            from db.users import get_reminder_candidates, mark_reminder_sent, check_and_expire_subscriptions
            from bot.keyboards import guest_welcome_keyboard
            while True:
                try:
                    # 1. Проверяем и автоматически переводим истекшие подписки в статус 'expired'
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
                            logging.info("Sent subscription expiration notice to user %d", exp_uid)
                        except Exception as e_exp:
                            logging.warning("Failed to notify user %d about expiration: %s", exp_uid, e_exp)

                    # 2. Напоминания за 3 дня и 1 день
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
                        else:  # 1d
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
                            logging.info("Subscription reminder (%s) sent to user %d", rem_type, uid)
                        except Exception as e:
                            logging.warning("Failed to send subscription reminder to %d: %s", uid, e)
                except Exception as err:
                    logging.error("Subscription retention worker error: %s", err)

                # Проверяем каждый час
                await asyncio.sleep(3600)

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
                
        for coro in [scanner.start(), tracker.start(), reporter.start(), run_subscription_retention_worker()]:
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
