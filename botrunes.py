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
from PIL import Image

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН")
DB_FILE = "users_db.json"

# База данных аминокислот (сокращена для примера, вставь полную из прошлого сообщения)
BASE_MAP = {"1": "А", "2": "Ц", "3": "У", "4": "Г"}
AMINO_ACIDS = {
    "Аланин": {"codons": ["ГЦУ", "ГЦГ", "ГЦЦ", "ГЦА"], "runes": [")", "¥", "𐰉", "𐰈"]},
    "Метионин": {"codons": ["АУГ"], "runes": ["Г"]},
    "Валин": {"codons": ["ГУУ", "ГУЦ", "ГУА", "ГУГ"], "runes": ["𐰓", "9", "ς"]},
    # ... ДОБАВЬ ОСТАЛЬНЫЕ ...
}

# --- СОСТОЯНИЯ ---
class Ritual(StatesGroup):
    waiting_for_blue = State()
    waiting_for_green = State()
    waiting_for_red = State()
    waiting_for_rune_choice = State()
    waiting_for_completion = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)

def get_numbers_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]
    ])

def get_runes_kb(runes_list):
    # Клавиатура для выбора руны, если их несколько
    buttons = [[InlineKeyboardButton(text=f"Руна: {r}", callback_data=f"rune_{i}")] for i, r in enumerate(runes_list)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_final_image(runes_texts, user_id):
    # ПЛЕЙСХОЛДЕР: Здесь будет логика склеивания 3 картинок рун с помощью Pillow (PIL)
    # Сейчас она просто создает пустую заглушку, чтобы код не падал, пока нет реальных картинок.
    final_path = f"images/final_{user_id}.jpg"
    img = Image.new('RGB', (600, 200), color = (73, 109, 137))
    img.save(final_path)
    return final_path

# --- ХЭНДЛЕРЫ ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Проверка на кулдаун (12 часов)
    db = load_db()
    user_id = str(message.from_user.id)
    if user_id in db:
        next_time = datetime.fromisoformat(db[user_id])
        if datetime.now() < next_time:
            wait_hours = (next_time - datetime.now()).seconds // 3600
            await message.answer(f"⏳ Обряд уже проведен! Следующий можно начать через {wait_hours} часов.")
            return

    await state.clear()
    await state.update_data(complex_num=1, final_runes=[])
    
    await message.answer(
        "🔮 **Начинаем обряд Белой Магии Рун!**\n\n"
        "Комплекс 1. Брось палочки и посмотри на **СИНЮЮ** грань. Сколько точек?",
        parse_mode="Markdown", reply_markup=get_numbers_kb()
    )
    await state.set_state(Ritual.waiting_for_blue)

@dp.callback_query(Ritual.waiting_for_blue, F.data.startswith("throw_"))
async def process_blue(callback: CallbackQuery, state: FSMContext):
    await state.update_data(blue=callback.data.split("_")[1])
    await callback.message.edit_text("Теперь посмотри на **ЗЕЛЕНУЮ** грань. Сколько точек?", reply_markup=get_numbers_kb())
    await state.set_state(Ritual.waiting_for_green)

@dp.callback_query(Ritual.waiting_for_green, F.data.startswith("throw_"))
async def process_green(callback: CallbackQuery, state: FSMContext):
    await state.update_data(green=callback.data.split("_")[1])
    await callback.message.edit_text("Теперь посмотри на **КРАСНУЮ** грань. Сколько точек?", reply_markup=get_numbers_kb())
    await state.set_state(Ritual.waiting_for_red)

@dp.callback_query(Ritual.waiting_for_red, F.data.startswith("throw_"))
async def process_red(callback: CallbackQuery, state: FSMContext):
    red_val = callback.data.split("_")[1]
    data = await state.get_data()
    triplet = BASE_MAP[data['blue']] + BASE_MAP[data['green']] + BASE_MAP[red_val]
    
    # Ищем аминокислоту
    amino = "Неизвестно"
    runes_list = []
    for name, a_data in AMINO_ACIDS.items():
        if triplet in a_data["codons"]:
            amino = name
            runes_list = a_data["runes"]
            break

    await state.update_data(current_amino=amino, current_runes=runes_list)

    if len(runes_list) > 1:
        # Если рун несколько - просим выбрать
        await callback.message.edit_text(
            f"🧪 Аминокислота: **{amino}**\n\nЭтой аминокислоте соответствует несколько рун. "
            "Посмотри на картинки и выбери одну интуитивно:",
            parse_mode="Markdown", reply_markup=get_runes_kb(runes_list)
        )
        # В будущем здесь можно отправлять media_group с картинками всех доступных рун
        await state.set_state(Ritual.waiting_for_rune_choice)
    else:
        # Если руна одна - сохраняем автоматически и идем дальше
        await save_rune_and_continue(callback.message, state, runes_list[0])

@dp.callback_query(Ritual.waiting_for_rune_choice, F.data.startswith("rune_"))
async def process_rune_choice(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = int(callback.data.split("_")[1])
    chosen_rune = data['current_runes'][idx]
    await save_rune_and_continue(callback.message, state, chosen_rune)

async def save_rune_and_continue(message: Message, state: FSMContext, chosen_rune: str):
    data = await state.get_data()
    final_runes = data['final_runes']
    final_runes.append(chosen_rune)
    complex_num = data['complex_num']

    if complex_num < 3:
        # Переход к следующему комплексу
        await state.update_data(complex_num=complex_num + 1, final_runes=final_runes)
        await message.edit_text(
            f"✅ Руна {chosen_rune} сохранена.\n\n"
            f"Комплекс {complex_num + 1}. Брось палочки и посмотри на **СИНЮЮ** грань:",
            reply_markup=get_numbers_kb()
        )
        await state.set_state(Ritual.waiting_for_blue)
    else:
        # Все 3 комплекса завершены
        user_id = message.chat.id
        final_image_path = create_final_image(final_runes, user_id)
        
        instruction_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Я всё сделал!", callback_data="ritual_done")]
        ])

        # Отправляем итог
        photo = FSInputFile(final_image_path)
        await bot.send_photo(
            chat_id=user_id,
            photo=photo,
            caption=(
                "🔮 **ТВОИ РУНЫ ОПРЕДЕЛЕНЫ** 🔮\n\n"
                f"Твоя триада: {final_runes[0]} | {final_runes[1]} | {final_runes[2]}\n\n"
                "📜 **Инструкция:**\n"
                "1. Перенеси эти руны на бумажную полоску справа налево.\n"
                "2. Наклей на нижнюю часть волчка.\n"
                "3. Закрути волчок на мандале 3 раза.\n\n"
                "🔗 [Подробная инструкция на сайте](https://твой-сайт.com/guide)\n\n"
                "Как только закончишь ритуал, нажми кнопку ниже."
            ),
            parse_mode="Markdown",
            reply_markup=instruction_kb
        )
        
        # Удаляем старое сообщение с клавиатурой
        await message.delete()
        await state.set_state(Ritual.waiting_for_completion)

@dp.callback_query(Ritual.waiting_for_completion, F.data == "ritual_done")
async def finish_ritual(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    
    # Записываем блокировку на 12 часов
    db = load_db()
    next_available = datetime.now() + timedelta(hours=12)
    db[user_id] = next_available.isoformat()
    save_db(db)

    await callback.message.edit_text(
        "🎉 **Поздравляю с успешным завершением обряда!**\n\n"
        "Магия начала свое действие. Я жду тебя завтра для проведения нового ритуала "
        "(команда /start будет доступна через 12 часов).",
        parse_mode="Markdown"
    )
    await state.clear()
# --- СЕКРЕТНАЯ КОМАНДА ДЛЯ СБРОСА ---
@dp.message(F.text == "/reset")
async def reset_timer(message: Message, state: FSMContext):
    MY_ID = 297967650  # Твой ID
    
    if message.from_user.id == MY_ID:
        db = load_db()
        user_id = str(message.from_user.id)
        
        if user_id in db:
            del db[user_id]
            save_db(db)
            await state.clear()  # Сбрасываем состояние ритуала, если ты "завис"
            await message.answer("✅ Таймер сброшен. Можешь проводить обряд снова.")
        else:
            await message.answer("Таймер не был установлен, ты можешь начинать!")
    else:
        await message.answer("У тебя нет прав для этой команды.")
async def main():
    # Создаем файл БД, если его нет
    if not os.path.exists(DB_FILE):
        save_db({})
    # Создаем папку для картинок, если её нет
    os.makedirs("images/runes", exist_ok=True)
    
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
