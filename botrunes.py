import asyncio
import logging
import json
import os
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН")
DB_FILE = "users_db.json"
MY_ID = 297967650  # Твой ID для команды /reset

# --- ИНИЦИАЛИЗАЦИЯ REDIS ---
redis = Redis(host='localhost')
storage = RedisStorage(redis=redis)

# --- БАЗА ДАННЫХ (Основа) ---
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
    waiting_for_completion = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# --- ФУНКЦИИ ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f)

# --- ХЭНДЛЕРЫ ---
@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    if message.from_user.id == MY_ID:
        db = load_db()
        user_id = str(message.from_user.id)
        if user_id in db:
            del db[user_id]
            save_db(db)
            await state.clear()
            await message.answer("✅ Таймер сброшен для твоего профиля.")
        else:
            await message.answer("Таймер не был установлен.")
    else:
        await message.answer("У тебя нет прав.")

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # (Логика кулдауна остается прежней...)
    await state.clear()
    await state.update_data(complex_num=1, final_runes=[])
    await message.answer("🔮 Начинаем обряд... Сколько точек на СИНЕЙ грани?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def process_blue(callback: CallbackQuery, state: FSMContext):
    await state.update_data(blue=callback.data.split("_")[1])
    await callback.message.edit_text("Теперь СИНЯЯ грань учтена. Сколько точек на ЗЕЛЕНОЙ?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_green)
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def process_green(callback: CallbackQuery, state: FSMContext):
    await state.update_data(green=callback.data.split("_")[1])
    await callback.message.edit_text("ЗЕЛЕНАЯ учтена. Сколько на КРАСНОЙ?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]))
    await state.set_state(Ritual.waiting_for_red)
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def process_red(callback: CallbackQuery, state: FSMContext):
    # ... (логика обработки триплета остается прежней) ...
    await callback.answer()

# ... (и так далее для всех функций save_rune_and_continue, process_rune_choice и т.д.)

async def main():
    os.makedirs("images/runes", exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
