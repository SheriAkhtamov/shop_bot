from aiogram.fsm.state import State, StatesGroup

class Registration(StatesGroup):
    choosing_language = State()
    waiting_for_phone = State()