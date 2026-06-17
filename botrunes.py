import asyncio
import logging
import json
import os
import urllib.parse
from datetime import datetime, timedelta

# --- ЖЕСТКАЯ ПРИВЯЗКА К ПАПКЕ (ЧТОБЫ СЕРВЕР НЕ ТЕРЯЛ ФАЙЛЫ) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, InputMediaPhoto, LabeledPrice, PreCheckoutQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

# --- ЗАГРУЗКА ТОКЕНОВ ИЗ СЕЙФА ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: Бот не видит токен! Проверьте файл .env")

DB_FILE = "users_db.json"
MY_ID = 297967650  # <-- ТВОЙ TELEGRAM ID

redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

BASE_MAP = {"1": "А", "2": "Ц", "3": "У", "4": "Г"}
AMINO_ACIDS = {
    "Аргинин": {"codons": ["ЦГЦ", "ЦГУ", "ЦГА", "ЦГГ", "АГА", "АГГ"], "runes": ["Ч", "Y"]},
    "Аланин": {"codons": ["ГЦУ", "ГЦГ", "ГЦЦ", "ГЦА"], "runes": [")", "¥", "𐰉", "𐰈"]},
    "Аспарагин": {"codons": ["ААУ", "ААЦ"], "runes": ["ʎ"]},
    "Аспарагиновая к-та": {"codons": ["ГАУ", "ГАЦ"], "runes": ["*", "1"]},
    "Валин": {"codons": ["ГУУ", "ГУЦ", "ГУА", "ГУГ"], "runes": ["𐰓", "9", "ς"]},
    "Глютамин": {"codons": ["ЦАА", "ЦАГ"], "runes": ["Λ", "П"]},
    "Глютаминовая к-та": {"codons": ["ГАА", "ГАГ"], "runes": ["Y"]},
    "Гистидин": {"codons": ["ЦАУ", "ЦАЦ"], "runes": ["𐰓"]},
    "Глицин": {"codons": ["ГГУ", "ГГА", "ГГЦ", "ГГГ"], "runes": ["☺", "D", "❂"]},
    "Стоп-кодон": {"codons": ["УАА"], "runes": ["33"]},
    "Изолейцин": {"codons": ["АУУ", "АУЦ", "АУА"], "runes": ["I|", "Є"]},
    "Лейцин": {"codons": ["УУА", "УУГ", "ЦУУ", "ЦУЦ", "ЦУА", "ЦУГ"], "runes": ["Y", "J"]},
    "Лизин": {"codons": ["ААА", "ААГ"], "runes": ["↑"]},
    "Пирролизин": {"codons": ["УАГ"], "runes": ["ᛟ"]},
    "Метионин": {"codons": ["АУГ"], "runes": ["Г"]},
    "Пролин": {"codons": ["ЦЦУ", "ЦЦГ", "ЦЦЦ", "ЦЦА"], "runes": ["ᛉ"]},
    "Серин": {"codons": ["УЦУ", "УЦГ", "УЦЦ", "УЦА", "АГУ", "АГЦ"], "runes": ["D", "☺"]},
    "Триптофан": {"codons": ["УГГ"], "runes": ["⌂"]},
    "Тирозин": {"codons": ["УАУ", "УАЦ"], "runes": ["ᛒ", "ᛃ"]},
    "Треонин": {"codons": ["АЦУ", "АЦГ", "АЦЦ", "АЦА"], "runes": ["ㅋ", "N", "◁", "F"]},
    "Фенилаланин": {"codons": ["УУУ", "УУЦ"], "runes": ["X", "|"]},
    "Цистеин": {"codons": ["УГУ", "УГЦ"], "runes": ["︽", "h"]},
    "Селеноцистеин": {"codons": ["УГА"], "runes": ["M"]}
}

class Ritual(StatesGroup):
    waiting_for_blue = State()
    waiting_for_green = State()
    waiting_for_red = State()
    waiting_for_rune_choice = State()
    waiting_for_payment = State()

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f)

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
                KeyboardButton(text="🕯 Подготовка и Инвентарь", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/prep.html"))
            ]
        ],
        resize_keyboard=True,
        is_persistent=True 
    )

def get_greeting_text(user_data, now):
    trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
    time_left = trial_end - now
    days_left = max(0, int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0))
    
    greeting = "Приветствую. Это Ваш цифровой помощник в достижении гармонии. Используем мудрость салгын кут и силу рунических символов, чтобы помочь вам восполнить утраченный ресурс.\n\n"
    
    if not user_data.get("paid", False):
        if now < trial_end:
            greeting += f"🎁 **У вас активно {days_left} дня БЕСПЛАТНОГО пользования!**\n\n"
        else:
            greeting += "⚠️ **Ваш 3-дневный бесплатный период окончен.**\nПройдите обряд, чтобы оплатить доступ к результатам и попасть в закрытое сообщество.\n\n"
    return greeting

async def daily_notifier():
    while True:
        db = load_db()
        now = datetime.now()
        changed = False
        for user_id, data in db.items():
            if isinstance(data, str) or data.get('paid', False): continue
            trial_end = datetime.fromisoformat(data['trial_end'])
            time_left = trial_end - now
            days_left = int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0)
            notified = data.get('notified', 0)
            try:
                if days_left == 2 and notified == 0:
                    await bot.send_message(chat_id=int(user_id), text="⏳ **Напоминание:** У вас осталось 2 дня бесплатного доступа к обрядам рун!")
                    data['notified'] = 1
                    changed = True
                elif days_left == 1 and notified == 1:
                    await bot.send_message(chat_id=int(user_id), text="⏳ **Напоминание:** Завтра заканчивается ваш бесплатный период! Успейте провести обряд.")
                    data['notified'] = 2
                    changed = True
                elif days_left <= 0 and notified == 2:
                    await bot.send_message(chat_id=int(user_id), text="⚠️ **Ваш бесплатный период завершен!**\n\nТеперь расшифровка обрядов стала платной. Оплатив доступ, вы получите результаты и инвайт в закрытое сообщество! 🔮")
                    data['notified'] = 3
                    changed = True
            except Exception: pass
        if changed: save_db(db)
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
            await bot.send_message(chat_id=MY_ID, text=admin_pending_text, parse_mode="Markdown")

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

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    db = load_db()
    user_id = str(message.from_user.id)
    now = datetime.now()
    if user_id not in db or isinstance(db[user_id], str):
        db[user_id] = {
            "trial_end": (now + timedelta(days=3)).isoformat(),
            "next_ritual_time": now.isoformat(),
            "notified": 0,
            "paid": False
        }
        save_db(db)
    await state.clear()
    
    caption = get_greeting_text(db[user_id], now)
    
    if os.path.exists("gif1_v2.mp4"):
        await message.answer_animation(animation=FSInputFile("gif1_v2.mp4"), caption=caption, reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    else:
        await message.answer(caption, reply_markup=get_main_menu_kb(), parse_mode="Markdown")
        
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
    
    if now < next_ritual:
        time_left = next_ritual - now
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await message.answer(f"⏳ Обряд уже проведен! Следующий будет доступен через {hours} ч. {minutes} мин.")
        return
        
    await state.update_data(complex_num=1, final_runes=[], final_aminos=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    
    caption = (
        "Бросай как на примере выше\n\n"
        "🔮 **Комплекс 1.** Брось палочки и посмотри на **СИНЮЮ** грань. Сколько точек?"
    )
    
    if os.path.exists("gif2_v2.mp4"):
        await message.answer_animation(animation=FSInputFile("gif2_v2.mp4"), caption=caption, parse_mode="Markdown", reply_markup=kb)
    else:
        await message.answer(caption, parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(blue=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🟢 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "Теперь посмотри на **ЗЕЛЕНУЮ** грань. Сколько точек?"
    if callback.message.animation or callback.message.video or callback.message.photo:
        await callback.message.edit_caption(caption=caption, parse_mode="Markdown", reply_markup=kb)
    else:
        await callback.message.edit_text(text=caption, parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_green)

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def proc_green(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(green=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔴 {i}", callback_data=f"throw_{i}") for i
