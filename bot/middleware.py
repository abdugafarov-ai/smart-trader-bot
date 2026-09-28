"""
Smart Trader Bot — Middleware для контроля доступа и аналитики активности.
Проверяет, одобрен ли пользователь, активна ли подписка, и скрытно фиксирует активность.
"""

import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Awaitable, Any

import config
from db.users import is_user_approved, get_user_status, log_user_activity


class AccessControlMiddleware(BaseMiddleware):
    """Middleware: пропускает только одобренных пользователей с активным тарифом и админа."""

    # Команды, доступные всем (даже неодобренным)
    PUBLIC_COMMANDS = {"/start", "/request", "/help"}

    async def __call__(
        self,
        handler: Callable[[Any, dict], Awaitable[Any]],
        event: Any,
        data: dict,
    ) -> Any:
        # Определяем telegram_id
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            # Проверяем публичные команды
            if event.text and any(event.text.startswith(cmd) for cmd in self.PUBLIC_COMMANDS):
                if user_id:
                    asyncio.create_task(log_user_activity(user_id))
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            # Пропускаем callback-и для заявок, триалов и информации о подписке
            if event.data and (event.data.startswith("req:") or event.data in ("menu:support", "menu", "menu:my_sub")):
                if user_id:
                    asyncio.create_task(log_user_activity(user_id))
                return await handler(event, data)

            # Пропускаем callback-и админа для заявок (approve/reject)
            if event.data and event.data.startswith(("admin_approve", "admin_reject")):
                if user_id == config.ADMIN_ID:
                    return await handler(event, data)
                else:
                    await event.answer('❌ Только администратор!', show_alert=True)
                    return

        if user_id is None:
            return await handler(event, data)

        # Скрытно фиксируем активность пользователя в базе
        asyncio.create_task(log_user_activity(user_id))

        # Админ всегда пропускается
        if user_id == config.ADMIN_ID:
            return await handler(event, data)

        # Проверяем одобрение и срок подписки
        if await is_user_approved(user_id):
            return await handler(event, data)

        # Доступ не активен — проверяем детальный статус
        status = await get_user_status(user_id)

        if isinstance(event, Message):
            if status == "pending":
                await event.answer(
                    "⏳ <b>Ваша заявка на рассмотрении.</b>\n"
                    "Администратор скоро её проверит.\n\n"
                    "Ожидайте уведомления! 🔔",
                    parse_mode="HTML"
                )
            elif status == "revoked":
                await event.answer(
                    "🔒 <b>Доступ к боту приостановлен администратором.</b>\n\n"
                    "Действие тарифа завершено или доступ был отключен.\n"
                    "Для продления подписки обратитесь к администратору.",
                    parse_mode="HTML"
                )
            elif status == "expired":
                await event.answer(
                    "⏳ <b>Срок действия вашей подписки истёк.</b>\n\n"
                    "Для продления доступа обратитесь к администратору.",
                    parse_mode="HTML"
                )
            elif status == "rejected":
                await event.answer(
                    "❌ <b>Ваша заявка была отклонена.</b>\n"
                    "Свяжитесь с администратором для уточнения.",
                    parse_mode="HTML"
                )
            else:
                await event.answer(
                    "🔒 <b>Доступ к боту ограничен.</b>\n\n"
                    "Отправьте /request чтобы подать заявку на доступ.",
                    parse_mode="HTML"
                )
        elif isinstance(event, CallbackQuery):
            if status == "revoked":
                await event.answer("🔒 Доступ приостановлен администратором.", show_alert=True)
            elif status == "expired":
                await event.answer("⏳ Срок подписки истёк. Обратитесь к админу.", show_alert=True)
            elif status == "pending":
                await event.answer("⏳ Заявка на рассмотрении.", show_alert=True)
            else:
                await event.answer("🔒 Доступ ограничен. Отправьте /request", show_alert=True)

        return  # Не вызываем handler
