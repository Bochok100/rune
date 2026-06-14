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
    
    # Удаляем старое сообщение с вопросом про красную грань, чтобы не засорять чат
    await callback.message.delete()
    
    if runes:
        # 1. Отправляем картинку и описание СРАЗУ
        desc_text = AMINO_DESCRIPTIONS.get(amino, "Описание пока не добавлено.")
        image_path = f"images/amino/{amino}.jpg"
        
        if os.path.exists(image_path):
            photo = FSInputFile(image_path)
            await bot.send_photo(chat_id=callback.message.chat.id, photo=photo, caption=f"🧬 **{amino}**", parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=callback.message.chat.id, text=f"🧬 **{amino}**\n*(Картинка еще не загружена)*", parse_mode="Markdown")

        await bot.send_message(chat_id=callback.message.chat.id, text=desc_text)

        # 2. Если рун несколько — просим выбрать, опираясь на картинку
        if len(runes) > 1:
            kb_buttons = []
            for i, r in enumerate(runes):
                # Называем кнопки в зависимости от количества рун
                if len(runes) == 2:
                    label = "Левая" if i == 0 else "Правая"
                elif len(runes) == 3:
                    label = ["Левая", "Центральная", "Правая"][i]
                else:
                    label = f"Вариант {i+1}"
                    
                # Делаем каждую кнопку с новой строки
                kb_buttons.append([InlineKeyboardButton(text=f"👉 {label} ({r})", callback_data=f"rune_{i}")])
            
            await bot.send_message(
                chat_id=callback.message.chat.id,
                text="👆 **Посмотри на картинку выше.**\nЭтой аминокислоте соответствует несколько рун. Сделай свой интуитивный выбор:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons)
            )
            await state.set_state(Ritual.waiting_for_rune_choice)
        else:
            # Если руна всего одна — сохраняем автоматически
            await save_rune_and_continue(callback.message, state, runes[0])
    else:
        await bot.send_message(chat_id=callback.message.chat.id, text=f"Триплет {triplet} не найден. Напиши /start для сброса.")
        
    await callback.answer()

@dp.callback_query(Ritual.waiting_for_rune_choice, F.data.startswith("rune_"))
async def proc_rune(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    # Убираем кнопки выбора после того, как человек нажал на одну из них
    await callback.message.delete() 
    
    await save_rune_and_continue(callback.message, state, data['current_runes'][int(callback.data.split("_")[1])])
    await callback.answer()

async def save_rune_and_continue(message: Message, state: FSMContext, rune: str):
    data = await state.get_data()
    runes = data['final_runes'] + [rune]
    complex_num = data['complex_num']
    
    # Переход на следующий комплекс или финал
    if complex_num < 3:
        await state.update_data(complex_num=complex_num + 1, final_runes=runes)
        await bot.send_message(
            chat_id=message.chat.id, 
            text=f"✅ Выбрана руна: **{rune}**\n\n🔮 **Комплекс {complex_num + 1}.** Брось палочки и посмотри на **СИНЮЮ** грань:", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i), callback_data=f"throw_{i}") for i in range(1, 5)]]), 
            parse_mode="Markdown"
        )
        await state.set_state(Ritual.waiting_for_blue)
    else:
        await bot.send_message(
            chat_id=message.chat.id, 
            text=f"🎉 **ОБРЯД ЗАВЕРШЕН!**\n\nТвоя финальная триада рун: **{' | '.join(runes)}**\n\nНанеси эти руны на нижнюю часть волчка справа налево и закрути его на мандале 3 раза.", 
            parse_mode="Markdown"
        )
        
        # Устанавливаем таймер на 12 часов
        db = load_db()
        db[str(message.chat.id)] = (datetime.now() + timedelta(hours=12)).isoformat()
        save_db(db)
        
        await state.clear()
