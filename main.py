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

# --- КОНФИГУРАЦИЯ ---
API_ID = 26695279
API_HASH = "13ad7ad5407f42fa50bcce8012d59065"
SESSION_STRING = "1ApWapzMBu66oSs3Y6tA4IZDdXfS2kLUQrjwd1rFa0r0KWwM2_rS-p0d6Ax8Ap4yMYNN2lYG74EGMGo4FQc3uhxBRQeB6wyfd4jqNcwBsbzh4uisdVs-ALrpxYF60OTZvzh1JVJaLrBlXAykX8jGoVQctfYB7g5GsNJ-Mc4Icuarzl7jnyleqMxxNgMycjxOX3FmnfIQ_tlYfDV1jlLemfv725Eu7fZBGGXbh-5o-EOmH7t3WvmjCNUXMI_SkgCBtEHTUEh-yUlI6f8TKxwmwGi-AyEYGK3YOQmb76ud3_BbKBS6Z93iR6ZiwmbgBnuaAHS-2fbO3d2TVbdNTYo3yQpvjZNdRr04="
BOT_TOKEN = "8868698546:AAHUgnSwYlmgGlNvDg2l5_HzD0PDXBfPTXc"
DB_NAME = "grabber.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Инициализация клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

class SetupState(StatesGroup):
    waiting_for_donor = State()
    waiting_for_target = State()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                target_chat TEXT,
                is_active INTEGER DEFAULT 1,
                clean_links INTEGER DEFAULT 1,
                clean_tags INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS donors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER UNIQUE,
                title TEXT
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
            INSERT OR IGNORE INTO settings (id, target_chat, is_active, clean_links, clean_tags)
            VALUES (1, '', 1, 1, 1)
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

async def get_donors():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM donors") as cursor:
            return await cursor.fetchall()

# --- КЛАВИАТУРЫ ---
def kb_main_menu(is_active: bool):
    status_emoji = "🟢 РАБОТАЕТ" if is_active else "🔴 НА ПАУЗЕ"
    toggle_text = "⏸ Поставить на паузу" if is_active else "▶️ Запустить пересылку"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {status_emoji}", callback_data="none")],
        [InlineKeyboardButton(text=toggle_text, callback_data="toggle_status")],
        [
            InlineKeyboardButton(text="📥 Доноры", callback_data="menu_donors"),
            InlineKeyboardButton(text="📤 Канал публикации", callback_data="menu_target")
        ],
        [InlineKeyboardButton(text="⚙️ Фильтры и очистка", callback_data="menu_filters")]
    ])

def kb_donors_menu(donors_list):
    buttons = []
    for d in donors_list:
        buttons.append([
            InlineKeyboardButton(text=f"🗑 {d['title']}", callback_data=f"del_donor_{d['channel_id']}")
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить донора", callback_data="add_donor")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_filters_menu(clean_links: bool, clean_tags: bool):
    links_text = "✅ Удалять ссылки (http, t.me)" if clean_links else "❌ Удалять ссылки (выкл)"
    tags_text = "✅ Удалять теги (@username)" if clean_tags else "❌ Удалять теги (выкл)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=links_text, callback_data="toggle_clean_links")],
        [InlineKeyboardButton(text=tags_text, callback_data="toggle_clean_tags")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="main_menu")]
    ])

def kb_back_to_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="main_menu")]
    ])

# --- ОЧИСТКА ТЕКСТА ---
def clean_post_text(text: str, clean_links: bool, clean_tags: bool) -> str:
    if not text:
        return ""
    if clean_links:
        text = re.sub(r'https?://\S+|t\.me/\S+', '', text)
    if clean_tags:
        text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
    return text.strip()

# --- ОБРАБОТЧИКИ AIOGRAM ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    settings = await get_settings()
    await message.answer(
        "👋 **Панель управления автограббером**\n\n"
        "Бот перехватывает новые посты из каналов-доноров и автоматически публикует в твою целевую группу.",
        reply_markup=kb_main_menu(bool(settings["is_active"])),
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(query: CallbackQuery, state: FSMContext):
    await state.clear()
    settings = await get_settings()
    await query.message.edit_text(
        "👋 **Панель управления автограббером**\n\n"
        "Выбери нужный раздел настроек ниже:",
        reply_markup=kb_main_menu(bool(settings["is_active"])),
        parse_mode=ParseMode.MARKDOWN
    )
    await query.answer()

@dp.callback_query(F.data == "toggle_status")
async def cb_toggle_status(query: CallbackQuery):
    settings = await get_settings()
    new_status = 0 if settings["is_active"] else 1
    await update_setting("is_active", new_status)
    await query.message.edit_reply_markup(reply_markup=kb_main_menu(bool(new_status)))
    await query.answer()

@dp.callback_query(F.data == "menu_donors")
async def cb_menu_donors(query: CallbackQuery):
    donors = await get_donors()
    text = "📥 **Список каналов-доноров:**\n\n"
    if not donors:
        text += "Каналы ещё не добавлены. Нажми «➕ Добавить донора»."
    else:
        text += "Нажми на канал, чтобы удалить его из списка отслеживания:"
    await query.message.edit_text(text, reply_markup=kb_donors_menu(donors), parse_mode=ParseMode.MARKDOWN)
    await query.answer()

@dp.callback_query(F.data == "add_donor")
async def cb_add_donor(query: CallbackQuery, state: FSMContext):
    await state.set_state(SetupState.waiting_for_donor)
    await query.message.edit_text(
        "📥 **Отправь юзернейм или ссылку на канал-донор**\n\n"
        "Примеры:\n"
        "• `@channel_name`\n"
        "• `https://t.me/joinchat/...` (для приватных каналов)\n"
        "• Перешли любое сообщение из этого канала сюда.",
        reply_markup=kb_back_to_menu(),
        parse_mode=ParseMode.MARKDOWN
    )
    await query.answer()

@dp.message(SetupState.waiting_for_donor)
async def process_donor_input(message: Message, state: FSMContext):
    input_data = message.forward_from_chat.id if message.forward_from_chat else message.text.strip()
    try:
        entity = await client.get_entity(input_data)
        title = getattr(entity, 'title', str(input_data))
        channel_id = entity.id

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT OR REPLACE INTO donors (channel_id, title) VALUES (?, ?)",
                (channel_id, title)
            )
            await db.commit()

        await state.clear()
        donors = await get_donors()
        await message.answer(
            f"✅ Канал **{title}** успешно добавлен в доноры!",
            reply_markup=kb_donors_menu(donors),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось найти канал: `{e}`\n"
            "Убедись, что твой Telegram-аккаунт подписан на этот канал.",
            reply_markup=kb_back_to_menu(),
            parse_mode=ParseMode.MARKDOWN
        )

@dp.callback_query(F.data.startswith("del_donor_"))
async def cb_del_donor(query: CallbackQuery):
    channel_id = int(query.data.replace("del_donor_", ""))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM donors WHERE channel_id = ?", (channel_id,))
        await db.commit()
    donors = await get_donors()
    await query.message.edit_text(
        "🗑 Донор успешно удален.",
        reply_markup=kb_donors_menu(donors)
    )
    await query.answer()

@dp.callback_query(F.data == "menu_target")
async def cb_menu_target(query: CallbackQuery, state: FSMContext):
    settings = await get_settings()
    current = settings["target_chat"] if settings["target_chat"] else "Не установлен"
    await state.set_state(SetupState.waiting_for_target)
    await query.message.edit_text(
        f"📤 **Канал для публикации постов**\n\n"
        f"Текущий получатель: `{current}`\n\n"
        "Отправь сюда **@username** или **ID** (например `-1001234567890`) твоего канала/группы.\n"
        "*(Убедись, что твой аккаунт или бот состоит в этом канале с правами публикации)*",
        reply_markup=kb_back_to_menu(),
        parse_mode=ParseMode.MARKDOWN
    )
    await query.answer()

@dp.message(SetupState.waiting_for_target)
async def process_target_input(message: Message, state: FSMContext):
    target = message.text.strip()
    await update_setting("target_chat", target)
    await state.clear()
    settings = await get_settings()
    await message.answer(
        f"✅ Канал публикации успешно обновлен на `{target}`!",
        reply_markup=kb_main_menu(bool(settings["is_active"])),
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "menu_filters")
async def cb_menu_filters(query: CallbackQuery):
    settings = await get_settings()
    await query.message.edit_text(
        "⚙️ **Настройки очистки рекламы и ссылок:**",
        reply_markup=kb_filters_menu(bool(settings["clean_links"]), bool(settings["clean_tags"]))
    )
    await query.answer()

@dp.callback_query(F.data.in_(["toggle_clean_links", "toggle_clean_tags"]))
async def cb_toggle_filters(query: CallbackQuery):
    settings = await get_settings()
    if query.data == "toggle_clean_links":
        new_val = 0 if settings["clean_links"] else 1
        await update_setting("clean_links", new_val)
    else:
        new_val = 0 if settings["clean_tags"] else 1
        await update_setting("clean_tags", new_val)
    settings = await get_settings()
    await query.message.edit_reply_markup(
        reply_markup=kb_filters_menu(bool(settings["clean_links"]), bool(settings["clean_tags"]))
    )
    await query.answer()

# --- ПЕРЕХВАТ И ПЕРЕСЫЛКА ЧЕРЕЗ TELETHON ---
@client.on(events.NewMessage)
async def telethon_grabber_handler(event):
    try:
        settings = await get_settings()
        if not settings or not settings["is_active"] or not settings["target_chat"]:
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
            async with db.execute(
                "SELECT 1 FROM history WHERE donor_id = ? AND post_id = ?",
                (chat_id, event.id)
            ) as cursor:
                if await cursor.fetchone():
                    return

            await db.execute(
                "INSERT INTO history (donor_id, post_id) VALUES (?, ?)",
                (chat_id, event.id)
            )
            await db.commit()

        clean_text = clean_post_text(
            event.raw_text,
            bool(settings["clean_links"]),
            bool(settings["clean_tags"])
        )

        target = settings["target_chat"]
        if target.startswith("-100") or target.startswith("-"):
            target = int(target)

        if event.message.media:
            await client.send_message(target, clean_text, file=event.message.media)
        else:
            if clean_text:
                await client.send_message(target, clean_text)

        logging.info(f"Успешно переслан пост {event.id} из {chat.title} в {target}")
    except Exception as err:
        logging.error(f"Ошибка при граббинге поста: {err}")

# --- ТОЧКА ВХОДА ---
async def main():
    await init_db()
    logging.info("База данных готова.")
    await client.start()
    logging.info("Telethon юзербот успешно авторизован.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
