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
    builder.row(
        InlineKeyboardButton(text="📊 Анализ Актива", callback_data="menu:analyze"),
        InlineKeyboardButton(text="📈 Индикаторы", callback_data="menu:indicators")
    )
    builder.row(
        InlineKeyboardButton(text="🧠 Модель ICT / SMC", callback_data="menu:strategy"),
        InlineKeyboardButton(text="⏰ Торговые Сессии", callback_data="menu:sessions")
    )
    builder.row(
        InlineKeyboardButton(text="📡 Радар Сигналов", callback_data="menu:signals"),
        InlineKeyboardButton(text="📰 Макро Календарь", callback_data="menu:news")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Win-Rate Статы", callback_data="menu:stats"),
        InlineKeyboardButton(text="📜 Журнал Сделок", callback_data="menu:history")
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Параметры", callback_data="settings"),
        InlineKeyboardButton(text="📖 Справочник", callback_data="menu:help")
    )
    return builder.as_markup()


def signal_inline_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """Интерактивная клавиатура под карточкой сигнала."""
    builder = InlineKeyboardBuilder()
    tv_symbol = f"FX:{symbol}" if symbol != "XAUUSD" else "TVC:GOLD"
    tv_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
    
    builder.row(
        InlineKeyboardButton(text="📈 График TradingView", url=tv_url),
        InlineKeyboardButton(text="🔍 Глубокий разбор", callback_data=f"sym:{symbol}")
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


def timeframes_keyboard() -> InlineKeyboardMarkup:
    """Выбор таймфрейма."""
    builder = InlineKeyboardBuilder()
    tfs = config.AVAILABLE_TIMEFRAMES
    for tf in tfs:
        builder.add(InlineKeyboardButton(text=f"⏱ {tf}", callback_data=f"tf:{tf}"))
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return builder.as_markup()


def strategies_keyboard() -> InlineKeyboardMarkup:
    """Выбор модели анализа."""
    builder = InlineKeyboardBuilder()
    strats = [
        ("🧠 ICT / SMC (Институциональная)", "ict"),
        ("📦 Supply & Demand (Зоны)", "sd"),
        ("📈 Wyckoff (Фазы рынка)", "wyckoff"),
        ("🔓 Breakout + Retest", "breakout"),
        ("⚡ Scalping Momentum", "scalping"),
        ("📊 Volume & VWAP", "volume"),
    ]
    for text, val in strats:
        builder.add(InlineKeyboardButton(text=text, callback_data=f"strat:{val}"))
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔍 Все модели сразу", callback_data="strat:all"))
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
    return builder.as_markup()


def settings_keyboard() -> InlineKeyboardMarkup:
    """Настройки терминала."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💱 Инструменты (17)", callback_data="settings:pairs"),
        InlineKeyboardButton(text="⏱ Таймфрейм по умолч.", callback_data="settings:tf")
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Уведомления ВКЛ/ВЫКЛ", callback_data="settings:notif")
    )
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu"))
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


def admin_approve_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для администратора: одобрить/отклонить заявку."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить доступ", callback_data=f"admin_approve:{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject:{user_id}")
    builder.adjust(2)
    return builder.as_markup()
