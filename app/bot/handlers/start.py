from aiogram import Router, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database.models import User
from app.database.repositories.users import UserRepository
from app.bot.states import Registration
from app.bot.keyboards import inline, reply

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession, state: FSMContext):
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(message.from_user.id)
    
    if user:
        # Пользователь уже есть, сразу даем доступ в магазин
        await message.answer(
            f"С возвращением, {user.username or message.from_user.first_name}! 👋\n"
            f"Xush kelibsiz, {user.username or message.from_user.first_name}!",
            reply_markup=inline.get_main_kb(user_id=message.from_user.id, lang=user.language)
        )
    else:
        # Начинаем регистрацию
        await state.set_state(Registration.choosing_language)
        await message.answer(
            "🇺🇿 Tilni tanlang / 🇷🇺 Выберите язык",
            reply_markup=inline.lang_kb
        )

# Обработка выбора языка
@router.callback_query(Registration.choosing_language, F.data.startswith("lang_"))
async def lang_chosen(callback: types.CallbackQuery, state: FSMContext):
    lang_code = callback.data.split("_")[1] # ru или uz
    await state.update_data(language=lang_code)
    
    await state.set_state(Registration.waiting_for_phone)
    
    text = "Пожалуйста, отправьте ваш номер телефона для регистрации 👇" if lang_code == "ru" else \
           "Ro'yxatdan o'tish uchun telefon raqamingizni yuboring 👇"
    
    await callback.message.delete()
    await callback.message.answer(text, reply_markup=reply.get_phone_kb(lang_code))

# Обработка получения контакта
@router.message(Registration.waiting_for_phone, F.contact)
async def contact_received(message: types.Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    lang = data.get("language", "ru")
    phone = message.contact.phone_number

    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        error_text = (
            "Пожалуйста, отправьте свой номер телефона через кнопку 👇"
            if lang == "ru"
            else "Iltimos, tugma orqali o'zingizning telefon raqamingizni yuboring 👇"
        )
        await message.answer(error_text, reply_markup=reply.get_phone_kb(lang))
        return
    
    # Создаем пользователя
    user_repo = UserRepository(session)
    
    try:
        user = await user_repo.get_by_telegram_id(message.from_user.id)
        if not user:
            new_user = User(
                telegram_id=message.from_user.id, 
                username=message.from_user.first_name,
                phone=phone,
                language=lang,
                role="user"
            )
            user_repo.add(new_user)
            await user_repo.commit()
        else:
            # Just update info
            user.phone = phone
            user.language = lang
            await user_repo.commit()
            
    except IntegrityError:
        await session.rollback()
        # Повторная попытка: пользователь уже создан в параллельном запросе
        user = await user_repo.get_by_telegram_id(message.from_user.id)
        if user:
            user.phone = phone
            user.language = lang
            await session.commit()
    
    await state.clear()
    
    welcome_text = "Вы успешно зарегистрированы! Нажмите кнопку ниже, чтобы открыть магазин 👇" if lang == "ru" else \
                   "Siz muvaffaqiyatli ro'yxatdan o'tdingiz! Do'konni ochish uchun pastdagi tugmani bosing 👇"
    
    await message.answer(
        welcome_text,
        reply_markup=types.ReplyKeyboardRemove()
    )
    
    await message.answer(
        "🛍 Shop Mini App",
        reply_markup=inline.get_main_kb(user_id=message.from_user.id, lang=lang)
    )
