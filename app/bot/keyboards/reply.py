from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_phone_kb(lang: str = "ru"):
    text = "📱 Поделиться контактом" if lang == "ru" else "📱 Telefon raqamni yuborish"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=text, request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )