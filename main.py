import asyncio
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

# ══════════════════════════════════════════════════════════
#                     СОСТОЯНИЯ FSM
# ══════════════════════════════════════════════════════════
class SetupState(StatesGroup):
    waiting_for_donor = State()
    waiting_for_target = State()
    waiting_for_signature = State()

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
            INSERT OR IGNORE INTO settings (id, target_chat, target_title, is_active)
            VALUES (1, '', 'Не привязан', 1)
        """)
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
def kb_main_menu(s: aiosqlite.Row):
    status_icon = "🟢 РАБОТАЕТ" if s["is_active"] else "⏸ НА ПАУЗЕ"
    toggle_btn = "⏸ Приостановить" if s["is_active"] else "▶️ Возобновить работу"
    
    keyboard = [
        [InlineKeyboardButton(text=f"Статус системы: {status_icon}", callback_data="none")],
        [InlineKeyboardButton(text=toggle_btn, callback_data="toggle_system_status")],
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
            InlineKeyboardButton(text=f"{chk(s['allow_audio'])} Музыка / Треки", callback_data="toggle_media_allow_audio"),
            InlineKeyboardButton(text=f"{chk(s['allow_docs'])} Файлы / Документы", callback_data="toggle_media_allow_docs")
        ],
        [
            InlineKeyboardButton(text=f"{chk(s['allow_voice'])} Голосовые / Кружки", callback_data="toggle_media_allow_voice")
        ],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")]
    ])

def kb_text_filters(s: aiosqlite.Row):
    def chk(val): return "✅ ВКЛ" if val else "❌ ВЫКЛ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Удаление ссылок (http / t.me): {chk(s['clean_links'])}", callback_data="toggle_txt_links")],
        [InlineKeyboardButton(text=f"Удаление тегов (@username): {chk(s['clean_tags'])}", callback_data="toggle_txt_tags")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")]
    ])

def kb_signature_menu(has_sig: bool):
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить / Установить подпись", callback_data="edit_signature_action")]
    ]
    if has_sig:
        buttons.append([InlineKeyboardButton(text="🗑 Удалить текущую подпись", callback_data="clear_signature_action")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_back_only():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="open_main_menu")]
    ])

# ══════════════════════════════════════════════════════════
#               ОБРАБОТЧИКИ МЕНЮ (AIOGRAM)
# ══════════════════════════════════════════════════════════
@dp.message(CommandStart())
async def cmd_start_handler(message: Message, state: FSMContext):
    await state.clear()
    s = await get_settings()
    text = (
        "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ АВТОГРАББЕРОМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Приёмник:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Всего скопировано:</b> <code>{s['total_posts']}</code> постов\n"
        f"• <b>Модуль перехвата:</b> <code>Telethon Engine v1.34</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>Система перехватывает медиафайлы любого формата в реальном времени без необходимости админки в донорах.</blockquote>"
    )
    await message.answer(text, reply_markup=kb_main_menu(s), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "open_main_menu")
async def cb_back_to_main(query: CallbackQuery, state: FSMContext):
    await state.clear()
    s = await get_settings()
    text = (
        "👑 <b>ГЛАВНОЕ МЕНЮ СИСТЕМЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Приёмник:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Всего скопировано:</b> <code>{s['total_posts']}</code> постов\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите интересующий раздел настроек ниже:"
    )
    await query.message.edit_text(text, reply_markup=kb_main_menu(s), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "toggle_system_status")
async def cb_toggle_status(query: CallbackQuery):
    s = await get_settings()
    new_val = 0 if s["is_active"] else 1
    await update_setting("is_active", new_val)
    s = await get_settings()
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(s))
    await query.answer("Статус системы обновлен!")

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
        text += f"Подключено каналов: <b>{len(donors)}</b>\n<i>Нажмите на канал в списке, чтобы удалить:</i>"
    
    await query.message.edit_text(text, reply_markup=kb_donors_list(donors), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "add_donor_action")
async def cb_add_donor_prompt(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_donor)
    text = (
        "📥 <b>ДОБАВЛЕНИЕ НОВОГО ДОНОРА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте боту <b>один из вариантов:</b>\n"
        "1. Юзернейм: <code>@channel_name</code> или <code>channel_name</code>\n"
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
            f"✅ <b>Донор успешно подключен!</b>\n\n"
            f"• <b>Название:</b> <code>{title}</code>\n"
            f"• <b>ID:</b> <code>{channel_id}</code>",
            reply_markup=kb_donors_list(donors),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Не удалось найти канал:</b> <code>{e}</code>\n\n"
            "<i>Убедитесь, что ваш аккаунт подписан на этот канал и ссылка введена верно.</i>",
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
    await query.message.edit_text(
        "🗑 <b>Канал-донор успешно удален из отслеживания!</b>",
        reply_markup=kb_donors_list(donors),
        parse_mode=ParseMode.HTML
    )
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
        "Отправьте юзернейм (<code>@my_channel</code>) или ссылку (<code>https://t.me/my_channel</code>).\n\n"
        "<blockquote>⚠️ <b>Требование:</b> Ваш аккаунт и бот должны иметь права администратора на публикацию постов в этом канале.</blockquote>"
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
        await message.answer(
            f"✅ <b>Канал публикации привязан!</b>\n\n"
            f"• <b>Название:</b> <code>{title}</code>\n"
            f"• <b>Цель:</b> <code>{raw_target}</code>",
            reply_markup=kb_main_menu(s),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка подключения к каналу:</b> <code>{e}</code>\n\n"
            "<i>Проверьте права вашего аккаунта в канале назначения.</i>",
            reply_markup=kb_back_only(),
            parse_mode=ParseMode.HTML
        )

# --- ФИЛЬТРЫ МЕДИА И ТЕКСТА ---
@dp.callback_query(F.data == "menu_media_filters")
async def cb_media_filters(query: CallbackQuery):
    s = await get_settings()
    text = (
        "🎛 <b>ФИЛЬТРЫ ТИПОВ КОНТЕНТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Вы можете отключить пересылку определенных видов медиафайлов:"
    )
    await query.message.edit_text(text, reply_markup=kb_media_filters(s), parse_mode=ParseMode.HTML)
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
    text = (
        "🧹 <b>ОЧИСТКА И ФИЛЬТРАЦИЯ ТЕКСТА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Настройте автоудаление чужих рекламных ссылок и упоминаний:"
    )
    await query.message.edit_text(text, reply_markup=kb_text_filters(s), parse_mode=ParseMode.HTML)
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

# --- КАСТОМНАЯ ПОДПИСЬ ---
@dp.callback_query(F.data == "menu_signature")
async def cb_menu_signature(query: CallbackQuery):
    s = await get_settings()
    sig = s["custom_signature"]
    text = (
        "✍️ <b>ФИРМЕННАЯ ПОДПИСЬ К ПОСТАМ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if sig:
        text += f"Текущая подпись:\n<blockquote>{sig}</blockquote>"
    else:
        text += "<i>Подпись не задана. Посты публикуются без изменений.</i>"
    
    await query.message.edit_text(text, reply_markup=kb_signature_menu(bool(sig)), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.callback_query(F.data == "edit_signature_action")
async def cb_edit_signature(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_signature)
    text = (
        "✍️ <b>ВВЕДИТЕ ВАШУ ПОДПИСЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте текст или ссылку, которая будет автоматически добавляться в конец каждого поста.\n\n"
        "<i>Пример:</i> <code>Подписывайся на @TelegaCosplay 🔥</code>"
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

@dp.message(SetupState.waiting_for_signature)
async def process_sig_save(message: Message, state: FSMContext):
    sig = message.text.strip()
    await update_setting("custom_signature", sig)
    await state.clear()
    s = await get_settings()
    await message.answer("✅ <b>Фирменная подпись успешно сохранена!</b>", reply_markup=kb_main_menu(s), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "clear_signature_action")
async def cb_clear_sig(query: CallbackQuery):
    await update_setting("custom_signature", "")
    s = await get_settings()
    await query.message.edit_text("🗑 <b>Подпись полностью удалена.</b>", reply_markup=kb_signature_menu(False), parse_mode=ParseMode.HTML)
    await query.answer()

# --- СТАТИСТИКА ---
@dp.callback_query(F.data == "menu_stats")
async def cb_stats(query: CallbackQuery):
    s = await get_settings()
    donors = await get_donors()
    text = (
        "📊 <b>СТАТИСТИКА И ДИАГНОСТИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Всего переслано постов:</b> <code>{s['total_posts']}</code>\n"
        f"• <b>Активных доноров:</b> <code>{len(donors)}</code>\n"
        f"• <b>Канал назначения:</b> <code>{s['target_title']}</code>\n"
        f"• <b>Состояние:</b> {'🟢 Активен' if s['is_active'] else '⏸ Приостановлен'}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await query.message.edit_text(text, reply_markup=kb_back_only(), parse_mode=ParseMode.HTML)
    await query.answer()

# ══════════════════════════════════════════════════════════
#         ПЕРЕХВАТ И ПЕРЕСЫЛКА ВСЕХ ТИПОВ КОНТЕНТА
# ══════════════════════════════════════════════════════════
@client.on(events.NewMessage)
async def handle_new_post(event):
    try:
        s = await get_settings()
        if not s or not s["is_active"] or not s["target_chat"]:
            return

        chat = await event.get_chat()
        chat_id = chat.id

        donors = await get_donors()
        donor_ids = [d["channel_id"] for d in donors]

        if chat_id not in donor_ids and getattr(chat, 'broadcast', False) is False:
            return

        if chat_id not in donor_ids:
            return

        # Защита от дублей
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM history WHERE donor_id = ? AND post_id = ?", (chat_id, event.id)) as c:
                if await c.fetchone():
                    return
            await db.execute("INSERT INTO history (donor_id, post_id) VALUES (?, ?)", (chat_id, event.id))
            await db.commit()

        # Проверка разрешенных типов медиа
        msg = event.message
        if msg.media:
            # Фото
            if isinstance(msg.media, MessageMediaPhoto) and not s["allow_photos"]:
                return
            # Документы, Видео, Аудио, Голосовые
            elif isinstance(msg.media, MessageMediaDocument):
                doc = msg.media.document
                is_audio = any(isinstance(a, DocumentAttributeAudio) and not a.voice for a in doc.attributes)
                is_voice = any(isinstance(a, DocumentAttributeAudio) and a.voice for a in doc.attributes)
                is_video = any(isinstance(a, DocumentAttributeVideo) for a in doc.attributes)
                
                if is_audio and not s["allow_audio"]:
                    return
                if is_voice and not s["allow_voice"]:
                    return
                if is_video and not s["allow_videos"]:
                    return
                if not (is_audio or is_voice or is_video) and not s["allow_docs"]:
                    return

        # Обработка текста
        clean_text = process_text_content(event.raw_text, s)
        target = parse_target_input(s["target_chat"])

        # Отправка контента
        if msg.media:
            await client.send_message(target, clean_text, file=msg.media)
        else:
            if clean_text:
                await client.send_message(target, clean_text)

        await increment_counter()
        logging.info(f"Успешно переслан контент #{event.id} из {getattr(chat, 'title', chat_id)} в {target}")
    except Exception as e:
        logging.error(f"Ошибка при обработке поста #{event.id}: {e}")

# ══════════════════════════════════════════════════════════
#                     ТОЧКА ЗАПУСКА
# ══════════════════════════════════════════════════════════
async def main():
    await init_db()
    logging.info("База данных инициализирована.")
    await client.start()
    logging.info("Telethon клиент запущен и авторизован.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
