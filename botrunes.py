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
PAYMENT_TOKEN = "381764678:TEST:ВАШ_ТЕСТОВЫЙ_ТОКЕН" # <-- ВСТАВЬ СЮДА СВОЙ ТОКЕН!
DB_FILE = "users_db.json"
MY_ID = 297967650

redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- БАЗА КОДОНОВ ---
BASE_MAP = {"1": "А", "2": "Ц", "3": "У", "4": "Г"}
AMINO_ACIDS = {
    "Аргинин": {"codons": ["ЦГЦ", "ЦГУ", "ЦГА", "ЦГГ", "АГА", "АГГ"], "runes": ["Ч", "Y"]},
    "Аланин": {"codons": ["ГЦУ", "ГЦГ", "ГЦЦ", "ГЦА"], "runes": [")", "¥", "𐰉", "𐰈"]},
    "Валин": {"codons": ["ГУУ", "ГУЦ", "ГУА", "ГУГ"], "runes": ["𐰓", "9", "ς"]},
    # (Остальные аминокислоты добавь сюда по аналогии с прошлыми версиями)
}

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
    data = await state.get_data()
    runes = data.get('final_runes', [])
    aminos = data.get('final_aminos', [])
    aminos_encoded = urllib.parse.quote(",".join(aminos))
    web_app_url = f"https://Bochok100.github.io/rune/result.html?aminos={aminos_encoded}&v={int(datetime.now().timestamp())}"
    
    kb_final = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть расшифровку", web_app=WebAppInfo(url=web_app_url))]
    ])
    await message.answer("✅ Оплата прошла успешно! Результат обряда:", reply_markup=kb_final)
    await state.clear()

# --- ЛОГИКА ОБРЯДА ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(complex_num=1, final_runes=[], final_aminos=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await message.answer("🔮 Начинаем обряд. Комплекс 1. Синюю грань?", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
    await state.update_data(blue=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🟢 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await callback.message.edit_text("Зеленую грань?", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_green)

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def proc_green(callback: CallbackQuery, state: FSMContext):
    await state.update_data(green=callback.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔴 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
    await callback.message.edit_text("Красную грань?", reply_markup=kb)
    await state.set_state(Ritual.waiting_for_red)

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def proc_red(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    triplet = BASE_MAP[data['blue']] + BASE_MAP[data['green']] + BASE_MAP[callback.data.split("_")[1]]
    amino, runes = "Неизвестно", ["-"]
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino, runes = name, a_data["runes"]
            break
    await state.update_data(current_runes=runes, current_amino=amino)
    await save_rune_and_continue(callback.message, state, runes[0], amino)

async def save_rune_and_continue(message: Message, state: FSMContext, rune: str, amino: str):
    data = await state.get_data()
    runes = data.get('final_runes', []) + [rune]
    aminos = data.get('final_aminos', []) + [amino]
    complex_num = data.get('complex_num', 1)
    
    if complex_num < 3:
        await state.update_data(complex_num=complex_num + 1, final_runes=runes, final_aminos=aminos)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔵 {i}", callback_data=f"throw_{i}") for i in range(1, 5)]])
        await message.answer(f"✅ Выбрана: {rune}. Комплекс {complex_num + 1}. Синюю?", reply_markup=kb)
        await state.set_state(Ritual.waiting_for_blue)
    else:
        await state.update_data(final_runes=runes, final_aminos=aminos)
        await message.answer("🎉 Обряд завершен! Оплатите для получения результата:")
        price = [LabeledPrice(label="Расшифровка", amount=50000)]
        await bot.send_invoice(chat_id=message.chat.id, title="Результат", description="Разблокировка", 
                               payload="unlock", provider_token=PAYMENT_TOKEN, currency="RUB", prices=price)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
