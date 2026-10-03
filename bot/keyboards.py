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


def admin_users_crm_keyboard(users: list[dict], tab: str = "all", page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура списка клиентов для CRM-панели администратора с вкладками и пагинацией."""
    builder = InlineKeyboardBuilder()
    now = datetime.now(timezone.utc)

    # 1. Вкладки (Tabs)
    tab_all_text = "• 🟢 Все •" if tab == "all" else "🟢 Все"
    tab_act_text = "• 💎 Активные •" if tab == "active" else "💎 Активные"
    tab_rev_text = "• 🔴 Отключ •" if tab == "revoked" else "🔴 Отключ"
    tab_pnd_text = "• ⏳ Заявки •" if tab == "pending" else "⏳ Заявки"

    builder.row(
        InlineKeyboardButton(text=tab_all_text, callback_data="crm:tab:all"),
        InlineKeyboardButton(text=tab_act_text, callback_data="crm:tab:active"),
        InlineKeyboardButton(text=tab_rev_text, callback_data="crm:tab:revoked"),
        InlineKeyboardButton(text=tab_pnd_text, callback_data="crm:tab:pending"),
    )

    # 2. Список пользователей
    for u in users:
        uid = u.get("telegram_id")
        if uid == config.ADMIN_ID:
            continue

        first_name = u.get("first_name") or "Пользователь"
        username = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
        tariff = u.get("tariff") or "PRO"
        status = u.get("status") or "pending"
        expires_at = u.get("expires_at")
        is_lifetime = bool(u.get("is_lifetime", 0))

        if is_lifetime:
            icon = "♾️ VIP"
        elif status == "approved":
            is_expired = False
            if expires_at:
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt < now:
                        is_expired = True
                except Exception:
                    pass
            icon = "⚠️ Истёк" if is_expired else "🟢"
        elif status == "revoked":
            icon = "🔴 Откл"
        elif status == "pending":
            icon = "⏳ Заявка"
        elif status == "rejected":
            icon = "❌ Отклон"
        else:
            icon = "⚪"

        btn_text = f"{icon} {first_name} ({username}) | {tariff}"
        builder.row(InlineKeyboardButton(text=btn_text, callback_data=f"crm:user:{uid}"))

    # 3. Пагинация
    if total_pages > 1:
        prev_page = max(1, page - 1)
        next_page = min(total_pages, page + 1)
        pag_buttons = []
        if page > 1:
            pag_buttons.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"crm:page:{tab}:{prev_page}"))
        pag_buttons.append(InlineKeyboardButton(text=f"Стр. {page}/{total_pages}", callback_data="crm:page_noop"))
        if page < total_pages:
            pag_buttons.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"crm:page:{tab}:{next_page}"))
        builder.row(*pag_buttons)

    # 4. Рассылка и навигация
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка всем клиентам (/broadcast)", callback_data="menu:broadcast")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить список", callback_data=f"crm:tab:{tab}"),
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def admin_user_card_keyboard(target_id: int, is_active: bool, is_lifetime: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура карточки клиента в CRM администратора."""
    builder = InlineKeyboardBuilder()

    # Строка 1: Отключение / Восстановление
    if is_active:
        builder.row(
            InlineKeyboardButton(text="🔴 Отключить доступ (Kick)", callback_data=f"crm:revoke:{target_id}")
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🟢 Восстановить доступ", callback_data=f"crm:restore:{target_id}")
        )

    # Строка 2: Продление тарифов
    builder.row(
        InlineKeyboardButton(text="🎁 +3 дня (Триал)", callback_data=f"crm:extend_days:{target_id}:3"),
        InlineKeyboardButton(text="💎 +30 дней ($50)", callback_data=f"crm:extend_days:{target_id}:30")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 +90 дней ($140)", callback_data=f"crm:extend_days:{target_id}:90"),
        InlineKeyboardButton(text="👑 +365 дней ($500)", callback_data=f"crm:extend_days:{target_id}:365")
    )
    # Строка 3: Бессрочный доступ (VIP для братьев и друзей)
    lifetime_text = "✨ Снять VIP статус" if is_lifetime else "♾️ Бессрочно (Братья / VIP)"
    builder.row(
        InlineKeyboardButton(text=lifetime_text, callback_data=f"crm:lifetime:{target_id}")
    )
    # Строка 4: Удаление и возврат
    builder.row(
        InlineKeyboardButton(text="🗑️ Удалить клиента", callback_data=f"crm:delete:{target_id}"),
        InlineKeyboardButton(text="◀️ Назад в CRM", callback_data="menu:crm")
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


def autotrade_keyboard(
    enabled: bool = True,
    mode: str = "micro",
    current_lot: float = 0.01,
    current_risk: float = 1.0,
    lot_mode: str = "fixed"
) -> InlineKeyboardMarkup:
    """Клавиатура быстрого управления авто-торговлей MT5 с интерактивными галочками выбора."""
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

    # Пресеты фиксированного лота с галочками
    is_fixed = (lot_mode == "fixed")
    
    t_001 = "✅ 0.01" if (is_fixed and abs(current_lot - 0.01) < 0.001) else "🔹 Лот 0.01"
    t_002 = "✅ 0.02" if (is_fixed and abs(current_lot - 0.02) < 0.001) else "🔹 Лот 0.02"
    t_005 = "✅ 0.05" if (is_fixed and abs(current_lot - 0.05) < 0.001) else "🔹 Лот 0.05"

    builder.row(
        InlineKeyboardButton(text=t_001, callback_data="autotrade:lot:0.01"),
        InlineKeyboardButton(text=t_002, callback_data="autotrade:lot:0.02"),
        InlineKeyboardButton(text=t_005, callback_data="autotrade:lot:0.05")
    )

    # Кнопка ручного ввода своего лота
    is_custom = is_fixed and (abs(current_lot - 0.01) >= 0.001 and abs(current_lot - 0.02) >= 0.001 and abs(current_lot - 0.05) >= 0.001)
    custom_btn_text = f"✅ ✍️ Свой лот: {current_lot:.2f}" if is_custom else "✍️ Задать свой лот"
    builder.row(
        InlineKeyboardButton(text=custom_btn_text, callback_data="autotrade:custom_lot")
    )

    # Пресеты риска с галочками
    is_risk = (lot_mode == "risk")
    r_05 = "✅ ⚖️ 0.5%" if (is_risk and abs(current_risk - 0.5) < 0.01) else "⚖️ Риск 0.5%"
    r_10 = "✅ ⚖️ 1.0%" if (is_risk and abs(current_risk - 1.0) < 0.01) else "⚖️ Риск 1.0%"
    r_20 = "✅ ⚖️ 2.0%" if (is_risk and abs(current_risk - 2.0) < 0.01) else "⚖️ Риск 2.0%"

    builder.row(
        InlineKeyboardButton(text=r_05, callback_data="autotrade:risk:0.5"),
        InlineKeyboardButton(text=r_10, callback_data="autotrade:risk:1.0"),
        InlineKeyboardButton(text=r_20, callback_data="autotrade:risk:2.0")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu")
    )
    return builder.as_markup()


def cancel_custom_lot_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура отмены ручного ввода лота."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="autotrade:cancel_custom_lot")
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
    """Клавиатура для администратора: гибкое одобрение/отклонение заявки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎁 Одобрить Триал (3 дня)", callback_data=f"admin_approve_trial:{user_id}"),
        InlineKeyboardButton(text="✅ Одобрить 30 дней ($50)", callback_data=f"admin_approve_30:{user_id}")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Одобрить 90 дней ($140)", callback_data=f"admin_approve_90:{user_id}"),
        InlineKeyboardButton(text="👑 Одобрить 365 дней ($500)", callback_data=f"admin_approve_365:{user_id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отклонить заявку", callback_data=f"admin_reject:{user_id}")
    )
    return builder.as_markup()


def guest_welcome_keyboard(admin_username: str = "") -> InlineKeyboardMarkup:
    """Клавиатура приветствия для гостей и новых пользователей на экране /start."""
    builder = InlineKeyboardBuilder()
    username = admin_username or config.ADMIN_USERNAME
    admin_url = f"https://t.me/{username}" if username else f"tg://user?id={config.ADMIN_ID}"

    builder.row(
        InlineKeyboardButton(text="🎁 Бесплатный Тест-драйв (3 дня)", callback_data="req:type:trial")
    )
    builder.row(
        InlineKeyboardButton(text="📝 Подать заявку на доступ (/request)", callback_data="req:start")
    )
    builder.row(
        InlineKeyboardButton(text="💎 Выбрать платный тариф", callback_data="req:plans")
    )
    builder.row(
        InlineKeyboardButton(text="💬 Написать Администратору", url=admin_url)
    )
    return builder.as_markup()


def request_options_keyboard(admin_username: str = "") -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа при подаче заявки через /request."""
    builder = InlineKeyboardBuilder()
    username = admin_username or config.ADMIN_USERNAME
    admin_url = f"https://t.me/{username}" if username else f"tg://user?id={config.ADMIN_ID}"

    builder.row(
        InlineKeyboardButton(text="🎁 Бесплатный Тест-драйв (3 дня)", callback_data="req:type:trial")
    )
    builder.row(
        InlineKeyboardButton(text="💎 Тариф 1 Месяц — $50", callback_data="req:type:1m")
    )
    builder.row(
        InlineKeyboardButton(text="🚀 Тариф 3 Месяца — $140 (скидка $10)", callback_data="req:type:3m")
    )
    builder.row(
        InlineKeyboardButton(text="👑 Тариф 1 Год — $500 (скидка $100)", callback_data="req:type:1y")
    )
    builder.row(
        InlineKeyboardButton(text="💬 Задать вопрос админу", url=admin_url)
    )
    return builder.as_markup()


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура отмены режима рассылки."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отменить рассылку", callback_data="broadcast:cancel")
    )
    return builder.as_markup()

