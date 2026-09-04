import asyncio
import logging
import json
import os
import re
import html as html_module
import urllib.parse
from datetime import datetime, timedelta

# --- ЖЕСТКАЯ ПРИВЯЗКА К ПАПКЕ ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, InputMediaPhoto, LabeledPrice, PreCheckoutQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.exceptions import TelegramBadRequest, TelegramConflictError, TelegramUnauthorizedError
from redis.asyncio import Redis

# --- УМНАЯ ЗАГРУЗКА ТОКЕНОВ ИЗ СЕЙФА ---
# Новые переменные: добавьте их в файл .env рядом с botrunes.py, затем прочитайте через env().
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

def env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip().replace('"', "").replace("'", "") if value else default

def env_int(name: str, default: int) -> int:
    raw = env(name, str(default))
    if raw.isdigit():
        return int(raw)
    return default

raw_token = env("BOT_TOKEN")
if not raw_token or "замените" in raw_token:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: Бот не видит токен! Проверьте BOT_TOKEN в файле .env")
BOT_TOKEN = raw_token

PAYMENT_TOKEN = env("PAYMENT_TOKEN")
admin_raw = env("ADMIN_ID")
if admin_raw.startswith(("live_", "test_")):
    if not PAYMENT_TOKEN or "замените" in PAYMENT_TOKEN:
        PAYMENT_TOKEN = admin_raw
    admin_raw = ""
if PAYMENT_TOKEN.startswith("замените"):
    PAYMENT_TOKEN = ""

REDIS_HOST = env("REDIS_HOST", "localhost") or "localhost"
REDIS_PORT = env_int("REDIS_PORT", 6379)

MY_ID = env_int("ADMIN_ID", 297967650)
if admin_raw.isdigit():
    MY_ID = int(admin_raw)

redis = Redis(host=REDIS_HOST, port=REDIS_PORT)
storage = RedisStorage(redis=redis)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

from ritual_data import (
    AMINO_ACIDS,
    BASE_MAP,
    RUNE_IMAGES,
    days_left_from,
    ensure_user_record,
    find_rune_image,
    format_access_status,
    get_greeting_text,
    load_db,
    parse_ritual_admin_args,
    restore_user_access,
    ritual_unlimited,
    save_db,
    schedule_next_ritual,
    set_ritual_mode,
)

FILE_IDS_PATH = "file_ids.json"
_file_ids_cache = None
_bot_username = None

def md_to_html(text: str) -> str:
    if not text:
        return text
    converted = html_module.escape(text, quote=False)
    converted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", converted)
    converted = re.sub(r"`([^`]+)`", r"<code>\1</code>", converted)
    converted = re.sub(r"(?<![a-zA-Z0-9])_([^_]+?)_(?![a-zA-Z0-9])", r"<i>\1</i>", converted)
    return converted

async def send_html(method, *args, **kwargs):
    original_text = kwargs.get("text")
    original_caption = kwargs.get("caption")
    if original_text:
        kwargs["text"] = md_to_html(original_text)
    if original_caption:
        kwargs["caption"] = md_to_html(original_caption)
    kwargs["parse_mode"] = "HTML"
    try:
        return await method(*args, **kwargs)
    except TelegramBadRequest as e:
        if "parse" not in str(e).lower() and "entities" not in str(e).lower():
            raise
        if original_text is not None:
            kwargs["text"] = original_text
        if original_caption is not None:
            kwargs["caption"] = original_caption
        kwargs.pop("parse_mode", None)
        return await method(*args, **kwargs)

def load_file_ids() -> dict:
    global _file_ids_cache
    if _file_ids_cache is None:
        if os.path.exists(FILE_IDS_PATH):
            with open(FILE_IDS_PATH, "r") as f:
                _file_ids_cache = json.load(f)
        else:
            _file_ids_cache = {}
    return _file_ids_cache

def remember_file_id(path: str, file_id: str):
    ids = load_file_ids()
    if ids.get(path) != file_id:
        ids[path] = file_id
        with open(FILE_IDS_PATH, "w") as f:
            json.dump(ids, f)

def media_ref(path: str):
    file_id = load_file_ids().get(path)
    return file_id if file_id else FSInputFile(path)

async def cached_bot_username() -> str:
    global _bot_username
    if not _bot_username:
        me = await bot.get_me()
        _bot_username = me.username
    return _bot_username

async def send_cached_animation(message: Message, path: str, **kwargs):
    msg = await send_html(message.answer_animation, animation=media_ref(path), **kwargs)
    fid = (msg.animation.file_id if msg.animation else None) or (msg.video.file_id if msg.video else None)
    if fid:
        remember_file_id(path, fid)
    return msg

async def send_cached_photo(chat_id: int, path: str, **kwargs):
    msg = await send_html(bot.send_photo, chat_id=chat_id, photo=media_ref(path), **kwargs)
    if msg.photo:
        remember_file_id(path, msg.photo[-1].file_id)
    return msg

class Ritual(StatesGroup):
    waiting_for_blue = State()
    waiting_for_green = State()
    waiting_for_red = State()
    waiting_for_rune_choice = State()
    waiting_for_carousel = State()
    waiting_for_payment = State()

def get_main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Об авторе", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/author.html"))],
        [InlineKeyboardButton(text="📜 История метода", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/method.html"))],
        [InlineKeyboardButton(text="🌬️ Буор, Ийэ и Салгын Кут", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/kut.html"))],
        [InlineKeyboardButton(text="🔮 Начать обряд", callback_data="start_ritual")]
    ])

def get_bottom_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔮 Начать обряд"),
                KeyboardButton(text="🕯 Подготовка", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/prep.html"))
            ],
            [
                KeyboardButton(text="💬 Отзывы", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/reviews.html")),
                KeyboardButton(text="🤝 Пригласить друга")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

def make_carousel_kb(current: int, total: int) -> InlineKeyboardMarkup:
    nav_row = []
    if total > 1:
        prev_i = (current - 1) % total
        next_i = (current + 1) % total
        nav_row = [
            InlineKeyboardButton(text="◀", callback_data=f"carousel_{prev_i}"),
            InlineKeyboardButton(text=f"{current+1}/{total}", callback_data="carousel_noop"),
            InlineKeyboardButton(text="▶", callback_data=f"carousel_{next_i}"),
        ]
    return InlineKeyboardMarkup(inline_keyboard=[
        nav_row,
        [InlineKeyboardButton(text=f"✅ Выбрать эту руну", callback_data=f"rune_{current}")]
    ] if nav_row else [
        [InlineKeyboardButton(text=f"✅ Выбрать эту руну", callback_data=f"rune_{current}")]
    ])

@dp.message(Command("whois"))
async def cmd_whois(message: Message, command: CommandObject):
    if message.from_user.id != MY_ID:
        return
    args = (command.args or "").split()
    target_id = args[0] if args else str(message.from_user.id)
    if not target_id.isdigit():
        await send_html(message.answer, text="Использование: `/whois <telegram_id>`")
        return
    db = load_db()
    data = db.get(target_id)
    if not isinstance(data, dict):
        await send_html(message.answer, text=f"Пользователь `{target_id}` не найден в базе.")
        return
    await send_html(message.answer, text=format_access_status(target_id, data))

@dp.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject):
    if message.from_user.id != MY_ID:
        return
    args = (command.args or "").split()
    if len(args) == 1:
        target_id, days_raw = str(message.from_user.id), args[0]
    elif len(args) == 2:
        target_id, days_raw = args[0], args[1]
    else:
        await send_html(
            message.answer,
            text=(
            "Восстановление доступа:\n"
            "`/grant <telegram_id> <дни>`\n"
            "`/grant <дни>` — продлить себе\n\n"
            "Примеры:\n`/grant 123456789 30`\n`/grant 7`"
            ),
        )
        return
    if not target_id.isdigit() or not days_raw.lstrip("-").isdigit():
        await message.answer("ID и количество дней должны быть числами.")
        return
    days = int(days_raw)
    if days < 1 or days > 3650:
        await message.answer("Количество дней должно быть от 1 до 3650.")
        return

    db = load_db()
    data = restore_user_access(db, target_id, days)
    save_db(db)
    status = format_access_status(target_id, data)
    await send_html(message.answer, text=f"🔓 **Доступ восстановлен на {days} дн.**\n\n{status}")

    if int(target_id) != message.from_user.id:
        try:
            left = days_left_from(datetime.fromisoformat(data["trial_end"]), datetime.now())
            await send_html(
                bot.send_message,
                chat_id=int(target_id),
                text=f"🔓 **Ваш доступ восстановлен.**\n\nАктивен ещё **{left}** дн. Напишите /start, чтобы продолжить.",
            )
        except Exception:
            await message.answer("⚠️ Пользователю не удалось отправить уведомление (возможно, он не запускал бота).")

@dp.message(Command("allow_ritual"))
async def cmd_allow_ritual(message: Message, command: CommandObject):
    if message.from_user.id != MY_ID:
        return
    args = (command.args or "").split()
    parsed = parse_ritual_admin_args(args, str(message.from_user.id))
    if not parsed:
        await send_html(
            message.answer,
            text=(
                "Обряд:\n"
                "`/allow_ritual` — сбросить таймер себе\n"
                "`/allow_ritual inf` — бесконечные обряды себе\n"
                "`/allow_ritual off` — снова лимит 12 часов\n"
                "`/allow_ritual <id>` — сбросить таймер человеку\n"
                "`/allow_ritual <id> inf` — бесконечные обряды\n"
                "`/allow_ritual <id> off` — выключить безлимит"
            ),
        )
        return
    target_id, mode = parsed
    db = load_db()
    now = datetime.now()
    data = set_ritual_mode(db, target_id, mode, now)
    save_db(db)
    if mode == "unlimited":
        text = f"🔮 Безлимит обрядов включён для `{target_id}`."
    elif mode == "off":
        text = f"🔮 Безлимит выключен для `{target_id}`. Снова пауза 12 часов."
    else:
        text = f"🔮 Таймер обряда сброшен для `{target_id}`."
    await send_html(message.answer, text=f"{text}\n\n{format_access_status(target_id, data)}")

@dp.message(Command("check_images"))
async def cmd_check_images(message: Message):
    if message.from_user.id != MY_ID: return
    report = "📊 **Отчет по картинкам рун:**\n\n"
    missing = []
    found = 0
    for amino, files in RUNE_IMAGES.items():
        for img in files:
            path = os.path.join("images", "runes", img)
            if not os.path.exists(path):
                if img not in missing: missing.append(img)
            else:
                found += 1
    report += f"✅ Найдено файлов: {found}\n❌ Отсутствует: {len(missing)}\n\n"
    if missing: report += "⚠️ **Не найдены файлы:**\n" + "\n".join(missing)
    else: report += "🎉 **Все картинки на месте!**"
    await send_html(message.answer, text=report)

@dp.message(Command("show_images"))
async def cmd_show_images(message: Message):
    if message.from_user.id != MY_ID: return
    await message.answer("🔍 Начинаю выгрузку всех картинок...\nСравни символ на фото с символом в тексте!")
    for amino, files in RUNE_IMAGES.items():
        for i, img in enumerate(files):
            path = os.path.join("images", "runes", img)
            runes_list = AMINO_ACIDS[amino]["runes"]
            rune_symbol = runes_list[i] if i < len(runes_list) else "?"
            if os.path.exists(path):
                caption = f"🧪 Аминокислота: **{amino}**\n📁 Файл: `{img}`\n🔮 На фото должна быть руна: **{rune_symbol}**"
                try:
                    await send_cached_photo(message.chat.id, path, caption=caption)
                    await asyncio.sleep(0.5)
                except Exception: pass
    await message.answer("✅ Выгрузка завершена!")

# =====================================================================
# === ГЛАВНЫЙ МОДУЛЬ УВЕДОМЛЕНИЙ (САМЫЙ ВАЖНЫЙ БЛОК ДЛЯ МАРКЕТИНГА) ===
# =====================================================================
async def daily_notifier():
    
    # Календарь особых дат (месяц-день)
    SPECIAL_DAYS = {
        "06-21": "☀️ Сегодня день летнего солнцестояния.\nРекомендуется выполнить расклад.",
        "12-21": "❄️ Сегодня день зимнего солнцестояния.\nРекомендуется выполнить расклад.",
        "03-20": "🌱 Сегодня день весеннего равноденствия.\nРекомендуется выполнить расклад.",
        "09-22": "🍁 Сегодня день осеннего равноденствия.\nРекомендуется выполнить расклад."
    }

    while True:
        try:
            db = load_db()
            now = datetime.now()
            today_str = now.strftime("%m-%d")
            changed = False

            for user_id, data in db.items():
                if not str(user_id).isdigit() or not isinstance(data, dict):
                    continue

                try:
                    if "trial_end" not in data:
                        data["trial_end"] = now.isoformat()
                        changed = True
                    if "next_ritual_time" not in data:
                        data["next_ritual_time"] = now.isoformat()
                        changed = True
                    if "notified_12h" not in data:
                        data["notified_12h"] = False
                    if "ritual_step" not in data:
                        data["ritual_step"] = 0
                    if "last_active" not in data:
                        data["last_active"] = now.isoformat()
                    if "notified_incomplete" not in data:
                        data["notified_incomplete"] = False
                    if "notified_inactive" not in data:
                        data["notified_inactive"] = False
                    if "special_day_notified" not in data:
                        data["special_day_notified"] = ""

                    trial_end = datetime.fromisoformat(data["trial_end"])
                    next_ritual = datetime.fromisoformat(data["next_ritual_time"])
                    last_active = datetime.fromisoformat(data["last_active"])

                    time_to_end = trial_end - now
                    days_left = int(time_to_end.total_seconds() / 86400) + (1 if time_to_end.total_seconds() % 86400 > 0 else 0)
                    sub_notified = data.get("notified", 0)

                    if now >= next_ritual and not data["notified_12h"] and not ritual_unlimited(data):
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔮 Начать обряд", callback_data="start_ritual")]])
                        await bot.send_message(
                            chat_id=int(user_id),
                            text="🌬️ Сегодня доступен новый расклад.\n\nПодготовьте палочки и выполните практику.",
                            reply_markup=kb
                        )
                        data["notified_12h"] = True
                        data["notified_inactive"] = False
                        changed = True

                    if data["ritual_step"] > 0 and (now - last_active).total_seconds() > 3600 and not data["notified_incomplete"]:
                        step = data["ritual_step"] - 1
                        await bot.send_message(
                            chat_id=int(user_id),
                            text=f"Вы выполнили только {step} из 3 бросков.\n\nЗавершите сегодняшний расклад."
                        )
                        data["notified_incomplete"] = True
                        changed = True

                    if days_left == 3 and sub_notified < 1:
                        await bot.send_message(int(user_id), "⚠️ Ваша подписка заканчивается через 3 дня.")
                        data["notified"] = 1
                        changed = True
                    elif days_left == 1 and sub_notified < 2:
                        await bot.send_message(int(user_id), "⏳ Остался 1 день доступа.")
                        data["notified"] = 2
                        changed = True
                    elif days_left <= 0 and sub_notified < 3:
                        await bot.send_message(int(user_id), "🔒 Ваша подписка завершена.\nВы можете продлить доступ, пройдя новый обряд.")
                        data["notified"] = 3
                        changed = True

                    if now >= next_ritual + timedelta(days=7) and not data["notified_inactive"]:
                        await bot.send_message(
                            int(user_id),
                            "Вы давно не выполняли расклад.\n\nВозможно, сегодня подходящий день вернуться к практике."
                        )
                        data["notified_inactive"] = True
                        changed = True

                    if today_str in SPECIAL_DAYS and data["special_day_notified"] != today_str:
                        await bot.send_message(int(user_id), SPECIAL_DAYS[today_str])
                        data["special_day_notified"] = today_str
                        changed = True

                except Exception as e:
                    logging.error("Ошибка уведомления пользователю %s: %s", user_id, e)

            if changed:
                save_db(db)
        except Exception:
            logging.exception("Сбой цикла напоминаний")

        await asyncio.sleep(3600)

@dp.message(F.web_app_data)
async def web_app_data_handler(message: Message, state: FSMContext):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("type") == "order_sticks":
            await state.update_data(pending_order=data)
            admin_pending_text = (
                "⏳ **НОВАЯ ЗАЯВКА (Ожидает оплаты)** ⏳\n\n"
                f"👤 **Имя:** {data['fio']}\n"
                f"📞 **Телефон:** {data['phone']}\n"
                f"🚚 **Способ:** {data['delivery']}\n"
                f"📍 **Адрес:** {data['address']}\n"
                f"💵 **Сумма к оплате:** {data['price']} руб.\n\n"
                f"💬 *Клиенту выставлен счет. Ждем поступления средств...*"
            )
            await send_html(bot.send_message, chat_id=MY_ID, text=admin_pending_text)
            price_rub = data.get("price", 400)
            prices = [LabeledPrice(label=f"Набор палочек ({data['delivery']})", amount=price_rub * 100)]
            await bot.send_invoice(
                chat_id=message.chat.id,
                title="Заказ четырехгранных палочек",
                description=f"Оплата инвентаря для обряда.\nСпособ получения: {data['delivery']}.",
                payload="pay_sticks",
                provider_token=PAYMENT_TOKEN,
                currency="RUB",
                prices=prices
            )
    except Exception as e:
        logging.error(f"Ошибка обработки заказа формы: {e}")

@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    db = load_db()
    user_id = str(message.from_user.id)
    if user_id in db: del db[user_id]
    save_db(db)
    await state.clear()
    await message.answer("✅ Твой профиль сброшен. Напиши /start для новых 3-х дней тестов.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text == "🤝 Пригласить друга")
async def referral_menu(message: Message):
    username = await cached_bot_username()
    ref_link = f"https://t.me/{username}?start=ref_{message.from_user.id}"
    
    db = load_db()
    user_id = str(message.from_user.id)
    user_data = db.get(user_id, {})
    refs_count = user_data.get("referrals_count", 0)
    
    text = (
        "🎁 **Реферальная программа**\n\n"
        "Приглашайте друзей и получайте бесплатные дни доступа к расшифровкам обрядов!\n\n"
        "✨ **Ваш бонус:** `+3 дня` за каждого нового участника\n\n"
        f"👥 Вы уже пригласили: **{refs_count} чел.**\n\n"
        "🔗 **Ваша персональная ссылка:**\n"
        f"`{ref_link}`\n\n"
        "_(Нажмите на ссылку, чтобы скопировать и отправьте её друзьям)_"
    )
    await send_html(message.answer, text=text)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    db = load_db()
    user_id = str(message.from_user.id)
    now = datetime.now()
    
    args = command.args
    referrer_id = None
    if args and args.startswith("ref_"):
        referrer_id = args.split("_")[1]

    if user_id not in db or isinstance(db[user_id], str):
        trial_days = 3 
        
        if referrer_id and referrer_id in db and referrer_id != user_id:
            ref_data = db[referrer_id]
            if isinstance(ref_data, dict):
                ref_end = datetime.fromisoformat(ref_data.get("trial_end", now.isoformat()))
                ref_start = ref_end if ref_end > now else now
                db[referrer_id]["trial_end"] = (ref_start + timedelta(days=3)).isoformat()
                db[referrer_id]["referrals_count"] = ref_data.get("referrals_count", 0) + 1
                db[referrer_id]["notified"] = 0 
                
                try:
                    await send_html(
                        bot.send_message,
                        chat_id=int(referrer_id),
                        text="🎉 **По вашей ссылке присоединился новый участник!**\nВам начислено `+3 дня` доступа к боту 🎁",
                    )
                except Exception:
                    pass

        db[user_id] = {
            "trial_end": (now + timedelta(days=trial_days)).isoformat(),
            "next_ritual_time": now.isoformat(),
            "notified": 0,
            "paid": False,
            "referrer": referrer_id if referrer_id else None,
            "referrals_count": 0,
            "ritual_step": 0,
            "last_active": now.isoformat(),
            "notified_incomplete": False,
            "notified_12h": False,
            "notified_inactive": False,
            "special_day_notified": ""
        }
        save_db(db)

    await state.clear()
    caption = get_greeting_text(db[user_id], now)
    
    if os.path.exists("gif1_v2.mp4"):
        await send_cached_animation(message, "gif1_v2.mp4", caption=caption, reply_markup=get_main_menu_kb())
    else:
        await send_html(message.answer, text=caption, reply_markup=get_main_menu_kb())
    await message.answer("👇 Для начала работы используйте меню ниже:", reply_markup=get_bottom_kb())

@dp.message(F.text == "🔮 Начать обряд")
async def start_ritual_text_handler(message: Message, state: FSMContext):
    await process_ritual_start(message, state, str(message.from_user.id))

@dp.callback_query(F.data == "start_ritual")
async def start_ritual_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await process_ritual_start(callback.message, state, str(callback.from_user.id))

async def process_ritual_start(message: Message, state: FSMContext, user_id: str):
    db = load_db()
    user_data = db.get(user_id, {})
    now = datetime.now()
    next_ritual = datetime.fromisoformat(user_data.get("next_ritual_time", now.isoformat()))
    
    if not ritual_unlimited(user_data) and now < next_ritual:
        time_left = next_ritual - now
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        username = await cached_bot_username()
        ref_link = f"https://t.me/{username}?start=ref_{user_id}"
        
        promo_text = (
            f"⏳ Обряд уже проведен! Следующий будет доступен через {hours} ч. {minutes} мин.\n\n"
            "🎁 **Как получить +3 дня доступа бесплатно?**\n"
            "Пригласите друга по вашей ссылке:\n"
            f"`{ref_link}`"
        )
        await send_html(message.answer, text=promo_text)
        return
        
    # ФИКСИРУЕМ СТАРТ ОБРЯДА
    user_data['ritual_step'] = 1
    user_data['last_active'] = now.isoformat()
    user_data['notified_incomplete'] = False
    db[user_id] = user_data
    save_db(db)

    await state.update_data(complex_num=1, final_runes=[], final_aminos=[], final_images=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "Бросай как на примере выше\n\n🔮 **Комплекс 1.** Брось палочки и посмотри на **СИНЮЮ** грань. Сколько точек?"
    if os.path.exists("gif2_v2.mp4"):
        await send_cached_animation(message, "gif2_v2.mp4", caption=caption, reply_markup=kb)
    else:
        await send_html(message.answer, text=caption, reply_markup=kb)
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(blue=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🟢 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "Теперь посмотри на **ЗЕЛЕНУЮ** грань. Сколько точек?"
    if callback.message.animation or callback.message.video or callback.message.photo:
        await send_html(callback.message.edit_caption, caption=caption, reply_markup=kb)
    else:
        await send_html(callback.message.edit_text, text=caption, reply_markup=kb)
    await state.set_state(Ritual.waiting_for_green)

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def proc_green(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(green=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔴 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "Теперь посмотри на **КРАСНУЮ** грань. Сколько точек?"
    if callback.message.animation or callback.message.video or callback.message.photo:
        await send_html(callback.message.edit_caption, caption=caption, reply_markup=kb)
    else:
        await send_html(callback.message.edit_text, text=caption, reply_markup=kb)
    await state.set_state(Ritual.waiting_for_red)

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def proc_red(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    red_val = callback.data.split("_")[1]
    triplet = BASE_MAP[red_val] + BASE_MAP[data['green']] + BASE_MAP[data['blue']]

    amino, runes = "Неизвестно", []
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino, runes = name, a_data["runes"]
            break

    await state.update_data(current_runes=runes, current_amino=amino)
    await callback.message.delete()

    if not runes:
        await bot.send_message(chat_id=callback.message.chat.id, text=f"Триплет {triplet} не найден.")
        return

    if len(runes) == 1:
        await save_rune_and_continue(callback.message, state, runes[0], amino, 0)
        return

    await show_carousel(callback.message.chat.id, state, amino, runes, current=0)
    await state.set_state(Ritual.waiting_for_carousel)

async def show_carousel(chat_id: int, state: FSMContext, amino: str, runes: list, current: int):
    total = len(runes)
    rune_symbol = runes[current]
    img_path = find_rune_image(amino, current)
    kb = make_carousel_kb(current, total)

    await state.update_data(carousel_index=current)
    caption = f"🧬 **{amino}**\n🔮 Руна: **{rune_symbol}**\n\n{current+1} из {total} — листайте ◀ ▶ и выберите нужную"
    html_caption = md_to_html(caption)
    data = await state.get_data()
    carousel_msg_id = data.get("carousel_msg_id")
    carousel_file_ids = data.get("carousel_file_ids", {})

    if img_path:
        file_id = carousel_file_ids.get(str(current)) or load_file_ids().get(img_path)
        if carousel_msg_id and file_id:
            try:
                await bot.edit_message_media(chat_id=chat_id, message_id=carousel_msg_id, media=InputMediaPhoto(media=file_id, caption=html_caption, parse_mode="HTML"), reply_markup=kb)
                return
            except Exception:
                pass
        if carousel_msg_id and not file_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=carousel_msg_id)
            except Exception:
                pass
        msg = await send_cached_photo(chat_id, img_path, caption=caption, reply_markup=kb)
        carousel_file_ids[str(current)] = msg.photo[-1].file_id
        await state.update_data(carousel_msg_id=msg.message_id, carousel_file_ids=carousel_file_ids)
    else:
        if carousel_msg_id:
            try:
                await send_html(bot.edit_message_text, chat_id=chat_id, message_id=carousel_msg_id, text=caption, reply_markup=kb)
                return
            except Exception:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=carousel_msg_id)
                except Exception:
                    pass
        msg = await send_html(bot.send_message, chat_id=chat_id, text=caption, reply_markup=kb)
        await state.update_data(carousel_msg_id=msg.message_id)

@dp.callback_query(Ritual.waiting_for_carousel, F.data.startswith("carousel_"))
async def proc_carousel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split("_")[1]
    if action == "noop": return
    data = await state.get_data()
    await show_carousel(callback.message.chat.id, state, data.get("current_amino", ""), data.get("current_runes", []), current=int(action))

@dp.callback_query(Ritual.waiting_for_carousel, F.data.startswith("rune_"))
async def proc_rune_carousel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    chosen_index = int(callback.data.split("_")[1])
    carousel_msg_id = data.get("carousel_msg_id")
    if carousel_msg_id:
        try: await bot.delete_message(chat_id=callback.message.chat.id, message_id=carousel_msg_id)
        except Exception: pass
    await state.update_data(carousel_msg_id=None, carousel_file_ids={})
    await save_rune_and_continue(callback.message, state, data.get("current_runes", [])[chosen_index], data.get("current_amino", ""), chosen_index)

@dp.callback_query(Ritual.waiting_for_rune_choice, F.data.startswith("rune_"))
async def proc_rune(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await callback.message.delete()
    chosen_index = int(callback.data.split("_")[1])
    await save_rune_and_continue(callback.message, state, data['current_runes'][chosen_index], data['current_amino'], chosen_index)

async def save_rune_and_continue(message: Message, state: FSMContext, rune: str, amino: str, chosen_index: int = 0):
    data = await state.get_data()
    runes = data.get('final_runes', []) + [rune]
    aminos = data.get('final_aminos', []) + [amino]
    
    images_list = data.get('final_images', [])
    files = RUNE_IMAGES.get(amino, [])
    if chosen_index < len(files) and files[chosen_index] is not None:
        img_filename = files[chosen_index]
    else:
        img_filename = files[0] if files else f"{amino}.jpg"
    images_list.append(img_filename)

    complex_num = data.get('complex_num', 1)
    
    db = load_db()
    user_id = str(message.chat.id)
    user_data = db.get(user_id, {})
    now = datetime.now()
    
    if complex_num < 3:
        # ОБНОВЛЯЕМ ШАГ
        user_data['ritual_step'] = complex_num + 1
        user_data['last_active'] = now.isoformat()
        user_data['notified_incomplete'] = False
        db[user_id] = user_data
        save_db(db)

        await state.update_data(complex_num=complex_num + 1, final_runes=runes, final_aminos=aminos, final_images=images_list)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
        caption = f"✅ Выбрана руна: **{rune}**\n\n🔮 **Комплекс {complex_num + 1}.** СИНЯЯ грань:"
        await send_html(message.answer, text=caption, reply_markup=kb)
        await state.set_state(Ritual.waiting_for_blue)
    else:
        # ОБРЯД ЗАВЕРШЕН — ОБНУЛЯЕМ ШАГ
        schedule_next_ritual(user_data, now)
        user_data["ritual_step"] = 0
        user_data["notified_12h"] = False
        user_data["notified_incomplete"] = False
        db[user_id] = user_data
        save_db(db)

        trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
        aminos_encoded = urllib.parse.quote(",".join(aminos))
        images_encoded = urllib.parse.quote(",".join(images_list))
        web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&images={images_encoded}&v={int(now.timestamp())}"
        
        if now < trial_end:
            kb_final = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📖 Получить результаты", web_app=WebAppInfo(url=web_app_url))]])
            time_left = trial_end - now
            days_left = max(0, int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0))
            
            final_text = f"🎉 **ОБРЯД ЗАВЕРШЕН!**\n\nТвоя финальная триада: **{' | '.join(runes)}**\n\n"
            final_text += f"🎁 У вас идет оплаченный период (осталось дней: {days_left}). Чтобы расшифровать послание Салгын Кут и активировать силу рун, нажмите кнопку **ПОЛУЧИТЬ РЕЗУЛЬТАТЫ** ниже 👇"
            
            await send_html(message.answer, text=final_text, reply_markup=kb_final)
            await state.clear()
        else:
            await state.update_data(final_runes=runes, final_aminos=aminos, final_images=images_list)
            
            username = await cached_bot_username()
            ref_link = f"https://t.me/{username}?start=ref_{user_id}"

            kb_pay = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 1 месяц — 990 ₽", callback_data="pay_1")],
                [InlineKeyboardButton(text="💳 3 месяца — 2 490 ₽", callback_data="pay_3")],
                [InlineKeyboardButton(text="💳 1 год — 9 990 ₽", callback_data="pay_12")]
            ])
            
            pay_text = (
                "🎉 **ОБРЯД ЗАВЕРШЕН!**\n\n"
                "⚠️ Ваш период бесплатного доступа закончился.\n\n"
                "Для получения расшифровки, безлимитных обрядов и доступа в закрытое сообщество выберите подходящий тариф ниже:\n\n"
                "💡 **Нет возможности оплатить?**\n"
                "Пригласите друга по ссылке ниже и получите `+3 дня` бесплатного доступа за каждого!\n"
                f"🔗 Ваша ссылка: `{ref_link}`"
            )
            
            await send_html(message.answer, text=pay_text, reply_markup=kb_pay)
            await state.set_state(Ritual.waiting_for_payment)

@dp.callback_query(Ritual.waiting_for_payment, F.data.startswith("pay_"))
async def process_payment_selection(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    months = int(callback.data.split("_")[1])
    
    prices_map = {
        1: ("Подписка на 1 месяц", 99000, "sub_1"),
        3: ("Подписка на 3 месяца", 249000, "sub_3"),
        12: ("Подписка на 1 год", 999000, "sub_12")
    }
    
    label, amount, payload = prices_map[months]
    price = [LabeledPrice(label=label, amount=amount)]
    
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Доступ к результатам",
        description=f"{label} и вступление в закрытый клуб.",
        payload=payload,
        provider_token=PAYMENT_TOKEN,
        currency="RUB",
        prices=price
    )

@dp.pre_checkout_query()
async def pre_checkout_process(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message, state: FSMContext):
    payload = message.successful_payment.invoice_payload
    now = datetime.now()
    
    if payload.startswith("sub_") or payload == "unlock_result":
        months = 1
        if payload == "sub_3": months = 3
        elif payload == "sub_12": months = 12
        
        days_to_add = months * 30 if months < 12 else 365
        
        db = load_db()
        user_id = str(message.chat.id)
        if user_id in db and isinstance(db[user_id], dict):
            current_end = datetime.fromisoformat(db[user_id].get("trial_end", now.isoformat()))
            start_date = current_end if current_end > now else now
            
            db[user_id]["trial_end"] = (start_date + timedelta(days=days_to_add)).isoformat()
            schedule_next_ritual(db[user_id], now)
            db[user_id]["notified"] = 0 
            save_db(db)
            
        data = await state.get_data()
        runes = data.get('final_runes', [])
        aminos = data.get('final_aminos', [])
        images_list = data.get('final_images', [])
        
        aminos_encoded = urllib.parse.quote(",".join(aminos))
        images_encoded = urllib.parse.quote(",".join(images_list))
        web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&images={images_encoded}&v={int(now.timestamp())}"
        
        kb_final = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Получить результаты", web_app=WebAppInfo(url=web_app_url))],
            [InlineKeyboardButton(text="💎 Вступить в сообщество", url="https://t.me/+SjHfMeVK4GA3N2Ey")]
        ])
        final_text = f"✅ **Оплата прошла успешно! Добро пожаловать.**\n\nВаша подписка продлена на {days_to_add} дней.\nТвоя финальная триада: **{' | '.join(runes)}**\n\n👇 Нажмите на кнопку ниже, чтобы вступить в наше закрытое сообщество!"
        await send_html(message.answer, text=final_text, reply_markup=kb_final)
        await state.clear()
        
    elif payload == "pay_sticks":
        data = await state.get_data()
        order_data = data.get("pending_order", {})
        fio = order_data.get("fio", "Не указано")
        phone = order_data.get("phone", "Не указано")
        delivery = order_data.get("delivery", "Не указано")
        address = order_data.get("address", "-")
        admin_text = (
            "✅ 💰 **ЗАКАЗ ПАЛОЧЕК УСПЕШНО ОПЛАЧЕН!** 💰 ✅\n\n"
            f"👤 **Покупатель:** {fio}\n"
            f"📞 **Телефон:** {phone}\n"
            f"🚚 **Способ:** {delivery}\n"
            f"📍 **Адрес:** {address}\n\n"
            f"💬 *Деньги получены. Свяжитесь с клиентом для отправки заказа!*"
        )
        await send_html(bot.send_message, chat_id=MY_ID, text=admin_text)
        await message.answer("🎉 **Поздравляем с приобретением!**\nОплата прошла успешно. Скоро с вами свяжутся, или вы можете написать напрямую: @daayakh")
        await state.update_data(pending_order=None)

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    os.makedirs("images/amino", exist_ok=True)
    os.makedirs("images/runes", exist_ok=True)

    try:
        await redis.ping()
    except Exception as e:
        logging.error("Redis недоступен (%s:%s): %s", REDIS_HOST, REDIS_PORT, e)
        raise

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        logging.error("Неверный BOT_TOKEN в файле .env — проверьте токен у @BotFather")
        raise

    logging.info("Бот онлайн: @%s id=%s", me.username, me.id)
    global _bot_username
    _bot_username = me.username
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск и меню"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(daily_notifier())
    try:
        await dp.start_polling(bot)
    except TelegramConflictError:
        logging.error(
            "Этот токен уже опрашивает другой процесс. "
            "Остановите старый бот (другой сервер или Docker) или смените токен."
        )
        raise

if __name__ == "__main__":
    asyncio.run(main())
