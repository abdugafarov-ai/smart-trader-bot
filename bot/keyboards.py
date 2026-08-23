from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import config

def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Анализ пары", callback_data="menu:analyze"),
        InlineKeyboardButton(text="📈 Индикаторы", callback_data="menu:indicators")
    )
    builder.row(
        InlineKeyboardButton(text="🧠 Стратегия", callback_data="menu:strategy"),
        InlineKeyboardButton(text="⏰ Сессии", callback_data="menu:sessions")
    )
    builder.row(
        InlineKeyboardButton(text="📡 Сигналы всех пар", callback_data="menu:signals"),
        InlineKeyboardButton(text="📰 Новости", callback_data="menu:news")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
        InlineKeyboardButton(text="📜 История", callback_data="menu:history")
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help")
    )
    return builder.as_markup()

def symbols_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Мажоры", callback_data="cat:majors"),
        InlineKeyboardButton(text="Кроссы", callback_data="cat:crosses"),
        InlineKeyboardButton(text="Металлы", callback_data="cat:commodities")
    )
    builder.row(
        InlineKeyboardButton(text="Индексы", callback_data="cat:indices"),
        InlineKeyboardButton(text="Крипто", callback_data="cat:crypto")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))
    return builder.as_markup()

def category_pairs_keyboard(category: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    pairs = []
    if category == 'majors':
        pairs = config.PAIRS_MAJORS
    elif category == 'crosses':
        pairs = config.PAIRS_CROSSES
    elif category == 'commodities':
        pairs = config.PAIRS_COMMODITIES
    elif category == 'indices':
        pairs = config.PAIRS_INDICES
    elif category == 'crypto':
        pairs = config.PAIRS_CRYPTO
        
    for sym in pairs:
        builder.add(InlineKeyboardButton(text=sym, callback_data=f"sym:{sym}"))
        
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu:analyze"))
    return builder.as_markup()

def timeframes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tfs = config.AVAILABLE_TIMEFRAMES
    for tf in tfs:
        builder.add(InlineKeyboardButton(text=tf, callback_data=f"tf:{tf}"))
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))
    return builder.as_markup()

def strategies_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    strats = [
        ("🧠 ICT/SMC", "ict"),
        ("📦 Supply & Demand", "sd"),
        ("📈 Wyckoff", "wyckoff"),
        ("🔓 Breakout + Retest", "breakout"),
        ("⚡ Scalping", "scalping"),
        ("📊 Volume Analysis", "volume"),
    ]
    for text, val in strats:
        builder.add(InlineKeyboardButton(text=text, callback_data=f"strat:{val}"))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔍 Все стратегии", callback_data="strat:all"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))
    return builder.as_markup()

def settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💱 Торговые пары", callback_data="settings:pairs"),
        InlineKeyboardButton(text="⏱ Таймфрейм", callback_data="settings:tf")
    )
    builder.row(
        InlineKeyboardButton(text="🔔 Уведомления ВКЛ/ВЫКЛ", callback_data="settings:notif")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu"))
    return builder.as_markup()

def back_keyboard(callback: str = 'menu') -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data=callback)
    return builder.as_markup()

def guide_keyboard(step: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if step < 4:
        builder.row(InlineKeyboardButton(text="Далее ➡️", callback_data="guide:next"))
        builder.row(InlineKeyboardButton(text="Пропустить", callback_data="guide:skip"))
    else:
        builder.row(InlineKeyboardButton(text="Начать торговлю 🚀", callback_data="guide:skip"))
    return builder.as_markup()

def admin_approve_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа: одобрить/отклонить заявку."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"admin_approve:{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject:{user_id}")
    builder.adjust(2)
    return builder.as_markup()

