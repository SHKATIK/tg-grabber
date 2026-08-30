import asyncio
import datetime
import json
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

# Буфер для сборки альбомов (grouped_id -> list of messages)
ALBUM_BUFFER = {}
ALBUM_LOCK = asyncio.Lock()

# ══════════════════════════════════════════════════════════
#                  СЛОВАРЬ ЯЗЫКОВ (I18N)
# ══════════════════════════════════════════════════════════
TEXTS = {
    "ru": {
        "main_title": (
            "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ FLEEP-GRABBER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Приёмник:</b> <code>{target}</code>\n"
            "• <b>Режим:</b> <code>{mode}</code>\n"
            "• <b>В очереди (Контент-план):</b> <code>{queue_count}</code> постов\n"
            "• <b>Всего опубликовано:</b> <code>{count}</code> постов\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Сборка альбомов до 10 медиа и автоматическая раскладка по тайм-слотам.</blockquote>"
        ),
        "mode_schedule": "⏰ По расписанию",
        "mode_instant": "⚡ Мгновенно",
        "status_running": "🟢 РАБОТАЕТ",
        "status_paused": "⏸ НА ПАУЗЕ",
        "btn_pause": "⏸ Приостановить",
        "btn_resume": "▶️ Возобновить работу",
        "btn_plan": "📅 Контент-план ({count})",
        "btn_schedule": "⏰ Тайм-слоты дня",
        "btn_mode": "🔄 Сменить режим отправки",
        "btn_donors": "📥 Источники (Доноры)",
        "btn_target": "📤 Канал публикации",
        "btn_media_filters": "🎛 Фильтры контента",
        "btn_text_filters": "🧹 Очистка текста",
        "btn_signature": "✍️ Фирменная подпись",
        "btn_stats": "📊 Статистика",
        "btn_change_lang": "🌐 Сменить язык",
        "btn_back": "🔙 Назад в меню",
        "btn_add_donor": "➕ Добавить донора",
        "donors_title": "📥 <b>ИСТОЧНИКИ (КАНАЛЫ-ДОНОРЫ)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "donors_empty": "<i>Список пуст. Добавьте первый канал для копирования.</i>",
        "donors_count": "Подключено доноров: <b>{count}</b>\n<i>Нажмите на канал для удаления:</i>",
        "add_donor_prompt": (
            "📥 <b>ДОБАВЛЕНИЕ НОВОГО ДОНОРА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Отправьте боту:\n"
            "1. Юзернейм: <code>@channel_name</code>\n"
            "2. Ссылку: <code>https://t.me/channel_name</code>\n"
            "3. Перешлите сообщение из этого канала сюда.\n\n"
            "<blockquote>⚠️ <b>Важно:</b> Ваш Telegram-аккаунт должен быть подписан на канал.</blockquote>"
        ),
        "donor_added_success": "✅ <b>Канал успешно подключен!</b>\n\n• <b>Название:</b> <code>{title}</code>\n• <b>ID:</b> <code>{id}</code>",
        "donor_deleted": "🗑 <b>Канал-донор успешно удален!</b>",
        "error_find_channel": "❌ <b>Ошибка поиска:</b> <code>{err}</code>\n\n<i>Убедитесь, что ваш аккаунт подписан на этот канал.</i>",
        "target_title_card": (
            "📤 <b>НАСТРОЙКА КАНАЛА ПУБЛИКАЦИИ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Текущий канал:</b> <code>{title}</code>\n"
            "• <b>Идентификатор:</b> <code>{target}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Отправьте юзернейм (<code>@my_channel</code>) или ссылку.\n\n"
            "<blockquote>⚠️ <b>Требование:</b> Ваш аккаунт и бот должны иметь права на публикацию записей.</blockquote>"
        ),
        "target_set_success": "✅ <b>Канал публикации привязан!</b>\n\n• <b>Название:</b> <code>{title}</code>\n• <b>Цель:</b> <code>{target}</code>",
        "media_filters_title": "🎛 <b>ФИЛЬТРЫ ТИПОВ КОНТЕНТА</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nВключите или выключите типы медиафайлов для копирования:",
        "f_photos": "Фотографии",
        "f_videos": "Видео / Reels",
        "f_audio": "Музыка / Треки",
        "f_docs": "Файлы / Архивы",
        "f_voice": "Кружки / Voice",
        "text_filters_title": "🧹 <b>ОЧИСТКА ТЕКСТА И РЕКЛАМЫ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nНастройте автоудаление ссылок и упоминаний:",
        "f_clean_links": "Удаление ссылок (http / t.me)",
        "f_clean_tags": "Удаление тегов (@username)",
        "sig_title": "✍️ <b>ФИРМЕННАЯ ПОДПИСЬ К ПОСТАМ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "sig_current": "Текущая подпись:\n<blockquote>{sig}</blockquote>",
        "sig_none": "<i>Подпись не задана. Посты публикуются как есть.</i>",
        "sig_btn_edit": "✏️ Установить новую подпись",
        "sig_btn_clear": "🗑 Удалить подпись",
        "sig_prompt": "✍️ <b>Отправьте текст подписи</b>, который будет прикрепляться к каждому посту:\n\n<i>Пример:</i> <code>Подпишись на @TelegaCosplay 🔥</code>",
        "sig_saved": "✅ <b>Фирменная подпись сохранена!</b>",
        "sig_cleared": "🗑 <b>Подпись успешно удалена.</b>",
        "plan_empty": "📅 <b>КОНТЕНТ-ПЛАН ПУСТ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nВ очереди нет запланированных постов.",
        "plan_card": (
            "📅 <b>ПОСТ В ОЧЕРЕДИ [{current}/{total}]</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Дата публикации:</b> <code>{time}</code>\n"
            "• <b>Тип контента:</b> <code>{media_type}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Превью текста:</b>\n{preview}"
        ),
        "btn_pub_now": "🚀 Опубликовать сейчас",
        "btn_del_post": "🗑 Удалить из очереди",
        "slots_title": (
            "⏰ <b>ТАЙМ-СЛОТЫ РАСПИСАНИЯ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "В эти часы бот автоматически выпускает готовые посты из очереди:\n\n"
            "<b>Текущие слоты:</b> {slots}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "btn_add_slot": "➕ Добавить слот времени",
        "btn_reset_slots": "🔄 Сбросить к стандартным (10, 14, 18, 22)",
        "prompt_slot": "⏰ Отправьте время в формате <code>ЧЧ:ММ</code> (например, <code>15:30</code> или <code>09:00</code>):"
    },
    "en": {
        "main_title": (
            "👑 <b>FLEEP-GRABBER CONTROL PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Target:</b> <code>{target}</code>\n"
            "• <b>Mode:</b> <code>{mode}</code>\n"
            "• <b>Queued (Content Plan):</b> <code>{queue_count}</code> posts\n"
            "• <b>Total published:</b> <code>{count}</code> posts\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>Album aggregation up to 10 media & auto-scheduling into time-slots.</blockquote>"
        ),
        "mode_schedule": "⏰ Scheduled",
        "mode_instant": "⚡ Instant",
        "status_running": "🟢 RUNNING",
        "status_paused": "⏸ PAUSED",
        "btn_pause": "⏸ Pause Grabber",
        "btn_resume": "▶️ Resume Grabber",
        "btn_plan": "📅 Content Plan ({count})",
        "btn_schedule": "⏰ Daily Time-Slots",
        "btn_mode": "🔄 Switch Post Mode",
        "btn_donors": "📥 Sources (Donors)",
        "btn_target": "📤 Target Channel",
        "btn_media_filters": "🎛 Media Filters",
        "btn_text_filters": "🧹 Text Cleaner",
        "btn_signature": "✍️ Custom Signature",
        "btn_stats": "📊 Statistics",
        "btn_change_lang": "🌐 Change Language",
        "btn_back": "🔙 Back to menu",
        "btn_add_donor": "➕ Add Donor Channel",
        "donors_title": "📥 <b>SOURCES (DONOR CHANNELS)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "donors_empty": "<i>List is empty. Click button below to add a donor.</i>",
        "donors_count": "Active donors: <b>{count}</b>\n<i>Click a channel button to remove it:</i>",
        "add_donor_prompt": (
            "📥 <b>ADD NEW DONOR CHANNEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send to bot:\n"
            "1. Username: <code>@channel_name</code>\n"
            "2. Link: <code>https://t.me/channel_name</code>\n"
            "3. Forward any post from that channel here.\n\n"
            "<blockquote>⚠️ <b>Important:</b> Your Telegram account must be subscribed to this channel.</blockquote>"
        ),
        "donor_added_success": "✅ <b>Channel successfully added!</b>\n\n• <b>Title:</b> <code>{title}</code>\n• <b>ID:</b> <code>{id}</code>",
        "donor_deleted": "🗑 <b>Donor channel successfully removed!</b>",
        "error_find_channel": "❌ <b>Channel not found:</b> <code>{err}</code>\n\n<i>Make sure your account is subscribed to this channel.</i>",
        "target_title_card": (
            "📤 <b>TARGET CHANNEL SETTINGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Current target:</b> <code>{title}</code>\n"
            "• <b>Identifier:</b> <code>{target}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send username (<code>@my_channel</code>) or link.\n\n"
            "<blockquote>⚠️ <b>Requirement:</b> Both your account and bot must have admin rights to post messages.</blockquote>"
        ),
        "target_set_success": "✅ <b>Target channel linked!</b>\n\n• <b>Title:</b> <code>{title}</code>\n• <b>Target:</b> <code>{target}</code>",
        "media_filters_title": "🎛 <b>MEDIA TYPE FILTERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nEnable or disable media types to copy:",
        "f_photos": "Photos",
        "f_videos": "Videos / Reels",
        "f_audio": "Music / Audio",
        "f_docs": "Files / Archives",
        "f_voice": "Voice / Circles",
        "text_filters_title": "🧹 <b>TEXT & AD CLEANER</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nConfigure automatic link and mention removal:",
        "f_clean_links": "Remove links (http / t.me)",
        "f_clean_tags": "Remove tags (@username)",
        "sig_title": "✍️ <b>CUSTOM POST SIGNATURE</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "sig_current": "Current signature:\n<blockquote>{sig}</blockquote>",
        "sig_none": "<i>No signature set. Posts will be published as is.</i>",
        "sig_btn_edit": "✏️ Set / Edit Signature",
        "sig_btn_clear": "🗑 Delete Signature",
        "sig_prompt": "✍️ <b>Send your signature text</b> to append at the end of each post:\n\n<i>Example:</i> <code>Follow @TelegaCosplay 🔥</code>",
        "sig_saved": "✅ <b>Custom signature saved!</b>",
        "sig_cleared": "🗑 <b>Signature removed.</b>",
        "plan_empty": "📅 <b>CONTENT PLAN IS EMPTY</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nNo pending posts in queue.",
        "plan_card": (
            "📅 <b>QUEUED POST [{current}/{total}]</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Scheduled time:</b> <code>{time}</code>\n"
            "• <b>Media type:</b> <code>{media_type}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Text preview:</b>\n{preview}"
        ),
        "btn_pub_now": "🚀 Publish Now",
        "btn_del_post": "🗑 Delete from Queue",
        "slots_title": (
            "⏰ <b>DAILY TIME-SLOTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Queued posts are automatically released at these hours:\n\n"
            "<b>Active slots:</b> {slots}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "btn_add_slot": "➕ Add Time Slot",
        "btn_reset_slots": "🔄 Reset to Default (10, 14, 18, 22)",
        "prompt_slot": "⏰ Send time in format <code>HH:MM</code> (e.g. <code>15:30</code> or <code>09:00</code>):"
    }
}

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
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru'
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
            CREATE TABLE IF NOT EXISTS schedule_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_time TEXT UNIQUE
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
        
        # Дефолтные 4 слота времени
        for slot in ["10:00", "14:00", "18:00", "22:00"]:
            await db.execute("INSERT OR IGNORE INTO schedule_slots (slot_time) VALUES (?)", (slot,))
            
        await db.commit()

async def get_user_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()
            return row[0] if row else "ru"

async def set_user_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO users (user_id, language) VALUES (?, ?)", (user_id, lang))
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

async def increment_counter():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE settings SET total_posts = total_posts + 1 WHERE id = 1")
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

async def get_pending_queue_count():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'") as c:
            row = await c.fetchone()
            return row[0] if row else 0

# ══════════════════════════════════════════════════════════
#               АЛГОРИТМ РАСЧЕТА СЛОТОВ ВРЕМЕНИ
# ══════════════════════════════════════════════════════════
async def calculate_next_scheduled_time() -> str:
    slots = await get_slots()
    if not slots:
        slots = ["10:00", "14:00", "18:00", "22:00"]

    now = datetime.datetime.now()

    # Смотрим последний запланированный пост в очереди
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT scheduled_time FROM queue WHERE status = 'pending' ORDER BY scheduled_time DESC LIMIT 1"
        ) as c:
            last_row = await c.fetchone()

    if last_row and last_row[0]:
        try:
            last_time = datetime.datetime.strptime(last_row[0], "%Y-%m-%d %H:%M:%S")
            base_date = last_time.date()
        except Exception:
            last_time = now
            base_date = now.date()
    else:
        last_time = now
        base_date = now.date()

    # Поиск следующего слота
    for day_offset in range(0, 60):
        current_date = base_date + datetime.timedelta(days=day_offset)
        for s in sorted(slots):
            sh, sm = map(int, s.split(":"))
            slot_dt = datetime.datetime.combine(current_date, datetime.time(sh, sm))
            # Слот должен быть позже последнего запланированного и позже текущего момента + 1 мин
            if slot_dt > last_time and slot_dt > now + datetime.timedelta(minutes=1):
                return slot_dt.strftime("%Y-%m-%d %H:%M:%S")

    fallback = now + datetime.timedelta(hours=2)
    return fallback.strftime("%Y-%m-%d %H:%M:%S")

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
def kb_language_select():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_en")
        ]
    ])

def kb_main_menu(s: aiosqlite.Row, lang: str, queue_count: int):
    t = TEXTS[lang]
    status_icon = t["status_running"] if s["is_active"] else t["status_paused"]
    toggle_btn = t["btn_pause"] if s["is_active"] else t["btn_resume"]
    mode_text = t["mode_schedule"] if s["mode"] == "schedule" else t["mode_instant"]

    keyboard = [
        [InlineKeyboardButton(text=f"{status_icon}", callback_data="none")],
        [InlineKeyboardButton(text=toggle_btn, callback_data="toggle_system_status")],
        [
            InlineKeyboardButton(text=t["btn_plan"].format(count=queue_count), callback_data="open_queue_page_0"),
            InlineKeyboardButton(text=t["btn_schedule"], callback_data="menu_schedule_slots")
        ],
        [
            InlineKeyboardButton(text=f"Режим: {mode_text}", callback_data="toggle_posting_mode")
        ],
        [
            InlineKeyboardButton(text=t["btn_donors"], callback_data="menu_donors"),
            InlineKeyboardButton(text=t["btn_target"], callback_data="menu_target")
        ],
        [
            InlineKeyboardButton(text=t["btn_media_filters"], callback_data="menu_media_filters"),
            InlineKeyboardButton(text=t["btn_text_filters"], callback_data="menu_text_filters")
        ],
        [
            InlineKeyboardButton(text=t["btn_signature"], callback_data="menu_signature"),
            InlineKeyboardButton(text=t["btn_change_lang"], callback_data="choose_language_action")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def kb_donors_list(donors, lang: str):
    t = TEXTS[lang]
    buttons = []
    for d in donors:
        title = d["title"][:22] + "..." if len(d["title"]) > 25 else d["title"]
        buttons.append([InlineKeyboardButton(text=f"🗑 {title}", callback_data=f"remove_donor_{d['channel_id']}")])
    buttons.append([InlineKeyboardButton(text=t["btn_add_donor"], callback_data="add_donor_action")])
    buttons.append([InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_media_filters(s: aiosqlite.Row, lang: str):
    t = TEXTS[lang]
    def chk(val): return "✅" if val else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{chk(s['allow_photos'])} {t['f_photos']}", callback_data="toggle_media_allow_photos"),
            InlineKeyboardButton(text=f"{chk(s['allow_videos'])} {t['f_videos']}", callback_data="toggle_media_allow_videos")
        ],
        [
            InlineKeyboardButton(text=f"{chk(s['allow_audio'])} {t['f_audio']}", callback_data="toggle_media_allow_audio"),
            InlineKeyboardButton(text=f"{chk(s['allow_docs'])} {t['f_docs']}", callback_data="toggle_media_allow_docs")
        ],
        [
            InlineKeyboardButton(text=f"{chk(s['allow_voice'])} {t['f_voice']}", callback_data="toggle_media_allow_voice")
        ],
        [InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")]
    ])

def kb_text_filters(s: aiosqlite.Row, lang: str):
    t = TEXTS[lang]
    def chk(val): return "✅" if val else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{t['f_clean_links']}: {chk(s['clean_links'])}", callback_data="toggle_txt_links")],
        [InlineKeyboardButton(text=f"{t['f_clean_tags']}: {chk(s['clean_tags'])}", callback_data="toggle_txt_tags")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")]
    ])

def kb_signature_menu(has_sig: bool, lang: str):
    t = TEXTS[lang]
    buttons = [
        [InlineKeyboardButton(text=t["sig_btn_edit"], callback_data="edit_signature_action")]
    ]
    if has_sig:
        buttons.append([InlineKeyboardButton(text=t["sig_btn_clear"], callback_data="clear_signature_action")])
    buttons.append([InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_slots_menu(slots, lang: str):
    t = TEXTS[lang]
    buttons = []
    for s in slots:
        buttons.append([InlineKeyboardButton(text=f"🗑 Удалить {s}", callback_data=f"del_slot_{s}")])
    buttons.append([InlineKeyboardButton(text=t["btn_add_slot"], callback_data="add_slot_action")])
    buttons.append([InlineKeyboardButton(text=t["btn_reset_slots"], callback_data="reset_slots_action")])
    buttons.append([InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_queue_pagination(current_idx: int, total_count: int, post_id: int, lang: str):
    t = TEXTS[lang]
    nav_buttons = []
    if current_idx > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"open_queue_page_{current_idx - 1}"))
    if current_idx < total_count - 1:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"open_queue_page_{current_idx + 1}"))

    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton(text=t["btn_pub_now"], callback_data=f"queue_publish_now_{post_id}_{current_idx}")])
    keyboard.append([InlineKeyboardButton(text=t["btn_del_post"], callback_data=f"queue_delete_{post_id}_{current_idx}")])
    keyboard.append([InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def kb_back_only(lang: str):
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_back"], callback_data="open_main_menu")]
    ])

# ══════════════════════════════════════════════════════════
#               ОБРАБОТЧИКИ МЕНЮ (AIOGRAM)
# ══════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start_handler(message: Message, state: FSMContext):
    await state.clear()
    lang = await get_user_lang(message.from_user.id)
    s = await get_settings()
    q_count = await get_pending_queue_count()
    t = TEXTS[lang]
    mode_str = t["mode_schedule"] if s["mode"] == "schedule" else t["mode_instant"]
    text = t["main_title"].format(
        target=s['target_title'],
        mode=mode_str,
        queue_count=q_count,
        count=s['total_posts']
    )
    await message.answer(text, reply_markup=kb_main_menu(s, lang, q_count), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "open_main_menu")
async def cb_back_to_main(query: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await get_user_lang(query.from_user.id)
    s = await get_settings()
    q_count = await get_pending_queue_count()
    t = TEXTS[lang]
    mode_str = t["mode_schedule"] if s["mode"] == "schedule" else t["mode_instant"]
    text = t["main_title"].format(
        target=s['target_title'],
        mode=mode_str,
        queue_count=q_count,
        count=s['total_posts']
    )
    await query.message.edit_text(text, reply_markup=kb_main_menu(s, lang, q_count), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "toggle_posting_mode")
async def cb_toggle_mode(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    s = await get_settings()
    new_mode = "instant" if s["mode"] == "schedule" else "schedule"
    await update_setting("mode", new_mode)
    s = await get_settings()
    q_count = await get_pending_queue_count()
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(s, lang, q_count))
    await query.answer("Режим публикации переключен!")

@dp.callback_query(F.data == "toggle_system_status")
async def cb_toggle_status(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    s = await get_settings()
    new_val = 0 if s["is_active"] else 1
    await update_setting("is_active", new_val)
    s = await get_settings()
    q_count = await get_pending_queue_count()
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(s, lang, q_count))
    await query.answer()

@dp.callback_query(F.data == "choose_language_action")
async def cb_choose_language(query: CallbackQuery):
    await query.message.edit_text(
        "🌐 <b>Выберите язык интерфейса / Select interface language:</b>",
        reply_markup=kb_language_select(),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@dp.callback_query(F.data.startswith("set_lang_"))
async def cb_set_language(query: CallbackQuery):
    lang = query.data.replace("set_lang_", "")
    await set_user_lang(query.from_user.id, lang)
    s = await get_settings()
    q_count = await get_pending_queue_count()
    t = TEXTS[lang]
    mode_str = t["mode_schedule"] if s["mode"] == "schedule" else t["mode_instant"]
    text = t["main_title"].format(target=s['target_title'], mode=mode_str, queue_count=q_count, count=s['total_posts'])
    await query.message.edit_text(text, reply_markup=kb_main_menu(s, lang, q_count), parse_mode=ParseMode.HTML)
    await query.answer("Language updated!")

# --- КОНТЕНТ-ПЛАН (ПРОСМОТР ОЧЕРЕДИ) ---
@dp.callback_query(F.data.startswith("open_queue_page_"))
async def cb_open_queue(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    page_idx = int(query.data.replace("open_queue_page_", ""))
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'") as c:
            total = (await c.fetchone())[0]

        if total == 0:
            await query.message.edit_text(t["plan_empty"], reply_markup=kb_back_only(lang), parse_mode=ParseMode.HTML)
            await query.answer()
            return

        if page_idx >= total:
            page_idx = total - 1

        async with db.execute(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY scheduled_time ASC LIMIT 1 OFFSET ?",
            (page_idx,)
        ) as c:
            row = await c.fetchone()

    preview = row["text"][:200] + "..." if len(row["text"]) > 200 else (row["text"] or "<i>[Без текста]</i>")
    text = t["plan_card"].format(
        current=page_idx + 1,
        total=total,
        time=row["scheduled_time"],
        media_type=row["media_type"].upper(),
        preview=preview
    )
    
    await query.message.edit_text(
        text,
        reply_markup=kb_queue_pagination(page_idx, total, row["id"], lang),
        parse_mode=ParseMode.HTML
    )
    await query.answer()

@dp.callback_query(F.data.startswith("queue_publish_now_"))
async def cb_queue_pub_now(query: CallbackQuery):
    parts = query.data.split("_")
    post_id = int(parts[3])
    page_idx = int(parts[4])
    
    await publish_queued_item(post_id)
    await query.answer("Пост опубликован прямо сейчас!")
    
    # Открываем ту же страницу
    await cb_open_queue_by_idx(query, page_idx)

@dp.callback_query(F.data.startswith("queue_delete_"))
async def cb_queue_delete(query: CallbackQuery):
    parts = query.data.split("_")
    post_id = int(parts[2])
    page_idx = int(parts[3])
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM queue WHERE id = ?", (post_id,))
        await db.commit()

    await query.answer("Пост удален из очереди!")
    await cb_open_queue_by_idx(query, max(0, page_idx - 1))

async def cb_open_queue_by_idx(query: CallbackQuery, page_idx: int):
    query.data = f"open_queue_page_{page_idx}"
    await cb_open_queue(query)

# --- ТАЙМ-СЛОТЫ ДНЯ ---
@dp.callback_query(F.data == "menu_schedule_slots")
async def cb_menu_slots(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    slots = await get_slots()
    slots_str = ", ".join(f"<code>{s}</code>" for s in slots) if slots else "<i>Слоты не заданы</i>"
    text = t["slots_title"].format(slots=slots_str)
    await query.message.edit_text(text, reply_markup=kb_slots_menu(slots, lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "add_slot_action")
async def cb_add_slot_prompt(query: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    await state.set_state(SetupState.waiting_for_slot)
    await query.message.edit_text(t["prompt_slot"], reply_markup=kb_back_only(lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_slot)
async def process_slot_input(message: Message, state: FSMContext):
    lang = await get_user_lang(message.from_user.id)
    val = message.text.strip()
    if re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", val):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO schedule_slots (slot_time) VALUES (?)", (val,))
            await db.commit()
        await state.clear()
        slots = await get_slots()
        slots_str = ", ".join(f"<code>{s}</code>" for s in slots)
        await message.answer(
            f"✅ Слот <code>{val}</code> успешно добавлен!\n\nТекущие слоты: {slots_str}",
            reply_markup=kb_slots_menu(slots, lang),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ Неверный формат времени. Введите время в формате <code>ЧЧ:ММ</code> (например <code>14:30</code>):",
            reply_markup=kb_back_only(lang),
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data.startswith("del_slot_"))
async def cb_del_slot(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    slot_val = query.data.replace("del_slot_", "")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots WHERE slot_time = ?", (slot_val,))
        await db.commit()
    slots = await get_slots()
    slots_str = ", ".join(f"<code>{s}</code>" for s in slots) if slots else "<i>Слоты не заданы</i>"
    text = TEXTS[lang]["slots_title"].format(slots=slots_str)
    await query.message.edit_text(text, reply_markup=kb_slots_menu(slots, lang), parse_mode=ParseMode.HTML)
    await query.answer(f"Слот {slot_val} удален!")

@dp.callback_query(F.data == "reset_slots_action")
async def cb_reset_slots(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM schedule_slots")
        for s in ["10:00", "14:00", "18:00", "22:00"]:
            await db.execute("INSERT INTO schedule_slots (slot_time) VALUES (?)", (s,))
        await db.commit()
    slots = await get_slots()
    slots_str = ", ".join(f"<code>{s}</code>" for s in slots)
    text = TEXTS[lang]["slots_title"].format(slots=slots_str)
    await query.message.edit_text(text, reply_markup=kb_slots_menu(slots, lang), parse_mode=ParseMode.HTML)
    await query.answer("Слоты сброшены к стандарту!")

# --- ДОНОРЫ ---
@dp.callback_query(F.data == "menu_donors")
async def cb_menu_donors(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    donors = await get_donors()
    text = t["donors_title"]
    if not donors:
        text += t["donors_empty"]
    else:
        text += t["donors_count"].format(count=len(donors))
    
    await query.message.edit_text(text, reply_markup=kb_donors_list(donors, lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "add_donor_action")
async def cb_add_donor_prompt(query: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    await state.set_state(SetupState.waiting_for_donor)
    await query.message.edit_text(t["add_donor_prompt"], reply_markup=kb_back_only(lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_donor)
async def process_donor_add(message: Message, state: FSMContext):
    lang = await get_user_lang(message.from_user.id)
    t = TEXTS[lang]
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
            t["donor_added_success"].format(title=title, id=channel_id),
            reply_markup=kb_donors_list(donors, lang),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            t["error_find_channel"].format(err=e),
            reply_markup=kb_back_only(lang),
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data.startswith("remove_donor_"))
async def cb_remove_donor(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    c_id = int(query.data.replace("remove_donor_", ""))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM donors WHERE channel_id = ?", (c_id,))
        await db.commit()
    donors = await get_donors()
    await query.message.edit_text(t["donor_deleted"], reply_markup=kb_donors_list(donors, lang), parse_mode=ParseMode.HTML)
    await query.answer()

# --- КАНАЛ ПУБЛИКАЦИИ ---
@dp.callback_query(F.data == "menu_target")
async def cb_menu_target(query: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    s = await get_settings()
    await state.set_state(SetupState.waiting_for_target)
    text = t["target_title_card"].format(
        title=s['target_title'],
        target=s['target_chat'] or 'None'
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_target)
async def process_target_set(message: Message, state: FSMContext):
    lang = await get_user_lang(message.from_user.id)
    t = TEXTS[lang]
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
        q_count = await get_pending_queue_count()
        await message.answer(
            t["target_set_success"].format(title=title, target=raw_target),
            reply_markup=kb_main_menu(s, lang, q_count),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Error / Ошибка:</b> <code>{e}</code>",
            reply_markup=kb_back_only(lang),
            parse_mode=ParseMode.HTML
        )

# --- ФИЛЬТРЫ МЕДИА И ТЕКСТА ---
@dp.callback_query(F.data == "menu_media_filters")
async def cb_media_filters(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    s = await get_settings()
    await query.message.edit_text(t["media_filters_title"], reply_markup=kb_media_filters(s, lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data.startswith("toggle_media_"))
async def cb_toggle_media(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    field = query.data.replace("toggle_media_", "")
    s = await get_settings()
    new_val = 0 if s[field] else 1
    await update_setting(field, new_val)
    s = await get_settings()
    await query.message.edit_reply_markup(reply_markup=kb_media_filters(s, lang))
    await query.answer()

@dp.callback_query(F.data == "menu_text_filters")
async def cb_text_filters(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    s = await get_settings()
    await query.message.edit_text(t["text_filters_title"], reply_markup=kb_text_filters(s, lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data.in_(["toggle_txt_links", "toggle_txt_tags"]))
async def cb_toggle_text_cleaning(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    s = await get_settings()
    if query.data == "toggle_txt_links":
        await update_setting("clean_links", 0 if s["clean_links"] else 1)
    else:
        await update_setting("clean_tags", 0 if s["clean_tags"] else 1)
    s = await get_settings()
    await query.message.edit_reply_markup(reply_markup=kb_text_filters(s, lang))
    await query.answer()

# --- КАСТОМНАЯ ПОДПИСЬ ---
@dp.callback_query(F.data == "menu_signature")
async def cb_menu_signature(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    s = await get_settings()
    sig = s["custom_signature"]
    text = t["sig_title"]
    if sig:
        text += t["sig_current"].format(sig=sig)
    else:
        text += t["sig_none"]
    
    await query.message.edit_text(text, reply_markup=kb_signature_menu(bool(sig), lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "edit_signature_action")
async def cb_edit_signature(query: CallbackQuery, state: FSMContext):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    await state.set_state(SetupState.waiting_for_signature)
    await query.message.edit_text(t["sig_prompt"], reply_markup=kb_back_only(lang), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_signature)
async def process_sig_save(message: Message, state: FSMContext):
    lang = await get_user_lang(message.from_user.id)
    t = TEXTS[lang]
    sig = message.text.strip()
    await update_setting("custom_signature", sig)
    await state.clear()
    s = await get_settings()
    q_count = await get_pending_queue_count()
    await message.answer(t["sig_saved"], reply_markup=kb_main_menu(s, lang, q_count), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "clear_signature_action")
async def cb_clear_sig(query: CallbackQuery):
    lang = await get_user_lang(query.from_user.id)
    t = TEXTS[lang]
    await update_setting("custom_signature", "")
    await query.message.edit_text(t["sig_cleared"], reply_markup=kb_signature_menu(False, lang), parse_mode=ParseMode.HTML)
    await query.answer()

# ══════════════════════════════════════════════════════════
#     ФОНОВАЯ ПУБЛИКАЦИЯ ИЗ ОЧЕРЕДИ (SCHEDULER ENGINE)
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
            await db.commit()

        await increment_counter()
        logging.info(f"Очередь #{queue_id} успешно опубликована в {target}")
    except Exception as e:
        logging.error(f"Ошибка публикации из очереди #{queue_id}: {e}")

async def background_scheduler():
    logging.info("Фоновый планировщик очереди запущен.")
    while True:
        try:
            s = await get_settings()
            if s and s["is_active"] and s["target_chat"] and s["mode"] == "schedule":
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            logging.error(f"Ошибка в цикле планировщика: {e}")
        await asyncio.sleep(20)

# ══════════════════════════════════════════════════════════
#     СБОРЩИК АЛЬБОМОВ И МГНОВЕННЫЙ ПЕРЕХВАТ (TELETHON)
# ══════════════════════════════════════════════════════════
async def process_media_bundle(donor_id: int, messages: list):
    s = await get_settings()
    if not s or not s["is_active"]:
        return

    # Извлекаем текст
    raw_text = ""
    for m in messages:
        if m.raw_text:
            raw_text = m.raw_text
            break

    clean_text = process_text_content(raw_text, s)
    post_ids_str = ",".join(str(m.id) for m in messages)

    # Определяем тип медиа
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

    # Если включен режим мгновенной отправки
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
        await increment_counter()
        logging.info(f"Мгновенно переслан пост ({media_type}) из {donor_id} в {target}")
    else:
        # Режим расписания: рассчитываем следующий слот времени
        next_time = await calculate_next_scheduled_time()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                """INSERT INTO queue (donor_id, post_ids, text, media_type, scheduled_time, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (donor_id, post_ids_str, clean_text, media_type, next_time)
            )
            await db.commit()
        logging.info(f"Пост ({media_type}) добавлен в контент-план на {next_time}")

async def album_debouncer(group_key: tuple):
    await asyncio.sleep(2.0)
    async with ALBUM_LOCK:
        messages = ALBUM_BUFFER.pop(group_key, [])
    if messages:
        donor_id = group_key[0]
        await process_media_bundle(donor_id, messages)

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

        # Проверка на дубликаты
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM history WHERE donor_id = ? AND post_id = ?", (chat_id, event.id)) as c:
                if await c.fetchone():
                    return
            await db.execute("INSERT INTO history (donor_id, post_id) VALUES (?, ?)", (chat_id, event.id))
            await db.commit()

        # Если сообщение принадлежит альбому (Media Group)
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
        logging.error(f"Ошибка при перехвате поста #{event.id}: {e}")

# ══════════════════════════════════════════════════════════
#                     ТОЧКА ЗАПУСКА
# ══════════════════════════════════════════════════════════
async def main():
    await init_db()
    logging.info("База данных готова к работе.")
    await client.start()
    logging.info("Telethon клиент запущен и слушает каналы-доноры.")
    asyncio.create_task(background_scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
