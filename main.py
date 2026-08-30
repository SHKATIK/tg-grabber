import asyncio
import datetime
import logging
import re
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto
)

# ══════════════════════════════════════════════════════════
#              КОНФИГУРАЦИЯ И АВТОРИЗАЦИЯ
# ══════════════════════════════════════════════════════════
API_ID = 26695279
API_HASH = "13ad7ad5407f42fa50bcce8012d59065"
SESSION_STRING = "1ApWapzMBu66oSs3Y6tA4IZDdXfS2kLUQrjwd1rFa0r0KWwM2_rS-p0d6Ax8Ap4yMYNN2lYG74EGMGo4FQc3uhxBRQeB6wyfd4jqNcwBsbzh4uisdVs-ALrpxYF60OTZvzh1JVJaLrBlXAykX8jGoVQctfYB7g5GsNJ-Mc4Icuarzl7jnyleqMxxNgMycjxOX3FmnfIQ_tlYfDV1jlLemfv725Eu7fZBGGXbh-5o-EOmH7t3WvmjCNUXMI_SkgCBtEHTUEh-yUlI6f8TKxwmwGi-AyEYGK3YOQmb76ud3_BbKBS6Z93iR6ZiwmbgBnuaAHS-2fbO3d2TVbdNTYo3yQpvjZNdRr04="
BOT_TOKEN = "8868698546:AAHUgnSwYlmgGlNvDg2l5_HzD0PDXBfPTXc"
DB_NAME = "grabber_pro.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

ALBUM_BUFFER = {}
ALBUM_LOCK = asyncio.Lock()

class SetupState(StatesGroup):
    waiting_for_donor = State()
    waiting_for_target = State()
    waiting_for_signature = State()
    waiting_for_slot = State()

# ══════════════════════════════════════════════════════════
#                  БАЗА ДАННЫХ (SQLite)
# ══════════════════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                target_chat TEXT DEFAULT '',
                target_title TEXT DEFAULT 'Не привязан',
                is_active INTEGER DEFAULT 1,
                mode TEXT DEFAULT 'schedule',
                clean_links INTEGER DEFAULT 1,
                clean_tags INTEGER DEFAULT 1,
                allow_photos INTEGER DEFAULT 1,
                allow_videos INTEGER DEFAULT 1,
                allow_audio INTEGER DEFAULT 1,
                allow_docs INTEGER DEFAULT 1,
                allow_voice INTEGER DEFAULT 1,
                custom_signature TEXT DEFAULT '',
                total_posts INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_time TEXT UNIQUE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS donors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER UNIQUE,
                title TEXT,
                username TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                donor_id INTEGER,
                post_id INTEGER,
                PRIMARY KEY (donor_id, post_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donor_id INTEGER,
                post_ids TEXT,
                text TEXT,
                media_type TEXT,
                scheduled_time TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO settings (id, target_chat, target_title, is_active, mode)
            VALUES (1, '', 'Не привязан', 1, 'schedule')
        """)
        # По умолчанию ставим 2 слота в день
        for default_slot in ["12:00", "19:00"]:
            await db.execute("INSERT OR IGNORE INTO schedule_slots (slot_time) VALUES (?)", (default_slot,))
            
        await db.commit()

async def get_settings():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM settings WHERE id = 1") as cursor:
            return await cursor.fetchone()

async def update_setting(column: str, value):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE settings SET {column} = ? WHERE id = 1", (value,))
        await db.commit()

async def get_donors():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM donors") as cursor:
            return await cursor.fetchall()

async def get_slots():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT slot_time FROM schedule_slots ORDER BY slot_time ASC") as c:
            rows = await c.fetchall()
            return [r[0] for r in rows]

async def get_queue_count():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'") as c:
            row = await c.fetchone()
            return row[0] if row else 0

# ══════════════════════════════════════════════════════════
#         РАСЧЕТ ВРЕМЕНИ ДЛЯ СЛЕДУЮЩЕГО ПОСТА
# ══════════════════════════════════════════════════════════
async def calculate_next_scheduled_time() -> str:
    slots = await get_slots()
    if not slots:
        slots = ["12:00", "19:00"]

    now = datetime.datetime.now()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT scheduled_time FROM queue WHERE status = 'pending' ORDER BY scheduled_time DESC LIMIT 1"
        ) as c:
            last_row = await c.fetchone()

    if last_row and last_row[0]:
        try:
            last_time = datetime.datetime.strptime(last_row[0], "%Y-%m-%d %H:%M")
            base_date = last_time.date()
        except Exception:
            last_time = now
            base_date = now.date()
    else:
        last_time = now
        base_date = now.date()

    for day_offset in range(0, 90):
        current_date = base_date + datetime.timedelta(days=day_offset)
        for s in sorted(slots):
            sh, sm = map(int, s.split(":"))
            slot_dt = datetime.datetime.combine(current_date, datetime.time(sh, sm))
            if slot_dt > last_time and slot_dt > (now + datetime.timedelta(minutes=1)):
                return slot_dt.strftime("%Y-%m-%d %H:%M")

    fallback = now + datetime.timedelta(hours=3)
    return fallback.strftime("%Y-%m-%d %H:%M")

# ══════════════════════════════════════════════════════════
#               ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════
def parse_target_input(raw: str):
    raw = raw.strip()
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    match = re.search(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)", raw)
    if match:
        return match.group(1)
    return raw.lstrip("@")

def process_text_content(text: str, settings: aiosqlite.Row) -> str:
    if not text:
        text = ""
    if settings["clean_links"]:
        text = re.sub(r'https?://\S+|t\.me/\S+', '', text)
    if settings["clean_tags"]:
        text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    
    text = text.strip()
    signature = settings["custom_signature"]
    if signature:
        if text:
            text = f"{text}\n\n{signature}"
        else:
            text = signature
    return text.strip()

# ══════════════════════════════════════════════════════════
#                 КЛАВИАТУРЫ И ИНТЕРФЕЙС
# ══════════════════════════════════════════════════════════
def kb_main_menu(s: aiosqlite.Row, q_count: int, slots_count: int):
    status_icon = "🟢 РАБОТАЕТ" if s["is_active"] else "⏸ НА ПАУЗЕ"
    toggle_btn = "⏸ Приостановить" if s["is_active"] else "▶️ Возобновить работу"
    mode_icon = "⏰ По расписанию" if s["mode"] == "schedule" else "⚡ Мгновенно"

    keyboard = [
        [InlineKeyboardButton(text=f"Статус системы: {status_icon}", callback_data="none")],
        [InlineKeyboardButton(text=toggle_btn, callback_data="toggle_system_status")],
        [InlineKeyboardButton(text=f"Режим: {mode_icon}", callback_data="toggle_posting_mode")],
        [
            InlineKeyboardButton(text=f"📅 Контент-план ({q_count})", callback_data="open_queue_page_0"),
            InlineKeyboardButton(text=f"⏰ Слоты ({slots_count}/день)", callback_data="menu_schedule_slots")
        ],
        [
            InlineKeyboardButton(text="📥 Источники (Доноры)", callback_data="menu_donors"),
            InlineKeyboardButton(text="📤 Канал публикации", callback_data="menu_target")
        ],
        [
            InlineKeyboardButton(text="🎛 Фильтры контента", callback_data="menu_media_filters"),
            InlineKeyboardButton(text="🧹 Очистка текста", callback_data="menu_text_filters")
        ],
        [
            InlineKeyboardButton(text="✍️ Моя подпись к постам", callback_data="menu_signature"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def kb_slots_presets(slots):
    slots_str = ", ".join(f"{s}" for s in slots) if slots else "Не задано"
    keyboard = [
        [InlineKeyboardButton(text="⚡ 2 поста в день (12:00, 19:00)", callback_data="preset_2_posts")],
        [InlineKeyboardButton(text="⚡ 3 поста в день (10:00, 15:00, 20:00)", callback_data="preset_3_posts")],
        [InlineKeyboardButton(text="⚡ 4 поста в день (10:00, 14:00, 18:00, 22:00)", callback_data="preset_4_posts")],
        [InlineKeyboardButton(text="➕ Добавить точное время", callback_data="add_custom_slot")],
        [InlineKeyboardButton(text="🗑 Очистить все слоты", callback_data="clear_all_slots")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def kb_queue_nav(current_idx: int, total_count: int, post_id: int):
    nav = []
    if current_idx > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"open_queue_page_{current_idx - 1}"))
    if current_idx < total_count - 1:
        nav.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"open_queue_page_{current_idx + 1}"))

    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data=f"q_pub_{post_id}_{current_idx}")])
    keyboard.append([InlineKeyboardButton(text="🗑 Удалить из очереди", callback_data=f"q_del_{post_id}_{current_idx}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def kb_donors_list(donors):
    buttons = []
    for d in donors:
        title = d["title"][:22] + "..." if len(d["title"]) > 25 else d["title"]
        buttons.append([InlineKeyboardButton(text=f"🗑 {title}", callback_data=f"remove_donor_{d['channel_id']}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить канал-донор", callback_data="add_donor_action")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_media_filters(s: aiosqlite.Row):
    def chk(val): return "✅" if val else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{chk(s['allow_photos'])} Фотографии", callback_data="toggle_media_allow_photos"),
            InlineKeyboardButton(text=f"{chk(s['allow_videos'])} Видео / Reels", callback_data="toggle_media_allow_videos")
        ],
        [
            InlineKeyboardButton(text=f"{chk(s['allow_audio'])} Музыка / Аудио", callback_data="toggle_media_allow_audio"),
            InlineKeyboardButton(text=f"{chk(s['allow_docs'])} Файлы / Документы", callback_data="toggle_media_allow_docs")
        ],
        [
            InlineKeyboardButton(text=f"{chk(s['allow_voice'])} Голосовые / Кружки", callback_data="toggle_media_allow_voice")
        ],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")]
    ])

def kb_text_filters(s: aiosqlite.Row):
    def chk(val): return "✅" if val else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Удалять ссылки: {chk(s['clean_links'])}", callback_data="toggle_txt_links")],
        [InlineKeyboardButton(text=f"Удалять теги @: {chk(s['clean_tags'])}", callback_data="toggle_txt_tags")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")]
    ])

def kb_signature_menu(has_sig: bool):
    buttons = [[InlineKeyboardButton(text="✏️ Изменить / Задать подпись", callback_data="edit_signature_action")]]
    if has_sig:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить подпись", callback_data="clear_signature_action")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_back_only():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="open_main_menu")]
    ])

# ══════════════════════════════════════════════════════════
#               ОБРАБОТЧИКИ МЕНЮ (AIOGRAM)
# ══════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start_handler(message: Message, state: FSMContext):
    await state.clear()
    s = await get_settings()
    q_count = await get_queue_count()
    slots = await get_slots()
    mode_str = "⏰ По расписанию" if s["mode"] == "schedule" else "⚡ Мгновенно"
    text = (
        "👑 <b>ГЛАВНОЕ МЕНЮ СИСТЕМЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Приёмник:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Режим работы:</b> <code>{mode_str}</code>\n"
        f"• <b>Контент-план (в очереди):</b> <code>{q_count}</code> постов\n"
        f"• <b>Слотов в день:</b> <code>{len(slots)}</code> слота\n"
        f"• <b>Всего опубликовано:</b> <code>{s['total_posts']}</code> постов\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите интересующий раздел настроек ниже:"
    )
    await message.answer(text, reply_markup=kb_main_menu(s, q_count, len(slots)), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "open_main_menu")
async def cb_back_to_main(query: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    q_count = await get_queue_count()
    slots = await get_slots()
    mode_str = "⏰ По расписанию" if s["mode"] == "schedule" else "⚡ Мгновенно"
    text = (
        "👑 <b>ГЛАВНОЕ МЕНЮ СИСТЕМЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Приёмник:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Режим работы:</b> <code>{mode_str}</code>\n"
        f"• <b>Контент-план (в очереди):</b> <code>{q_count}</code> постов\n"
        f"• <b>Слотов в день:</b> <code>{len(slots)}</code> слота\n"
        f"• <b>Всего опубликовано:</b> <code>{s['total_posts']}</code> постов\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите интересующий раздел настроек ниже:"
    )
    await query.message.edit_text(text, reply_markup=kb_main_menu(s, q_count, len(slots)), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "toggle_posting_mode")
async def cb_toggle_mode(query: CallbackQuery):
    s = await get_settings()
    new_mode = "instant" if s["mode"] == "schedule" else "schedule"
    await update_setting("mode", new_mode)
    s = await get_settings()
    q_count = await get_queue_count()
    slots = await get_slots()
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(s, q_count, len(slots)))
    await query.answer(f"Режим переключен на: {'По расписанию' if new_mode == 'schedule' else 'Мгновенно'}!")

@dp.callback_query(F.data == "toggle_system_status")
async def cb_toggle_status(query: CallbackQuery):
    s = await get_settings()
    new_val = 0 if s["is_active"] else 1
    await update_setting("is_active", new_val)
    s = await get_settings()
    q_count = await get_queue_count()
    slots = await get_slots()
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(s, q_count, len(slots)))
    await query.answer("Статус системы обновлен!")

# --- КОНТЕНТ-ПЛАН (ОЧЕРЕДЬ) ---
@dp.callback_query(F.data.startswith("open_queue_page_"))
async def cb_open_queue(query: CallbackQuery):
    page_idx = int(query.data.replace("open_queue_page_", ""))
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'") as c:
            total = (await c.fetchone())[0]

        if total == 0:
            await query.message.edit_text(
                "📅 <b>КОНТЕНТ-ПЛАН ПУСТ</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Сейчас в очереди нет отложенных постов. Когда доноры опубликуют новый контент, он появится здесь с расписанием по датам.</i>",
                reply_markup=kb_back_only(),
                parse_mode=ParseMode.HTML
            )
            await query.answer()
            return

        if page_idx >= total:
            page_idx = total - 1

        async with db.execute(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY scheduled_time ASC LIMIT 1 OFFSET ?",
            (page_idx,)
        ) as c:
            row = await c.fetchone()

    preview = row["text"][:180] + "..." if len(row["text"]) > 180 else (row["text"] or "<i>[Без текста]</i>")
    text = (
        f"📅 <b>КОНТЕНТ-ПЛАН: ПОСТ [{page_idx + 1}/{total}]</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Запланирован на:</b> <code>{row['scheduled_time']}</code>\n"
        f"• <b>Формат:</b> <code>{row['media_type'].upper()}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Текст поста:</b>\n<blockquote>{preview}</blockquote>"
    )
    
    await query.message.edit_text(
        text,
        reply_markup=kb_queue_nav(page_idx, total, row["id"]),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@dp.callback_query(F.data.startswith("q_pub_"))
async def cb_q_pub_now(query: CallbackQuery):
    parts = query.data.split("_")
    post_id = int(parts[2])
    page_idx = int(parts[3])
    
    await publish_queued_item(post_id)
    await query.answer("Пост опубликован!")
    
    # Обновляем вид
    query.data = f"open_queue_page_{page_idx}"
    await cb_open_queue(query)

@dp.callback_query(F.data.startswith("q_del_"))
async def cb_q_del(query: CallbackQuery):
    parts = query.data.split("_")
    post_id = int(parts[2])
    page_idx = int(parts[3])
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM queue WHERE id = ?", (post_id,))
        await db.commit()

    await query.answer("Пост удален из очереди!")
    query.data = f"open_queue_page_{max(0, page_idx - 1)}"
    await cb_open_queue(query)

# --- НАСТРОЙКА РАСПИСАНИЯ И СЛОТОВ ---
@dp.callback_query(F.data == "menu_schedule_slots")
async def cb_menu_slots(query: CallbackQuery):
    slots = await get_slots()
    slots_str = ", ".join(f"<code>{s}</code>" for s in slots) if slots else "<i>Слоты не заданы</i>"
    text = (
        "⏰ <b>НАСТРОЙКА РАСПИСАНИЯ ПУБЛИКАЦИЙ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Текущие часы выхода:</b> {slots_str}\n"
        f"• <b>Всего постов в день:</b> <code>{len(slots)}</code> шт.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите готовый пресет или добавьте свое точное время:"
    )
    await query.message.edit_text(text, reply_markup=kb_slots_presets(slots), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "preset_2_posts")
async def cb_preset_2(query: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots")
        for s in ["12:00", "19:00"]:
            await db.execute("INSERT INTO schedule_slots (slot_time) VALUES (?)", (s,))
        await db.commit()
    await query.answer("Установлено: 2 поста в день (12:00, 19:00)!")
    await cb_menu_slots(query)

@dp.callback_query(F.data == "preset_3_posts")
async def cb_preset_3(query: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots")
        for s in ["10:00", "15:00", "20:00"]:
            await db.execute("INSERT INTO schedule_slots (slot_time) VALUES (?)", (s,))
        await db.commit()
    await query.answer("Установлено: 3 поста в день (10:00, 15:00, 20:00)!")
    await cb_menu_slots(query)

@dp.callback_query(F.data == "preset_4_posts")
async def cb_preset_4(query: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots")
        for s in ["10:00", "14:00", "18:00", "22:00"]:
            await db.execute("INSERT INTO schedule_slots (slot_time) VALUES (?)", (s,))
        await db.commit()
    await query.answer("Установлено: 4 поста в день (10:00, 14:00, 18:00, 22:00)!")
    await cb_menu_slots(query)

@dp.callback_query(F.data == "clear_all_slots")
async def cb_clear_slots(query: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots")
        await db.commit()
    await query.answer("Все слоты очищены!")
    await cb_menu_slots(query)

@dp.callback_query(F.data == "add_custom_slot")
async def cb_add_slot_prompt(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_slot)
    await query.message.edit_text(
        "⏰ <b>ВВЕДИТЕ ВРЕМЯ ДЛЯ СЛОТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте время в формате <code>ЧЧ:ММ</code> (например, <code>16:30</code> или <code>09:00</code>):",
        reply_markup=kb_back_only(),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@dp.message(SetupState.waiting_for_slot)
async def process_slot_save(message: Message, state: FSMContext):
    val = message.text.strip()
    if re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", val):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO schedule_slots (slot_time) VALUES (?)", (val,))
            await db.commit()
        await state.clear()
        slots = await get_slots()
        await message.answer(f"✅ Слот <code>{val}</code> успешно добавлен!", reply_markup=kb_slots_presets(slots), parse_mode=ParseMode.HTML)
    else:
        await message.answer("❌ Неверный формат времени. Введите в виде <code>14:30</code>:", reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)

# --- ДОНОРЫ ---
@dp.callback_query(F.data == "menu_donors")
async def cb_menu_donors(query: CallbackQuery):
    donors = await get_donors()
    text = (
        "📥 <b>ИСТОЧНИКИ (КАНАЛЫ-ДОНОРЫ)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if not donors:
        text += "<i>Список пуст. Добавьте первый канал для отслеживания.</i>"
    else:
        text += f"Подключено доноров: <b>{len(donors)}</b>\n<i>Нажмите на канал для удаления:</i>"
    
    await query.message.edit_text(text, reply_markup=kb_donors_list(donors), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "add_donor_action")
async def cb_add_donor_prompt(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_donor)
    text = (
        "📥 <b>ДОБАВЛЕНИЕ НОВОГО ДОНОРА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте боту <b>один из вариантов:</b>\n"
        "1. Юзернейм: <code>@channel_name</code>\n"
        "2. Ссылку: <code>https://t.me/channel_name</code>\n"
        "3. Перешлите пост из этого канала сюда.\n\n"
        "<blockquote>⚠️ <b>Важно:</b> Ваш Telegram-аккаунт должен быть подписан на этот канал.</blockquote>"
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_donor)
async def process_donor_add(message: Message, state: FSMContext):
    if message.forward_from_chat:
        target_raw = message.forward_from_chat.id
    else:
        target_raw = parse_target_input(message.text)

    try:
        entity = await client.get_entity(target_raw)
        title = getattr(entity, 'title', str(target_raw))
        uname = getattr(entity, 'username', '')
        channel_id = entity.id

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO donors (channel_id, title, username) VALUES (?, ?, ?)",
                (channel_id, title, uname)
            )
            await db.commit()

        await state.clear()
        donors = await get_donors()
        await message.answer(
            f"✅ <b>Канал успешно подключен!</b>\n\n• <b>Название:</b> <code>{title}</code>\n• <b>ID:</b> <code>{channel_id}</code>",
            reply_markup=kb_donors_list(donors),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Не удалось найти канал:</b> <code>{e}</code>\n\n<i>Убедитесь, что ваш аккаунт подписан на этот канал.</i>",
            reply_markup=kb_back_only(),
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data.startswith("remove_donor_"))
async def cb_remove_donor(query: CallbackQuery):
    c_id = int(query.data.replace("remove_donor_", ""))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM donors WHERE channel_id = ?", (c_id,))
        await db.commit()
    donors = await get_donors()
    await query.message.edit_text("🗑 <b>Канал-донор успешно удален!</b>", reply_markup=kb_donors_list(donors), parse_mode=ParseMode.HTML)
    await query.answer()

# --- КАНАЛ ПУБЛИКАЦИИ ---
@dp.callback_query(F.data == "menu_target")
async def cb_menu_target(query: CallbackQuery, state: FSMContext):
    s = await get_settings()
    await state.set_state(SetupState.waiting_for_target)
    text = (
        "📤 <b>НАСТРОЙКА КАНАЛА ПУБЛИКАЦИИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Текущий канал:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Идентификатор:</b> <code>{s['target_chat'] or 'Не задан'}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте юзернейм (<code>@my_channel</code>) или ссылку.\n\n"
        "<blockquote>⚠️ <b>Требование:</b> Ваш аккаунт и бот должны иметь права администратора на публикацию постов.</blockquote>"
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_target)
async def process_target_set(message: Message, state: FSMContext):
    raw_target = parse_target_input(message.text)
    try:
        entity = await client.get_entity(raw_target)
        title = getattr(entity, 'title', str(raw_target))
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE settings SET target_chat = ?, target_title = ? WHERE id = 1",
                (str(raw_target), title)
            )
            await db.commit()

        await state.clear()
        s = await get_settings()
        q_count = await get_queue_count()
        slots = await get_slots()
        await message.answer(
            f"✅ <b>Канал публикации привязан!</b>\n\n• <b>Название:</b> <code>{title}</code>\n• <b>Цель:</b> <code>{raw_target}</code>",
            reply_markup=kb_main_menu(s, q_count, len(slots)),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(f"❌ <b>Ошибка:</b> <code>{e}</code>", reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)

# --- ФИЛЬТРЫ МЕДИА И ОЧИСТКА ---
@dp.callback_query(F.data == "menu_media_filters")
async def cb_media_filters(query: CallbackQuery):
    s = await get_settings()
    await query.message.edit_text("🎛 <b>ФИЛЬТРЫ ТИПОВ КОНТЕНТА</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nВключите или выключите типы файлов:", reply_markup=kb_media_filters(s), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data.startswith("toggle_media_"))
async def cb_toggle_media(query: CallbackQuery):
    field = query.data.replace("toggle_media_", "")
    s = await get_settings()
    new_val = 0 if s[field] else 1
    await update_setting(field, new_val)
    s = await get_settings()
    await query.message.edit_reply_markup(reply_markup=kb_media_filters(s))
    await query.answer()

@dp.callback_query(F.data == "menu_text_filters")
async def cb_text_filters(query: CallbackQuery):
    s = await get_settings()
    await query.message.edit_text("🧹 <b>ОЧИСТКА ТЕКСТА И РЕКЛАМЫ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nНастройте автоудаление чужих ссылок:", reply_markup=kb_text_filters(s), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data.in_(["toggle_txt_links", "toggle_txt_tags"]))
async def cb_toggle_text_cleaning(query: CallbackQuery):
    s = await get_settings()
    if query.data == "toggle_txt_links":
        await update_setting("clean_links", 0 if s["clean_links"] else 1)
    else:
        await update_setting("clean_tags", 0 if s["clean_tags"] else 1)
    s = await get_settings()
    await query.message.edit_reply_markup(reply_markup=kb_text_filters(s))
    await query.answer()

# --- ПОДПИСЬ И СТАТИСТИКА ---
@dp.callback_query(F.data == "menu_signature")
async def cb_menu_signature(query: CallbackQuery):
    s = await get_settings()
    sig = s["custom_signature"]
    text = "✍️ <b>ФИРМЕННАЯ ПОДПИСЬ К ПОСТАМ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"Текущая подпись:\n<blockquote>{sig}</blockquote>" if sig else "<i>Подпись не задана.</i>"
    await query.message.edit_text(text, reply_markup=kb_signature_menu(bool(sig)), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "edit_signature_action")
async def cb_edit_signature(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_signature)
    await query.message.edit_text("✍️ <b>Отправьте текст подписи:</b>", reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_signature)
async def process_sig_save(message: Message, state: FSMContext):
    sig = message.text.strip()
    await update_setting("custom_signature", sig)
    await state.clear()
    s = await get_settings()
    q_count = await get_queue_count()
    slots = await get_slots()
    await message.answer("✅ <b>Подпись сохранена!</b>", reply_markup=kb_main_menu(s, q_count, len(slots)), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "clear_signature_action")
async def cb_clear_sig(query: CallbackQuery):
    await update_setting("custom_signature", "")
    await query.message.edit_text("🗑 <b>Подпись удалена.</b>", reply_markup=kb_signature_menu(False), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "menu_stats")
async def cb_stats(query: CallbackQuery):
    s = await get_settings()
    donors = await get_donors()
    q_count = await get_queue_count()
    slots = await get_slots()
    text = (
        "📊 <b>СТАТИСТИКА И ДИАГНОСТИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Всего опубликовано:</b> <code>{s['total_posts']}</code> постов\n"
        f"• <b>В контент-плане:</b> <code>{q_count}</code> постов\n"
        f"• <b>Слотов в расписании:</b> <code>{len(slots)}</code> в день\n"
        f"• <b>Подключено доноров:</b> <code>{len(donors)}</code>\n"
        f"• <b>Приёмник:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Статус:</b> {'🟢 Активен' if s['is_active'] else '⏸ Приостановлен'}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

# ══════════════════════════════════════════════════════════
#     ФОНОВЫЙ ПЛАНИРОВЩИК (SCHEDULER ENGINE)
# ══════════════════════════════════════════════════════════
async def publish_queued_item(queue_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM queue WHERE id = ?", (queue_id,)) as c:
            row = await c.fetchone()
            if not row:
                return

    s = await get_settings()
    if not s["target_chat"]:
        return

    target = parse_target_input(s["target_chat"])
    post_ids = [int(i) for i in row["post_ids"].split(",") if i]

    try:
        messages = await client.get_messages(row["donor_id"], ids=post_ids)
        if not messages:
            return
        if not isinstance(messages, list):
            messages = [messages]

        clean_text = row["text"]

        if row["media_type"] == "album":
            media_list = [m.media for m in messages if m and m.media]
            if media_list:
                await client.send_file(target, media_list, caption=clean_text)
        elif row["media_type"] in ["photo", "video", "audio", "voice", "document"]:
            valid_m = next((m for m in messages if m and m.media), None)
            if valid_m:
                await client.send_message(target, clean_text, file=valid_m.media)
            else:
                if clean_text:
                    await client.send_message(target, clean_text)
        else:
            if clean_text:
                await client.send_message(target, clean_text)

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE queue SET status = 'published' WHERE id = ?", (queue_id,))
            await db.execute("UPDATE settings SET total_posts = total_posts + 1 WHERE id = 1")
            await db.commit()

        logging.info(f"Пост из очереди #{queue_id} опубликован в {target}")
    except Exception as e:
        logging.error(f"Ошибка публикации #{queue_id}: {e}")

async def background_scheduler():
    logging.info("Фоновый планировщик контент-плана запущен.")
    while True:
        try:
            s = await get_settings()
            if s and s["is_active"] and s["target_chat"] and s["mode"] == "schedule":
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                async with aiosqlite.connect(DB_NAME) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT id FROM queue WHERE status = 'pending' AND scheduled_time <= ? ORDER BY scheduled_time ASC",
                        (now_str,)
                    ) as c:
                        rows = await c.fetchall()

                for r in rows:
                    await publish_queued_item(r["id"])
                    await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"Ошибка в планировщике: {e}")
        await asyncio.sleep(15)

# ══════════════════════════════════════════════════════════
#         СБОРКА АЛЬБОМОВ И ПЕРЕХВАТ (TELETHON)
# ══════════════════════════════════════════════════════════
async def process_media_bundle(donor_id: int, messages: list):
    s = await get_settings()
    if not s or not s["is_active"]:
        return

    raw_text = ""
    for m in messages:
        if m.raw_text:
            raw_text = m.raw_text
            break

    clean_text = process_text_content(raw_text, s)
    post_ids_str = ",".join(str(m.id) for m in messages)

    is_album = len(messages) > 1
    media_type = "album" if is_album else "text"

    if not is_album and messages[0].media:
        m = messages[0]
        if isinstance(m.media, MessageMediaPhoto):
            if not s["allow_photos"]: return
            media_type = "photo"
        elif isinstance(m.media, MessageMediaDocument):
            doc = m.media.document
            is_audio = any(isinstance(a, DocumentAttributeAudio) and not a.voice for a in doc.attributes)
            is_voice = any(isinstance(a, DocumentAttributeAudio) and a.voice for a in doc.attributes)
            is_video = any(isinstance(a, DocumentAttributeVideo) for a in doc.attributes)
            
            if is_audio:
                if not s["allow_audio"]: return
                media_type = "audio"
            elif is_voice:
                if not s["allow_voice"]: return
                media_type = "voice"
            elif is_video:
                if not s["allow_videos"]: return
                media_type = "video"
            else:
                if not s["allow_docs"]: return
                media_type = "document"

    if s["mode"] == "instant":
        target = parse_target_input(s["target_chat"])
        if is_album:
            files = [m.media for m in messages if m.media]
            await client.send_file(target, files, caption=clean_text)
        elif media_type != "text":
            await client.send_message(target, clean_text, file=messages[0].media)
        else:
            if clean_text:
                await client.send_message(target, clean_text)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE settings SET total_posts = total_posts + 1 WHERE id = 1")
            await db.commit()
        logging.info(f"Мгновенно опубликован пост ({media_type}) в {target}")
    else:
        next_time = await calculate_next_scheduled_time()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                """INSERT INTO queue (donor_id, post_ids, text, media_type, scheduled_time, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (donor_id, post_ids_str, clean_text, media_type, next_time)
            )
            await db.commit()
        logging.info(f"Пост ({media_type}) поставлен в контент-план на {next_time}")

async def album_debouncer(group_key: tuple):
    await asyncio.sleep(2.0)
    async with ALBUM_LOCK:
        messages = ALBUM_BUFFER.pop(group_key, [])
    if messages:
        await process_media_bundle(group_key[0], messages)

@client.on(events.NewMessage)
async def telethon_event_receiver(event):
    try:
        s = await get_settings()
        if not s or not s["is_active"]:
            return

        chat = await event.get_chat()
        chat_id = chat.id

        donors = await get_donors()
        donor_ids = [d["channel_id"] for d in donors]

        if chat_id not in donor_ids and getattr(chat, 'broadcast', False) is False:
            return

        if chat_id not in donor_ids:
            return

        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM history WHERE donor_id = ? AND post_id = ?", (chat_id, event.id)) as c:
                if await c.fetchone():
                    return
            await db.execute("INSERT INTO history (donor_id, post_id) VALUES (?, ?)", (chat_id, event.id))
            await db.commit()

        if event.message.grouped_id:
            group_key = (chat_id, event.message.grouped_id)
            async with ALBUM_LOCK:
                if group_key not in ALBUM_BUFFER:
                    ALBUM_BUFFER[group_key] = []
                    asyncio.create_task(album_debouncer(group_key))
                ALBUM_BUFFER[group_key].append(event.message)
        else:
            await process_media_bundle(chat_id, [event.message])

    except Exception as e:
        logging.error(f"Ошибка перехвата поста #{event.id}: {e}")

# ══════════════════════════════════════════════════════════
#                     ТОЧКА ЗАПУСКА
# ══════════════════════════════════════════════════════════
async def main():
    await init_db()
    logging.info("База данных готова к работе.")
    await client.start()
    logging.info("Telethon клиент запущен и авторизован.")
    asyncio.create_task(background_scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
