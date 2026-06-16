import asyncio
import logging
import json
import os
import urllib.parse
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.types.web_app_info import WebAppInfo
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8713600489:AAHj7U6brsJngHu0F6Ig-PLqwGRRjmlRbtc"
PAYMENT_TOKEN = "381764678:TEST:181793" 
DB_FILE = "users_db.json"
MY_ID = 297967650

redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- БАЗА КОДОНОВ И РУН ---
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

# --- ГЛАВНОЕ МЕНЮ И ТЕКСТЫ ---
def get_main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Об авторе", callback_data="info_author")],
        [InlineKeyboardButton(text="📜 История метода", callback_data="info_method")],
        [InlineKeyboardButton(text="🌬️ Буор, Ийэ и Салгын Кут", callback_data="info_kut")],
        [InlineKeyboardButton(text="🕯 Как подготовиться?", web_app=WebAppInfo(url="https://Bochok100.github.io/rune/prep.html"))],
        [InlineKeyboardButton(text="🔮 Начать обряд", callback_data="start_ritual")]
    ])

def get_greeting_text(user_data, now):
    trial_end = datetime.fromisoformat(user_data.get("trial_end", now.isoformat()))
    days_left = (trial_end - now).days
    
    greeting = "Приветствую! Выберите нужный раздел меню или начните обряд:\n\n"
    if not user_data.get("paid", False):
        if now < trial_end:
            greeting += f"🎁 **У вас активно 3 дня БЕСПЛАТНОГО пользования!**\n*(Осталось дней: {max(0, days_left)})*\n\n"
        else:
            greeting += "⚠️ **Ваш 3-дневный бесплатный период окончен.**\nПройдите обряд, чтобы оплатить доступ к результатам и попасть в VIP-клуб.\n\n"
    return greeting

# --- ФОНОВЫЙ ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ ---
async def daily_notifier():
    while True:
        db = load_db()
        now = datetime.now()
        changed = False
        
        for user_id, data in db.items():
            if isinstance(data, str) or data.get('paid', False):
                continue
                
            trial_end = datetime.fromisoformat(data['trial_end'])
            days_left = (trial_end - now).days
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
                elif days_left < 0 and notified == 2:
                    await bot.send_message(chat_id=int(user_id), text="⚠️ **Ваш бесплатный период завершен!**\n\nТеперь расшифровка обрядов стала платной. Оплатив доступ, вы не только сможете читать значения рун, но и получите приглашение в наше закрытое VIP-сообщество! 🔮")
                    data['notified'] = 3
                    changed = True
            except Exception:
                pass
                
        if changed:
            save_db(db)
        await asyncio.sleep(3600) # Проверяем каждый час

# --- КОМАНДЫ ---
@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    if message.from_user.id == MY_ID:
        db = load_db()
        user_id = str(message.from_user.id)
        if user_id in db:
            del db[user_id]
            save_db(db)
        await state.clear()
        await message.answer("✅ Твой личный профиль сброшен (триал начат заново). Напиши /start")

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    db = load_db()
    user_id = str(message.from_user.id)
    now = datetime.now()

    if user_id not in db or isinstance(db[user_id], str):
        db[user_id] = {
            "trial_end": (now + timedelta(days=3)).isoformat(),
            "next_ritual_time": now.isoformat(), # Готов к обряду прямо сейчас
            "notified": 0,
            "paid": False
        }
        save_db(db)

    await state.clear()
    greeting = get_greeting_text(db[user_id], now)
    await message.answer(greeting, reply_markup=get_main_menu_kb(), parse_mode="Markdown")

# --- ИНФОРМАЦИОННЫЕ РАЗДЕЛЫ ---
@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery, state: FSMContext):
    db = load_db()
    user_id = str(callback.from_user.id)
    now = datetime.now()
    greeting = get_greeting_text(db.get(user_id, {}), now)
    await callback.message.edit_text(greeting, reply_markup=get_main_menu_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "info_author")
async def info_author(callback: CallbackQuery):
    text = (
        "📖 **Об авторе метода**\n\n"
        "Метод, используемый в данном боте, основан на работах Андрея Ивановича Кривошапкина (Айыңа).\n\n"
        "Айыңа посвятил многие годы изучению древнетюркской гадательной книги «Ирк Битиг», рунических знаков и их интерпретации. "
        "На основе собственных исследований он разработал авторскую систему работы с комбинациями рун, четырёхгранными палочками и связанными с ними символическими значениями.\n\n"
        "При жизни Айыңа передавал свои знания ученикам и стремился сделать метод доступным для людей, интересующихся традиционной культурой и духовным наследием народа саха.\n\n"
        "После ухода автора его ученики продолжают изучать, сохранять и распространять полученные знания.\n\n"
        "Данный бот создан как цифровой инструмент для знакомства с методом Айыңа и сохранения его наследия для будущих поколений."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "info_method")
async def info_method(callback: CallbackQuery):
    text = (
        "📜 **История и суть метода**\n\n"
        "Данный метод направлен на работу с **Салгын Кут** — воздушной составляющей человека.\n\n"
        "Согласно традиции, гармонизация Салгын Кут помогает человеку лучше воспринимать информацию, осознавать внутренние процессы и восстанавливать внутреннее равновесие.\n\n"
        "Практика основана на работах Андрея Ивановича Кривошапкина (Айыҥа), исследователя, рунолога и автора метода."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "info_kut")
async def info_kut(callback: CallbackQuery):
    text = (
        "🌬️ **Три составляющие человека в традиции саха**\n\n"
        "Согласно традиционным представлениям народа саха, человек состоит не только из физического тела. Его сущность образуют три взаимосвязанные составляющие — три Кут.\n\n"
        "🌱 **Буор Кут (земляная составляющая)**\n"
        "Связан с физическим телом человека, его здоровьем, жизненной силой и связью с земным миром. Это основа материального существования человека, его телесная оболочка. Традиционно связывают с Нижним миром.\n\n"
        "🤱 **Ийэ Кут (материнская составляющая)**\n"
        "Человек получает её от родителей при рождении. Она связана с наследственностью, родовой памятью, происхождением и продолжением рода. В современной интерпретации её можно сравнить с генетической информацией (ДНК). Традиционно связывают со Срединным миром.\n\n"
        "🌬 **Салгын Кут (воздушная составляющая)**\n"
        "Связан с сознанием человека, его интеллектом, мыслями, вдохновением, внутренним восприятием и духовным развитием. Именно через Салгын Кут человек взаимодействует с миром идей, знаний и высших смыслов. Традиционно связывают с Верхним миром."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- ЛОГИКА ОБРЯДА ---
@dp.callback_query(F.data == "start_ritual")
async def start_ritual_handler(callback: CallbackQuery, state: FSMContext):
    db = load_db()
    user_id = str(callback.from_user.id)
    user_data = db.get(user_id, {})
    now = datetime.now()
    next_ritual = datetime.fromisoformat(user_data.get("next_ritual_time", now.isoformat()))
    
    # ПРОВЕРКА ТАЙМЕРА ПРИ НАЖАТИИ КНОПКИ
    if now < next_ritual:
        time_left = next_ritual - now
        hours, remainder = divmod(time_left.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await callback.answer(f"⏳ Обряд уже проведен! Следующий будет доступен через {hours} ч. {minutes} мин.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(complex_num=1, final_runes=[], final_aminos=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await callback.message.edit_text("🔮 **Начинаем обряд.**\n\nКомплекс 1. Брось палочки и посмотри на **СИНЮЮ** грань. Сколько точек?", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
    await state.update_data(blue=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🟢 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await callback.message.edit_text("Теперь посмотри на **ЗЕЛЕНУЮ** грань. Сколько точек?", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_green)

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def proc_green(callback: CallbackQuery, state: FSMContext):
    await state.update_data(green=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔴 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await callback.message.edit_text("Теперь посмотри на **КРАСНУЮ** грань. Сколько точек?", parse_mode="Markdown", reply_markup=kb)
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
        await message.answer(f"✅ Выбрана руна: **{rune}**\n\n🔮 **Комплекс {complex_num + 1}.** СИНЯЯ грань:", reply_markup=kb, parse_mode="Markdown")
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
            kb_final = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📖 Открыть инструкцию и расшифровку", web_app=WebAppInfo(url=web_app_url))]])
            
            final_text = f"🎉 **ОБРЯД ЗАВЕРШЕН!**\n\nТвоя финальная триада: **{' | '.join(runes)}**\nПерепишите их на полоску бумаги (справа налево)."
            
            if not is_paid:
                final_text += f"\n\n🎁 *У вас идет бесплатный период (осталось дней: {max(0, (trial_end - now).days)}).*\nПо завершении триала доступ станет платным."
                
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

# --- ЛОГИКА ОПЛАТЫ ---
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
        [InlineKeyboardButton(text="📖 Открыть инструкцию и расшифровку", web_app=WebAppInfo(url=web_app_url))],
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
