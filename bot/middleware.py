"""
Smart Trader Bot — Middleware для контроля доступа.
Проверяет, одобрен ли пользователь, прежде чем обработать команду.
"""

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, Update
from typing import Callable, Awaitable, Any

import config
from db.users import is_user_approved, get_user_status


class AccessControlMiddleware(BaseMiddleware):
    """Middleware: пропускает только одобренных пользователей и админа."""

    # Команды, доступные всем (даже неодобренным)
    PUBLIC_COMMANDS = {"/start", "/request"}

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
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            # Пропускаем callback-и для заявок (approve/reject)
            if event.data and event.data.startswith(("admin_approve:", "admin_reject:")):
                return await handler(event, data)

        if user_id is None:
            return await handler(event, data)

        # Админ всегда пропускается
        if user_id == config.ADMIN_ID:
            return await handler(event, data)

        # Проверяем одобрение
        if await is_user_approved(user_id):
            return await handler(event, data)

        # Не одобрен — отправляем сообщение
        status = await get_user_status(user_id)
        if isinstance(event, Message):
            if status == "pending":
                await event.answer(
                    "⏳ Ваша заявка на рассмотрении.\n"
                    "Администратор скоро её проверит.\n\n"
                    "Ожидайте уведомления! 🔔"
                )
            elif status == "rejected":
                await event.answer(
                    "❌ Ваша заявка была отклонена.\n"
                    "Свяжитесь с администратором для уточнения."
                )
            else:
                await event.answer(
                    "🔒 Доступ к боту ограничен.\n\n"
                    "Отправьте /request чтобы подать заявку на доступ."
                )
        elif isinstance(event, CallbackQuery):
            await event.answer("🔒 Доступ ограничен. Отправьте /request", show_alert=True)

        return  # Не вызываем handler
