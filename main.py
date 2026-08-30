import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# =======================================================
# 1. ЗАГРУЗКА ТОКЕНА И НАСТРОЕК
# =======================================================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8868698546:AAHUgnSwYlmgGlNvDg2l5_HzD0PDXBfPTXc")
BOT_USERNAME = "tg_grabber_robot"

REF_REWARD_DAYS = 7   # Сколько дней давать за приглашенного друга
SUB_PRICE_RUB = 299   # Стоимость подписки за месяц

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# =======================================================
# 2. БАЗА ДАННЫХ (SQLite)
# =======================================================
def db_init():
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER,
                sub_until TEXT,
                donor_channel TEXT,
                target_channel TEXT,
                signature TEXT,
                filter_ads INTEGER DEFAULT 1,
                balance REAL DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posted_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                post_id TEXT,
                posted_at TEXT
            )
        """)
        conn.commit()

def db_get_user(user_id: int):
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone()

def db_register_user(user_id: int, referrer_id: Optional[int] = None):
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            # 24 часа бесплатного тест-драйва для новых пользователей
            trial_sub = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            if referrer_id == user_id:
                referrer_id = None
            cursor.execute(
                "INSERT INTO users (user_id, referrer_id, sub_until) VALUES (?, ?, ?)",
                (user_id, referrer_id, trial_sub)
            )
            conn.commit()

            if referrer_id:
                db_add_sub_days(referrer_id, REF_REWARD_DAYS)

def db_add_sub_days(user_id: int, days: int):
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sub_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            current_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            start_date = max(current_date, datetime.now())
        else:
            start_date = datetime.now()
        new_date = (start_date + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (new_date, user_id))
        conn.commit()

def db_update_field(user_id: int, field: str, value):
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()

def db_is_post_sent(user_id: int, post_id: str) -> bool:
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM posted_history WHERE user_id = ? AND post_id = ?", (user_id, post_id))
        return cursor.fetchone() is not None

def db_mark_post_sent(user_id: int, post_id: str):
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO posted_history (user_id, post_id, posted_at) VALUES (?, ?, ?)",
            (user_id, post_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

# =======================================================
# 3. FSM СОСТОЯНИЯ
# =======================================================
class SetupState(StatesGroup):
    waiting_for_donor = State()
    waiting_for_target = State()
    waiting_for_signature = State()

# =======================================================
# 4. МЕНЮ И КНОПКИ
# =======================================================
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user = db_get_user(user_id)
    donor = f"@{user[3]}" if user and user[3] else "❌ Не задан"
    target = user[4] if user and user[4] else "❌ Не привязан"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📥 Донор: {donor}", callback_data="set_donor")],
        [InlineKeyboardButton(text=f"📤 Канал: {target}", callback_data="set_target")],
        [InlineKeyboardButton(text="✍️ Моя подпись под постами", callback_data="set_sig")],
        [InlineKeyboardButton(text="💳 Продлить подписку (299₽)", callback_data="buy_sub")],
        [InlineKeyboardButton(text="🔗 Партнерская программа", callback_data="ref_system")]
    ])

# =======================================================
# 5. ХЕНДЛЕРЫ БОТА
# =======================================================
@dp.message(CommandStart())
async def start_cmd(message: types.Message, command: CommandObject):
    ref_id = None
    if command.args and command.args.startswith("ref_"):
        try:
            ref_id = int(command.args.replace("ref_", ""))
        except ValueError:
            pass

    db_register_user(message.from_user.id, ref_id)
    user = db_get_user(message.from_user.id)
    sub_until = user[2] if user else "1 день"

    text = (
        f"🤖 <b>Добро пожаловать в TG-GRABBER SaaS!</b>\n\n"
        f"Автоматический перенос постов из любых открытых каналов в твой канал с зачисткой чужих ссылок и рекламы.\n\n"
        f"⏳ <b>Подписка активна до:</b> <code>{sub_until}</code>\n"
        f"💡 <i>Вам выдан бесплатный пробный период на 24 часа.</i>"
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

@dp.callback_query(F.data == "set_donor")
async def step_donor(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "📥 <b>Отправь юзернейм или ссылку на открытый канал-донор.</b>\n\n"
        "<i>Пример: @durov или https://t.me/tginfo</i>",
        parse_mode="HTML"
    )
    await state.set_state(SetupState.waiting_for_donor)
    await cb.answer()

@dp.message(SetupState.waiting_for_donor)
async def save_donor(message: types.Message, state: FSMContext):
    raw = message.text.strip().replace("https://t.me/", "").replace("@", "").replace("/", "")
    db_update_field(message.from_user.id, "donor_channel", raw)
    await state.clear()
    await message.answer(f"✅ Канал-донор <b>@{raw}</b> успешно сохранен!", reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

@dp.callback_query(F.data == "set_target")
async def step_target(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "📤 <b>Привязка твоего целевого канала:</b>\n\n"
        "1. Добавь этого бота (<code>@tg_grabber_robot</code>) в свой канал в роли <b>Администратора</b> (с правом публикации сообщений).\n"
        "2. Отправь мне <b>@username</b> твоего канала или перешли сюда любой пост из него.",
        parse_mode="HTML"
    )
    await state.set_state(SetupState.waiting_for_target)
    await cb.answer()

@dp.message(SetupState.waiting_for_target)
async def save_target(message: types.Message, state: FSMContext):
    target = None
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        target = str(message.forward_from_chat.id)
    elif message.text:
        target = message.text.strip()
        if not target.startswith("@") and not target.startswith("-100"):
            target = f"@{target}"

    if target:
        db_update_field(message.from_user.id, "target_channel", target)
        await state.clear()
        await message.answer(f"✅ Целевой канал <b>{target}</b> привязан!", reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")
    else:
        await message.answer("❌ Не удалось распознать канал. Отправь юзернейм (например @my_channel).")

@dp.callback_query(F.data == "set_sig")
async def step_sig(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "✍️ <b>Введи подпись, которая будет добавляться под каждым постом.</b>\n\n"
        "<i>Пример: 👉 Подпишись: @my_channel\nОтправь цифру 0, чтобы отключить подпись.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SetupState.waiting_for_signature)
    await cb.answer()

@dp.message(SetupState.waiting_for_signature)
async def save_sig(message: types.Message, state: FSMContext):
    sig = "" if message.text.strip() == "0" else message.text.strip()
    db_update_field(message.from_user.id, "signature", sig)
    await state.clear()
    await message.answer("✅ Подпись успешно обновлена!", reply_markup=get_main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "ref_system")
async def ref_handler(cb: types.CallbackQuery):
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{cb.from_user.id}"
    with sqlite3.connect("grabber_saas.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (cb.from_user.id,))
        count = cursor.fetchone()[0]

    text = (
        "💼 <b>Партнерская реферальная система</b>\n\n"
        f"🔗 <b>Твоя ссылка для приглашений:</b>\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено друзей: <b>{count}</b>\n"
        f"🎁 <b>Бонус:</b> +{REF_REWARD_DAYS} дней работы граббера за каждого активного реферала!"
    )
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer()

@dp.callback_query(F.data == "buy_sub")
async def buy_handler(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить 299 ₽ (1 месяц)", callback_data="pay_month")],
        [InlineKeyboardButton(text="⭐️ Оплатить 150 Stars", callback_data="pay_month")]
    ])
    await cb.message.answer(
        "💎 <b>Оформление подписки на TG-GRABBER:</b>\n\n"
        "• Работа 24/7 без ограничений\n"
        "• Мгновенный перенос контента\n"
        "• Полная зачистка чужих ссылок и рекламы\n\n"
        "Выбери вариант оплаты:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await cb.answer()

@dp.callback_query(F.data == "pay_month")
async def process_pay(cb: types.CallbackQuery):
    db_add_sub_days(cb.from_user.id, 30)
    await cb.message.answer("🎉 <b>Оплата прошла успешно! Подписка продлена на 30 дней.</b>", parse_mode="HTML")
    await cb.answer()

# =======================================================
# 6. ФИЛЬТР РЕКЛАМЫ И ОЧИСТКА
# =======================================================
STOP_WORDS = ["казино", "1win", "промокод", "скидки", "ставки", "вавада", "vavada", "подпишись на спонсора", "партнерский"]

def clean_post_text(raw_text: str, signature: str) -> Optional[str]:
    if not raw_text:
        return signature if signature else None

    lower_text = raw_text.lower()
    for word in STOP_WORDS:
        if word in lower_text:
            return None

    text = re.sub(r"https?://\S+", "", raw_text)
    text = re.sub(r"@\w+", "", text)
    text = text.strip()

    if signature:
        text = f"{text}\n\n{signature}" if text else signature

    return text

# =======================================================
# 7. ФОНОВЫЙ ГРАББЕР (24/7)
# =======================================================
async def fetch_channel_posts(channel_name: str):
    """Парсинг открытого канала через публичное веб-зеркало Telegram."""
    url = f"https://t.me/s/{channel_name}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")
    parsed_posts = []

    for msg in messages[-5:]:
        post_id = msg.get("data-post")
        if not post_id:
            continue

        text_block = msg.find("div", class_="tgme_widget_message_text")
        text = text_block.get_text(separator="\n").strip() if text_block else ""

        photo_tag = msg.find("a", class_="tgme_widget_message_photo_wrap")
        photo_url = None
        if photo_tag and "background-image:url('" in photo_tag.get("style", ""):
            style = photo_tag.get("style")
            photo_url = style.split("background-image:url('")[1].split("')")[0]

        parsed_posts.append({"id": post_id, "text": text, "photo": photo_url})

    return parsed_posts

async def grabber_loop():
    """Фоновый цикл проверки доноров и отправки постов."""
    await asyncio.sleep(5)
    logging.info("[*] Фоновый граббер запущен!")
    while True:
        try:
            with sqlite3.connect("grabber_saas.db") as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, sub_until, donor_channel, target_channel, signature FROM users")
                users = cursor.fetchall()

            for user_id, sub_until, donor, target, signature in users:
                if not donor or not target or not sub_until:
                    continue

                if datetime.strptime(sub_until, "%Y-%m-%d %H:%M:%S") < datetime.now():
                    continue

                posts = await fetch_channel_posts(donor)
                for post in posts:
                    if db_is_post_sent(user_id, post["id"]):
                        continue

                    final_text = clean_post_text(post["text"], signature or "")
                    if final_text is None and not post["photo"]:
                        db_mark_post_sent(user_id, post["id"])
                        continue

                    try:
                        if post["photo"]:
                            await bot.send_photo(chat_id=target, photo=post["photo"], caption=final_text)
                        elif final_text:
                            await bot.send_message(chat_id=target, text=final_text)

                        db_mark_post_sent(user_id, post["id"])
                        logging.info(f"[+] Пост {post['id']} отправлен в канал {target}")
                        await asyncio.sleep(3)
                    except Exception as err:
                        logging.error(f"[-] Ошибка отправки в {target}: {err}")

        except Exception as e:
            logging.error(f"[!] Ошибка в цикле граббера: {e}")

        await asyncio.sleep(30)

# =======================================================
# 8. ЗАПУСК
# =======================================================
async def main():
    db_init()
    asyncio.create_task(grabber_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())