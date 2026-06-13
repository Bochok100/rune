import asyncio
import logging
import json
import os
import sys
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis
from PIL import Image

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "users_db.json"
MY_ID = 297967650  # Твой ID

# --- REDIS STORAGE ---
redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)

# --- ДАННЫЕ ---
BASE_MAP = {"1": "А", "2": "Ц", "3": "У", "4": "Г"}
AMINO_ACIDS = {
    "Аргинин": {"codons": ["ЦГЦ", "ЦГУ", "ЦГА", "ЦГГ", "АГА", "АГГ"], "runes": ["Ч", "Y"]},
    "Аланин": {"codons": ["ГЦУ", "ГЦГ", "ГЦЦ", "ГЦА"], "runes": [")", "¥", "𐰉", "𐰈"]},
    "Аспарагин": {"codons": ["ААУ", "ААЦ"], "runes": ["ʎ"]},
    "Валин": {"codons": ["ГУУ", "ГУЦ", "ГУА", "ГУГ"], "runes": ["𐰓", "9", "ς"]},
    "Глицин": {"codons": ["ГГУ", "ГГА", "ГГЦ", "ГГГ"], "runes": ["☺", "D", "❂"]},
    "Метионин": {"codons": ["АУГ"], "runes": ["Г"]},
    "Серин": {"codons": ["УЦУ", "УЦГ", "УЦЦ", "УЦА", "АГУ", "АГЦ"], "runes": ["D", "☺"]},
    "Тирозин": {"codons": ["УАУ", "УАЦ"], "runes": ["ᛒ", "ᛃ"]},
    "Треонин": {"codons": ["АЦУ", "АЦГ", "АЦЦ", "АЦА"], "runes": ["ㅋ", "N", "◁", "F"]}
}

class Ritual(StatesGroup):
    waiting_for_blue = State()
    waiting_for_green = State()
    waiting_for_red = State()
    waiting_for_rune_choice = State()
    waiting_for_completion = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- ФУНКЦИИ БД ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f)

# --- АДМИН КОМАНДЫ ---
@dp.message(F.text == "/restart")
async def restart_bot(message: Message):
    if message.from_user.id == MY_ID:
        await message.answer("🔄 Перезагружаюсь...")
        os._exit(0) # systemd перезапустит бота сам

@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    if message.from_user.id == MY_ID:
        db = load_db()
        user_id = str(message.from_user.id)
        if user_id in db:
            del db[user_id]
            save_db(db)
        await state.clear()
        await message.answer("✅ Таймер сброшен.")

# --- РИТУАЛ ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    db = load_db()
    user_id = str(message.from_user.id)
    if user_id in db:
        if datetime.now() < datetime.fromisoformat(db[user_id]):
            await message.answer("⏳ Обряд доступен через 12 часов.")
            return

    await state.clear()
    await state.update_data(complex_num=1, final_runes=[])
    await message.answer("🔮 Комплекс 1. СИНЯЯ грань (количество точек):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def proc_blue(callback: CallbackQuery, state: FSMContext):
    await state.update_data(blue=callback.data.split("_")[1])
    await callback.message.edit_text("ЗЕЛЕНАЯ грань (количество точек):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_green)
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def proc_green(callback: CallbackQuery, state: FSMContext):
    await state.update_data(green=callback.data.split("_")[1])
    await callback.message.edit_text("КРАСНАЯ грань (количество точек):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_red)
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def proc_red(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    triplet = BASE_MAP[data['blue']] + BASE_MAP[data['green']] + BASE_MAP[callback.data.split("_")[1]]
    
    amino, runes = "Неизвестно", []
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino, runes = name, a_data["runes"]
            break
    
    await state.update_data(current_runes=runes)
    if len(runes) > 1:
        await callback.message.edit_text(f"🧪 {amino}. Выбери руну:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=r, callback_data=f"rune_{i}")] for i, r in enumerate(runes)]))
        await state.set_state(Ritual.waiting_for_rune_choice)
    elif len(runes) == 1:
        await save_rune_and_continue(callback.message, state, runes[0])
    else:
        await callback.message.answer("Триплет не найден. /start для сброса.")
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_rune_choice, F.data.startswith("rune_"))
async def proc_rune(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await save_rune_and_continue(callback.message, state, data['current_runes'][int(callback.data.split("_")[1])])
    await callback.answer()

async def save_rune_and_continue(message: Message, state: FSMContext, rune: str):
    data = await state.get_data()
    runes = data['final_runes'] + [rune]
    if data['complex_num'] < 3:
        await state.update_data(complex_num=data['complex_num'] + 1, final_runes=runes)
        await message.edit_text(f"✅ Руна {rune} сохранена. Комплекс {data['complex_num'] + 1}. СИНЯЯ грань:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
        await state.set_state(Ritual.waiting_for_blue)
    else:
        await message.answer(f"🔮 Готово! Триада: {' | '.join(runes)}")
        await state.set_state(Ritual.waiting_for_completion)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
