"""
Smart Trader Bot — Keyboards.
Стиль: 🏛 «Wall Street / Bloomberg Terminal».
Разделение ролей: Администратор (полный контроль MT5 + CRM) и Клиент (сигналы, статистика, подписка).
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timezone
import config


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню Администратора (Полный доступ к MT5, CRM и настройкам)."""
    builder = InlineKeyboardBuilder()

    # 1. MT5 Терминал и Автопилот
    builder.row(
        InlineKeyboardButton(text="🖥 Мой Терминал MT5", callback_data="menu:terminal"),
        InlineKeyboardButton(text="⚙️ Автопилот", callback_data="menu:autotrade")
    )
    # 2. CRM Управление клиентами и подписками
    builder.row(
        InlineKeyboardButton(text="👥 Управление Клиентами (CRM)", callback_data="menu:crm")
    )
    # 3. Результаты и история
    builder.row(
        InlineKeyboardButton(text="📊 Статистика & Win-Rate", callback_data="menu:stats"),
        InlineKeyboardButton(text="📜 Журнал Сделок", callback_data="menu:history")
    )
    # 4. Рыночные условия
    builder.row(
        InlineKeyboardButton(text="⏰ Торговые Сессии", callback_data="menu:sessions"),
        InlineKeyboardButton(text="📰 Макро Календарь", callback_data="menu:news")
    )
    # 5. Справочник
    builder.row(
        InlineKeyboardButton(text="📖 Справочник / Инфо", callback_data="menu:help")
    )
    return builder.as_markup()


def client_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню Клиента (Только сигналы, статистика, аналитика и подписка)."""
    builder = InlineKeyboardBuilder()

    # 1. Результаты и история
    builder.row(
        InlineKeyboardButton(text="📊 Статистика & Win-Rate", callback_data="menu:stats"),
        InlineKeyboardButton(text="📜 Журнал Сигналов", callback_data="menu:history")
    )
    # 2. Рыночные условия
    builder.row(
        InlineKeyboardButton(text="⏰ Торговые Сессии", callback_data="menu:sessions"),
        InlineKeyboardButton(text="📰 Макро Календарь", callback_data="menu:news")
    )
    # 3. Подписка клиента
    builder.row(
        InlineKeyboardButton(text="💎 Моя Подписка & Тариф", callback_data="menu:my_sub")
    )
    # 4. Обучение и поддержка
    builder.row(
        InlineKeyboardButton(text="🎓 Обучение (Гид)", callback_data="menu:help"),
        InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support")
    )
    return builder.as_markup()


def main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Диспетчер главного меню в зависимости от роли пользователя."""
    if is_admin:
        return admin_menu_keyboard()
    return client_menu_keyboard()


def client_subscription_keyboard(admin_username: str = "") -> InlineKeyboardMarkup:
    """Клавиатура раздела подписки для клиента."""
    builder = InlineKeyboardBuilder()
    username = admin_username or config.ADMIN_USERNAME
    admin_url = f"https://t.me/{username}" if username else f"tg://user?id={config.ADMIN_ID}"
    
    builder.row(
        InlineKeyboardButton(text="💳 Продлить тариф / Сменить план", url=admin_url)
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def client_support_keyboard(admin_username: str = "") -> InlineKeyboardMarkup:
    """Клавиатура раздела службы поддержки."""
    builder = InlineKeyboardBuilder()
    username = admin_username or config.ADMIN_USERNAME
    admin_url = f"https://t.me/{username}" if username else f"tg://user?id={config.ADMIN_ID}"
    
    builder.row(
        InlineKeyboardButton(text="💬 Написать Администратору", url=admin_url)
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def admin_users_crm_keyboard(users: list[dict]) -> InlineKeyboardMarkup:
    """Клавиатура списка клиентов для CRM-панели администратора."""
    builder = InlineKeyboardBuilder()
    now = datetime.now(timezone.utc)

    # Список пользователей
    for u in users:
        uid = u.get("telegram_id")
        if uid == config.ADMIN_ID:
            continue

        first_name = u.get("first_name") or "Пользователь"
        username = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
        tariff = u.get("tariff") or "PRO"
        status = u.get("status") or "pending"
        expires_at = u.get("expires_at")

        # Иконка статуса
        if status == "approved":
            is_expired = False
            if expires_at:
                try:
                    if datetime.fromisoformat(expires_at) < now:
                        is_expired = True
                except Exception:
                    pass
            icon = "⚠️ Истёк" if is_expired else "🟢"
        elif status == "revoked":
            icon = "🔴 Отключен"
        elif status == "pending":
            icon = "⏳ Заявка"
        elif status == "rejected":
            icon = "❌ Отклонён"
        else:
            icon = "⚪"

        btn_text = f"{icon} {first_name} ({username}) | {tariff}"
        builder.row(InlineKeyboardButton(text=btn_text, callback_data=f"crm:user:{uid}"))

    # Навигационные кнопки
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить список", callback_data="menu:crm"),
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def admin_user_card_keyboard(target_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура карточки клиента в CRM администратора."""
    builder = InlineKeyboardBuilder()

    if is_active:
        builder.row(
            InlineKeyboardButton(text="🔴 Отключить доступ (Kick)", callback_data=f"crm:revoke:{target_id}")
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🟢 Восстановить доступ", callback_data=f"crm:restore:{target_id}")
        )

    builder.row(
        InlineKeyboardButton(text="➕ Продлить 30 дней", callback_data=f"crm:extend:{target_id}"),
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"crm:delete:{target_id}")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад к списку CRM", callback_data="menu:crm")
    )
    return builder.as_markup()


def terminal_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура пульта управления MetaTrader 5."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="terminal:refresh"),
        InlineKeyboardButton(text="⚙️ Автопилот", callback_data="menu:autotrade")
    )
    builder.row(
        InlineKeyboardButton(text="🛑 ПАНИКА: ЗАКРЫТЬ ВСЁ", callback_data="terminal:panic_confirm")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def panic_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение экстренного закрытия всех сделок."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚠️ ДА, ЗАКРЫТЬ ВСЁ!", callback_data="terminal:panic_exec"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="menu:terminal")
    )
    return builder.as_markup()


def autotrade_keyboard(enabled: bool = True, mode: str = "micro") -> InlineKeyboardMarkup:
    """Клавиатура быстрого управления авто-торговлей MT5."""
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 ПРИОСТАНОВИТЬ АВТОПИЛОТ" if enabled else "🟢 ВКЛЮЧИТЬ АВТОПИЛОТ"
    toggle_cb = "autotrade:off" if enabled else "autotrade:on"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)
    )
    # Отдельные кнопки для выбора профиля (Radio-style)
    if mode == "micro":
        btn_micro = InlineKeyboardButton(text="✅ 🛡️ Режим «Микро-депозит»", callback_data="autotrade:mode_noop:micro")
        btn_prop  = InlineKeyboardButton(text="👑 Режим: Институционал", callback_data="autotrade:mode:prop")
    else:
        btn_micro = InlineKeyboardButton(text="🛡️ Режим «Микро-депозит»", callback_data="autotrade:mode:micro")
        btn_prop  = InlineKeyboardButton(text="✅ 👑 Режим: Институционал", callback_data="autotrade:mode_noop:prop")
    builder.row(btn_micro)
    builder.row(btn_prop)

    builder.row(
        InlineKeyboardButton(text="🔹 Лот 0.01", callback_data="autotrade:lot:0.01"),
        InlineKeyboardButton(text="🔹 Лот 0.02", callback_data="autotrade:lot:0.02"),
        InlineKeyboardButton(text="🔹 Лот 0.05", callback_data="autotrade:lot:0.05")
    )
    builder.row(
        InlineKeyboardButton(text="⚖️ Риск 0.5%", callback_data="autotrade:risk:0.5"),
        InlineKeyboardButton(text="⚖️ Риск 1.0%", callback_data="autotrade:risk:1.0"),
        InlineKeyboardButton(text="⚖️ Риск 2.0%", callback_data="autotrade:risk:2.0")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def signal_inline_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """Интерактивная клавиатура под карточкой сигнала."""
    builder = InlineKeyboardBuilder()
    tv_symbol = f"FX:{symbol}" if symbol != "XAUUSD" else "TVC:GOLD"
    tv_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
    
    builder.row(
        InlineKeyboardButton(text="📈 TradingView", url=tv_url),
        InlineKeyboardButton(text="🔍 Разбор", callback_data=f"sym:{symbol}")
    )
    return builder.as_markup()


def analysis_result_keyboard(symbol: str, can_execute: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура под ручным анализом пары, с кнопкой быстрой отправки в MT5."""
    builder = InlineKeyboardBuilder()
    if can_execute:
        builder.row(InlineKeyboardButton(text="⚡ Открыть в MT5 Авто-роботом", callback_data=f"exec_mt5:{symbol}"))
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"sym:{symbol}"),
        InlineKeyboardButton(text="◀️ В Терминал", callback_data="menu")
    )
    return builder.as_markup()


def back_keyboard(callback: str = 'menu') -> InlineKeyboardMarkup:
    """Универсальная кнопка возврата."""
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ В Меню", callback_data=callback)
    return builder.as_markup()


def guide_keyboard(step: int) -> InlineKeyboardMarkup:
    """Навигация по интерактивному гиду."""
    builder = InlineKeyboardBuilder()
    if step < 4:
        builder.row(InlineKeyboardButton(text="Далее ➡️", callback_data="guide:next"))
        builder.row(InlineKeyboardButton(text="Пропустить обучение", callback_data="guide:skip"))
    else:
        builder.row(InlineKeyboardButton(text="Войти в Терминал 🏛", callback_data="guide:skip"))
    return builder.as_markup()


def help_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура справочника с кнопкой запуска интерактивного гида."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎓 Пройти Обучение (Гид)", callback_data="guide:start")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def admin_approve_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для администратора: одобрить/отклонить заявку."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить доступ (30д)", callback_data=f"admin_approve:{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject:{user_id}")
    builder.adjust(2)
    return builder.as_markup()
