"""
Smart Trader Bot — Keyboards.
Стиль: 🏛 «Wall Street / Bloomberg Terminal».
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import config


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню терминала."""
    builder = InlineKeyboardBuilder()

    # 1. Центральный пульт MT5
    builder.row(
        InlineKeyboardButton(text="🖥 Мой Терминал MT5", callback_data="menu:terminal")
    )
    # 2. Результаты и история
    builder.row(
        InlineKeyboardButton(text="📊 Статистика & Win-Rate", callback_data="menu:stats"),
        InlineKeyboardButton(text="📜 Журнал Сделок", callback_data="menu:history")
    )
    # 3. Рыночные условия
    builder.row(
        InlineKeyboardButton(text="⏰ Торговые Сессии", callback_data="menu:sessions"),
        InlineKeyboardButton(text="📰 Макро Календарь", callback_data="menu:news")
    )
    # 4. Управление и справочник
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки Автопилота", callback_data="menu:autotrade"),
        InlineKeyboardButton(text="📖 Справочник", callback_data="menu:help")
    )
    return builder.as_markup()


def terminal_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура пульта управления MetaTrader 5."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data="terminal:refresh"),
        InlineKeyboardButton(text="📸 Скриншот MT5", callback_data="terminal:screenshot"),
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


def symbols_keyboard() -> InlineKeyboardMarkup:
    """Выбор категории активов."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💎 Валютные Мажоры (7)", callback_data="cat:majors"),
        InlineKeyboardButton(text="💱 Валютные Кроссы (9)", callback_data="cat:crosses"),
    )
    builder.row(
        InlineKeyboardButton(text="🥇 Золото Спот (XAUUSD)", callback_data="sym:XAUUSD")
    )
    builder.row(InlineKeyboardButton(text="◀️ В Главное Меню", callback_data="menu"))
    return builder.as_markup()


def category_pairs_keyboard(category: str) -> InlineKeyboardMarkup:
    """Список пар внутри категории."""
    builder = InlineKeyboardBuilder()
    
    pairs = []
    if category == 'majors':
        pairs = config.PAIRS_MAJORS
    elif category == 'crosses':
        pairs = config.PAIRS_CROSSES
    elif category == 'commodities':
        pairs = config.PAIRS_COMMODITIES
        
    for sym in pairs:
        builder.add(InlineKeyboardButton(text=f"📊 {sym}", callback_data=f"sym:{sym}"))
        
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="◀️ Назад к категориям", callback_data="menu:analyze"))
    return builder.as_markup()




def back_keyboard(callback: str = 'menu') -> InlineKeyboardMarkup:
    """Универсальная кнопка возврата."""
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ В Терминал", callback_data=callback)
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
    builder.button(text="✅ Одобрить доступ", callback_data=f"admin_approve:{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject:{user_id}")
    builder.adjust(2)
    return builder.as_markup()
