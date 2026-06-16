import asyncio
import logging
import json
import os
import urllib.parse
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, InputMediaPhoto, LabeledPrice, PreCheckoutQuery
)
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8713600489:AAHj7U6brsJngHu0F6Ig-PLqwGRRjmlRbtc"
PAYMENT_TOKEN = "381764678:TEST:ВАШ_ТЕСТОВЫЙ_ТОКЕН" # <-- ВСТАВЬ СВОЙ ТОКЕН СЮДА!
DB_FILE = "users_db.json"
MY_ID = 297967650  # <-- ТВОЙ TELEGRAM ID ДЛЯ ЗАЯВОК

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
        [InlineKeyboardButton(text="🕯 Как подготовиться?", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/prep.html"))],
        [InlineKeyboardButton(text="🔮 Начать обряд", callback_data="start_ritual")]
    ])

def get_greeting_text(user_data, now):
    trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
    time_left = trial_end - now
    days_left = max(0, int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0))
    
    greeting = "Привет. Ты в системе работы с рунами и тремя Кут. Нажми «Старт», чтобы продолжить.\n\n"
    if not user_data.get("paid", False):
        if now < trial_end:
            greeting += f"🎁 **У вас активно {days_left} дня БЕСПЛАТНОГО пользования!**\n\n"
        else:
            greeting += "⚠️ **Ваш 3-дневный бесплатный период окончен.**\nПройдите обряд, чтобы оплатить доступ к результатам и попасть в VIP-клуб.\n\n"
    return greeting

# --- ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ ---
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
                    await bot.send_message(chat_id=int(user_id), text="⚠️ **Ваш бесплатный период завершен!**\n\nТеперь расшифровка обрядов стала платной. Оплатив доступ, вы получите результаты и инвайт в закрытый VIP-сообщество! 🔮")
                    data['notified'] = 3
                    changed = True
            except Exception: pass
        if changed: save_db(db)
        await asyncio.sleep(3600)

# --- ОБРАБОТКА ДАННЫХ ИЗ ФОРМЫ (ЗАКАЗ ПАЛОЧЕК) ---
@dp.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("type") == "order_sticks":
            order_text = (
                "🚨 **НОВЫЙ ЗАКАЗ ЧЕТЫРЕХГРАННЫХ ПАЛОЧЕК!**\n\n"
                f"👤 **Покупатель:** {data['fio']}\n"
                f"📞 **Телефон:** {data['phone']}\n"
                f"🚚 **Способ:** {data['delivery']}\n"
                f"📍 **Адрес доставки:** {data['address']}\n\n"
                f"💬 *Свяжитесь с клиентом для подтверждения заказа.*"
            )
            await bot.send_message(chat_id=MY_ID, text=order_text, parse_mode="Markdown")
            await message.answer("🎉 **Заказ успешно отправлен!**\nАвтор свяжется с вами по указанному номеру телефона для уточнения деталей. Спасибо!")
    except Exception as e:
        logging.error(f"Ошибка обработки заказа формы: {e}")

# --- КОМАНДЫ ---
@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    if message.from_user.id == MY_ID:
        db = load_db()
        user_id = str(message.from_user.id)
        if user_id in db: del db[user_id]
        save_db(db)
        await state.clear()
        await message.answer("✅ Твой профиль сброшен. Напиши /start для теста 3-х дней.")

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
    
    if os.path.exists("gif1.mp4"):
        await message.answer_animation(animation=FSInputFile("gif1.mp4"), caption=caption, reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    else:
        await message.answer(caption, reply_markup=get_main_menu_kb(), parse_mode="Markdown")

# --- ЛОГИКА ОБРЯДА ---
@dp.callback_query(F.data == "start_ritual")
async def start_ritual_handler(callback: CallbackQuery, state: FSMContext):
    db = load_db()
    user_id = str(callback.from_user.id)
    user_data = db.get(user_id, {})
    now = datetime.now()
    next_ritual = datetime.fromisoformat(user_data.get("next_ritual_time", now.isoformat()))
    
    if now < next_ritual:
        time_left = next_ritual - now
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await callback.answer(f"⏳ Обряд уже проведен! Следующий будет доступен через {hours} ч. {minutes} мин.", show_alert=True)
        return
        
    await callback.message.delete()
    await state.update_data(complex_num=1, final_runes=[], final_aminos=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "🔮 **Начинаем обряд.**\n\nКомплекс 1. Брось палочки и посмотри на **СИНЮЮ** грань. Сколько точек?"
    
    if os.path.exists("gif2.mp4"):
        await bot.send_animation(chat_id=callback.message.chat.id, animation=FSInputFile("gif2.mp4"), caption=caption, parse_mode="Markdown", reply_markup=kb)
    else:
        await bot.send_message(chat_id=callback.message.chat.id, text=caption, parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
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
    await state.update_data(green=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔴 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    caption = "Теперь посмотри на **КРАСНУЮ** грань. Сколько точек?"
    if callback.message.animation or callback.message.video or callback.message.photo:
        await callback.message.edit_caption(caption=caption, parse_mode="Markdown", reply_markup=kb)
    else:
        await callback.message.edit_text(text=caption, parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_red)

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def proc_red(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    triplet = BASE_MAP[data['blue']] + BASE_MAP[data['green']] + BASE_MAP[callback.data.split("_")[1]]
    amino, runes = "Неизвестно", []
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino, runes = name, a_data["runes"]
            break
    await state.update_data(current_runes=runes, current_amino=amino)
    await callback.message.delete()
    if runes:
        if len(runes) > 1:
            kb_buttons = []
            for i, r in enumerate(runes):
                kb_buttons.append([InlineKeyboardButton(text=f"👉 Руна {i+1} ({r})", callback_data=f"rune_{i}")])
            await bot.send_message(chat_id=callback.message.chat.id, text=f"🧬 Выпало: **{amino}**\n👆 Сделай свой выбор:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), parse_mode="Markdown")
            await state.set_state(Ritual.waiting_for_rune_choice)
        else:
            await save_rune_and_continue(callback.message, state, runes[0], amino)
    else:
        await bot.send_message(chat_id=callback.message.chat.id, text=f"Триплет {triplet} не найден.")

@dp.callback_query(Ritual.waiting_for_rune_choice, F.data.startswith("rune_"))
async def proc_rune(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await callback.message.delete() 
    await save_rune_and_continue(callback.message, state, data['current_runes'][int(callback.data.split("_")[1])], data['current_amino'])

async def save_rune_and_continue(message: Message, state: FSMContext, rune: str, amino: str):
    data = await state.get_data()
    runes = data.get('final_runes', []) + [rune]
    aminos = data.get('final_aminos', []) + [amino]
    complex_num = data.get('complex_num', 1)
    if complex_num < 3:
        await state.update_data(complex_num=complex_num + 1, final_runes=runes, final_aminos=aminos)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
        caption = f"✅ Выбрана руна: **{rune}**\n\n🔮 **Комплекс {complex_num + 1}.** СИНЯЯ грань:"
        if os.path.exists("gif2.mp4"):
            await message.answer_animation(animation=FSInputFile("gif2.mp4"), caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            await message.answer(caption, parse_mode="Markdown", reply_markup=kb)
        await state.set_state(Ritual.waiting_for_blue)
    else:
        db = load_db()
        user_id = str(message.chat.id)
        user_data = db.get(user_id, {})
        now = datetime.now()
        is_paid = user_data.get("paid", False)
        trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
        
        if is_paid or now < trial_end:
            aminos_encoded = urllib.parse.quote(",".join(aminos))
            web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&v={int(now.timestamp())}"
            kb_final = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📖 Получить результаты", web_app=WebAppInfo(url=web_app_url))]])
            
            final_text = f"🎉 **ОБРЯД ЗАВЕРШЕН!**\n\nТвоя финальная триада: **{' | '.join(runes)}**\nПерепишите их на полоску бумаги (справа налево).\n\n"
            if not is_paid:
                time_left = trial_end - now
                days_left = max(0, int(time_left.total_seconds() / 86400) + (1 if time_left.total_seconds() % 86400 > 0 else 0))
                final_text += f"🎁 У вас идет бесплатный период (осталось дней: {days_left}). Чтобы расшифровать послание Салгын Кут и активировать силу рун, нажмите кнопку **ПОЛУЧИТЬ РЕЗУЛТАТЫ** ниже 👇"
                
            await message.answer(final_text, reply_markup=kb_final, parse_mode="Markdown")
            user_data["next_ritual_time"] = (now + timedelta(hours=12)).isoformat()
            db[user_id] = user_data
            save_db(db)
            await state.clear()
        else:
            await state.update_data(final_runes=runes, final_aminos=aminos)
            await message.answer("🎉 **ОБРЯД ЗАВЕРШЕН!**\n\n⚠️ Ваш бесплатный 3-дневный период закончился.\n\nДля получения расшифровки и доступа в наш VIP-клуб, пожалуйста, оплатите подписку:")
            price = [LabeledPrice(label="Расшифровка и VIP-клуб", amount=50000)]
            await bot.send_invoice(chat_id=message.chat.id, title="Доступ к результатам", description="Оплата расшифровки рун и вступление в клуб.", payload="unlock_result", provider_token=PAYMENT_TOKEN, currency="RUB", prices=price)
            await state.set_state(Ritual.waiting_for_payment)

@dp.pre_checkout_query()
async def pre_checkout_process(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message, state: FSMContext):
    now = datetime.now()
    db = load_db()
    user_id = str(message.chat.id)
    if user_id in db and isinstance(db[user_id], dict):
        db[user_id]["paid"] = True
        db[user_id]["next_ritual_time"] = (now + timedelta(hours=12)).isoformat()
        save_db(db)
    data = await state.get_data()
    runes = data.get('final_runes', [])
    aminos = data.get('final_aminos', [])
    aminos_encoded = urllib.parse.quote(",".join(aminos))
    web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&v={int(now.timestamp())}"
    kb_final = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Получить результаты", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton(text="💎 Вступить в VIP-клуб", url="https://t.me/+SjHfMeVK4GA3N2Ey")]
    ])
    final_text = f"✅ **Оплата прошла успешно! Добро пожаловать.**\n\nТвоя финальная триада: **{' | '.join(runes)}**\nПерепишите их на полоску бумаги (справа налево).\n\n👇 Нажмите на кнопку ниже, чтобы вступить в наше закрытое VIP-сообщество!"
    await message.answer(final_text, reply_markup=kb_final, parse_mode="Markdown")
    await state.clear()

async def main():
    os.makedirs("images/amino", exist_ok=True)
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(daily_notifier())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
