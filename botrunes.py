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
PAYMENT_TOKEN = "381764678:TEST:ВАШ_ТЕСТОВЫЙ_ТОКЕН" # Вставь сюда реальный токен от ЮKassa
DB_FILE = "users_db.json"
MY_ID = 297967650

redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- СОСТОЯНИЯ ---
class Ritual(StatesGroup):
    waiting_for_blue = State()
    waiting_for_green = State()
    waiting_for_red = State()
    waiting_for_rune_choice = State()
    waiting_for_payment = State()

# --- ЛОГИКА ОПЛАТЫ ---
@dp.pre_checkout_query()
async def pre_checkout_process(pre_checkout: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message, state: FSMContext):
    # Достаем сохраненные руны из памяти
    data = await state.get_data()
    runes = data.get('final_runes', [])
    aminos = data.get('final_aminos', [])
    
    aminos_encoded = urllib.parse.quote(",".join(aminos))
    web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&v={int(datetime.now().timestamp())}"
    
    kb_final = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть расшифровку", web_app=WebAppInfo(url=web_app_url))]
    ])
    
    await message.answer("✅ Оплата прошла успешно! Теперь вам доступна расшифровка:", reply_markup=kb_final)
    await state.clear()

# --- ЛОГИКА ОБРЯДА ---
async def save_rune_and_continue(message: Message, state: FSMContext, rune: str, amino: str):
    data = await state.get_data()
    runes = data.get('final_runes', []) + [rune]
    aminos = data.get('final_aminos', []) + [amino]
    complex_num = data.get('complex_num', 1)
    
    if complex_num < 3:
        await state.update_data(complex_num=complex_num + 1, final_runes=runes, final_aminos=aminos)
        # (Тут код запроса нового броска как в прошлой версии...)
    else:
        # ЗАВЕРШЕНИЕ: Сохраняем данные и просим оплатить
        await state.update_data(final_runes=runes, final_aminos=aminos)
        await message.answer("🎉 **ОБРЯД ЗАВЕРШЕН!**\nДля получения расшифровки нажмите кнопку ниже:")
        
        price = [LabeledPrice(label="Расшифровка обряда", amount=50000)]
        await bot.send_invoice(
            chat_id=message.chat.id, title="Разблокировать результат",
            description="Оплата доступа к расшифровке ваших рун.",
            payload="unlock_result", provider_token=PAYMENT_TOKEN,
            currency="RUB", prices=price
        )
        await state.set_state(Ritual.waiting_for_payment)

# ... (остальные функции proc_blue, proc_green, proc_red оставляем как были) ...
