import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime, timedelta
import io
from PIL import Image
import pytesseract
import pdfplumber
import re
import os

# Вкажіть шлях до Tesseract на вашому macOS
pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'

# Налаштування логування
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Налаштування бота

API_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID_HOME = -1001442489680  # Чат для Дому
CHAT_ID_DACHA = -1002317620785  # Чат для Дачі
SUMMARY_CHAT_ID = -1001442489680  # Чат для підсумків (можна змінити)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Словник для зберігання сум (формат: {"рік-місяць": сума})
PAYMENT_SUMS = {}

# Визначення ключових слів для ідентифікації об'єктів
HOME_IDENTIFIERS = {"Чорнобильська"}
DACHA_IDENTIFIERS = {"Козацька"}

SERVICE_IDENTIFIERS = {
    "Дім": {
        "квартплата": {"code": "12345678", "keywords": ["за утримання буд. та прибуд"]},
        "газ": {"code": "87654321", "keywords": ["за газ"]},
        "газ доставка": {"code": "56789012", "keywords": ["за доставку газу"]},
        "холодна вода": {"code": "61943-01", "keywords": ["ХВ", "61943"]},
        "холодна вода абонплата": {"code": "61943-02", "keywords": ["за абонентське обслуговування", "61943"]},
        "інтернет": {"code": "08101006", "keywords": ["08101006"]},
        "опалення": {"code": "34567890", "keywords": ["за опалення"]},
        "опалення доставка": {"code": "90123456", "keywords": ["абонен. обслугов. (ТЕ)"]},
        "вивіз сміття": {"code": "78901234", "keywords": ["вивезення побутових відходів"]},
        "електроенергія": {"code": "23456789", "keywords": ["Електроенергія"]},
        "гаряча вода абон плата": {"code": "141005201460100", "keywords": ["абон. обслугов.", "141005201460100"]}
    },
    "Дача": {
        "електроенергія": {"code": "000400560811", "keywords": ["Електроенергія", "000400560811"]},
        "газ": {"code": "180562637", "keywords": ["Плата за спожитий газ", "о/р 180562637"]},
        "доставка газу": {"code": "0800293595", "keywords": ["Доставка газу", "0800293595"]},
        "інтернет": {"code": "0473896", "keywords": ["0473896"]}
    }
}

MONTHS = {
    "січень": "Січень", "січня": "Січень",
    "лютий": "Лютий", "лютого": "Лютий",
    "березень": "Березень", "березня": "Березень",
    "квітень": "Квітень", "квітня": "Квітень",
    "травень": "Травень", "травня": "Травень",
    "червень": "Червень", "червня": "Червень",
    "липень": "Липень", "липня": "Липень",
    "серпень": "Серпень", "серпня": "Серпень",
    "вересень": "Вересень", "вересня": "Вересень",
    "жовтень": "Жовтень", "жовтня": "Жовтень",
    "листопад": "Листопад", "листопада": "Листопад",
    "грудень": "Грудень", "грудня": "Грудень"
}

# Клавіатури
object_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Дім"), KeyboardButton(text="Дача")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

service_kb_home = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="квартплата"), KeyboardButton(text="газ"), KeyboardButton(text="газ доставка")],
        [KeyboardButton(text="холодна вода"), KeyboardButton(text="холодна вода абонплата")],
        [KeyboardButton(text="інтернет"), KeyboardButton(text="опалення"), KeyboardButton(text="опалення доставка")],
        [KeyboardButton(text="вивіз сміття"), KeyboardButton(text="електроенергія"), KeyboardButton(text="гаряча вода абон плата")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

service_kb_dacha = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="електроенергія"), KeyboardButton(text="газ")],
        [KeyboardButton(text="доставка газу"), KeyboardButton(text="інтернет")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

month_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Січень"), KeyboardButton(text="Лютий"), KeyboardButton(text="Березень")],
        [KeyboardButton(text="Квітень"), KeyboardButton(text="Травень"), KeyboardButton(text="Червень")],
        [KeyboardButton(text="Липень"), KeyboardButton(text="Серпень"), KeyboardButton(text="Вересень")],
        [KeyboardButton(text="Жовтень"), KeyboardButton(text="Листопад"), KeyboardButton(text="Грудень")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Підтвердити", callback_data="confirm")],
    [InlineKeyboardButton(text="Скасувати", callback_data="cancel")]
])

VALID_MONTHS = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]

# Стани
class ReceiptStates(StatesGroup):
    waiting_for_object = State()
    waiting_for_service = State()
    waiting_for_month = State()
    waiting_for_amount = State()
    waiting_for_confirmation = State()

# Функція для автоматичного визначення об'єкта, послуги, місяця та суми
async def identify_receipt(file_id: str, file_type: str) -> tuple[str, str, str, float]:
    file = await bot.get_file(file_id)
    file_path = file.file_path
    downloaded_file = await bot.download_file(file_path)
    
    text = ""
    if file_type == "photo":
        image = Image.open(io.BytesIO(downloaded_file.read()))
        text = pytesseract.image_to_string(image, lang='ukr').lower()
        logger.debug("Витягнутий текст з фото: %s", text)
    else:
        try:
            with pdfplumber.open(io.BytesIO(downloaded_file.read())) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text
                    else:
                        image = page.to_image().original
                        text += pytesseract.image_to_string(image, lang='ukr')
            text = text.lower()
            logger.debug("Витягнутий текст з PDF: %s", text)
        except Exception as e:
            logger.error("Помилка при обробці PDF: %s", str(e))
            return None, None, None, None

    object_type = None
    if any(identifier.lower() in text for identifier in HOME_IDENTIFIERS):
        object_type = "Дім"
        logger.debug("Визначено об'єкт: Дім")
    elif any(identifier.lower() in text for identifier in DACHA_IDENTIFIERS):
        object_type = "Дача"
        logger.debug("Визначено об'єкт: Дача")

    service = None
    if object_type:
        for svc, data in SERVICE_IDENTIFIERS[object_type].items():
            if data["code"].lower() in text:
                service = svc
                logger.debug("Визначено послугу за кодом: %s (код: %s)", service, data["code"])
                break
        if not service:
            for svc, data in SERVICE_IDENTIFIERS[object_type].items():
                if any(keyword.lower() in text for keyword in data["keywords"]):
                    service = svc
                    logger.debug("Визначено послугу за ключовими словами: %s", service)
                    break

    month = None
    for month_key, month_value in MONTHS.items():
        if month_key in text:
            month = month_value
            logger.debug("Визначено місяць: %s", month)
            break

    amount = None
    amount_pattern = re.compile(r'(?:сума\s*\(грн\)|грн)\s*(\d+\s*\d*[.,]\d+|\d+[.,]\d+)')
    amounts = amount_pattern.findall(text)
    logger.debug("Знайдені суми з контекстом: %s", amounts)
    if amounts:
        amount_str = amounts[0].replace(" ", "").replace(",", ".")
        try:
            amount = float(amount_str)
            logger.debug("Визначено суму: %s", amount)
        except ValueError:
            logger.debug("Не вдалося перетворити суму: %s", amount_str)
    else:
        fallback_pattern = re.compile(r'\b\d+\s*\d*[.,]\d+\b|\b\d+[.,]\d+\b')
        fallback_amounts = fallback_pattern.findall(text)
        logger.debug("Знайдені суми без контексту: %s", fallback_amounts)
        if fallback_amounts:
            amount_str = fallback_amounts[0].replace(" ", "").replace(",", ".")
            try:
                amount = float(amount_str)
                logger.debug("Визначено суму (без контексту): %s", amount)
            except ValueError:
                logger.debug("Не вдалося перетворити суму (без контексту): %s", amount_str)

    return object_type, service, month, amount

@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    logger.debug("Отримано команду /start від %s", message.from_user.id)
    await message.answer(
        "Привіт! Я твій бот для обробки квитанцій.\n\n"
        "Надішли мені фотографію або документ квитанції!"
    )

@dp.message(Command("cancel"))
async def cancel_process(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    logger.debug("Отримано команду /cancel, поточний стан: %s", current_state)
    if current_state is None:
        await message.answer("Немає активного процесу для скасування. Надішли квитанцію через /start!")
        return
    await state.clear()
    await message.answer("Процес скасовано. Надішли нову квитанцію через /start!", reply_markup=types.ReplyKeyboardRemove())

@dp.message(lambda message: message.photo or message.document)
async def handle_receipt(message: types.Message, state: FSMContext):
    logger.debug("Отримано фото або документ від %s, тип: %s", message.from_user.id, message.content_type)
    
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    file_type = "photo" if message.photo else "document"
    
    object_type, service, month, amount = await identify_receipt(file_id, file_type)
    
    await state.update_data(
        file_id=file_id,
        file_type=file_type,
        object=object_type,
        category=service,
        year=datetime.now().year,
        month=month,
        amount=amount
    )
    
    if not object_type:
        await message.answer("Не вдалося визначити об'єкт. Оберіть його вручну:", reply_markup=object_kb)
        await state.set_state(ReceiptStates.waiting_for_object)
        return
    
    if not service:
        service_kb = service_kb_home if object_type == "Дім" else service_kb_dacha
        await message.answer(f"Об'єкт: {object_type}\nНе вдалося визначити послугу. Оберіть її вручну:", reply_markup=service_kb)
        await state.set_state(ReceiptStates.waiting_for_service)
        return
    
    data = await state.get_data()
    month_str = data['month'] if data['month'] else "невідомий місяць"
    amount_str = f"{data['amount']:.2f}" if data['amount'] is not None else "невідома сума"
    
    if not month or not amount:
        await message.answer(
            f"Визначено: {object_type} - {service}\n"
            f"Місяць: {month_str}\n"
            f"Сума: {amount_str} грн\n"
            "Деякі дані не визначено. Вкажіть їх вручну."
        )
        if not month:
            await message.answer("Оберіть місяць оплати:", reply_markup=month_kb)
            await state.set_state(ReceiptStates.waiting_for_month)
        elif not amount:
            await message.answer("Введіть суму платежу (тільки число, наприклад, 123.45):")
            await state.set_state(ReceiptStates.waiting_for_amount)
    else:
        caption = f"{service} | {month} {data['year']} | {amount:.2f} грн"
        await message.answer(f"Перевірте дані:\n{caption}\n\nВсе правильно?", reply_markup=confirm_kb)
        await state.set_state(ReceiptStates.waiting_for_confirmation)

@dp.message(ReceiptStates.waiting_for_object)
async def process_object(message: types.Message, state: FSMContext):
    if message.text not in ["Дім", "Дача"]:
        await message.answer("Будь ласка, оберіть об'єкт із клавіатури.")
        return
    await state.update_data(object=message.text)
    service_kb = service_kb_home if message.text == "Дім" else service_kb_dacha
    await message.answer(f"Об'єкт: {message.text}\nОберіть послугу:", reply_markup=service_kb)
    await state.set_state(ReceiptStates.waiting_for_service)

@dp.message(ReceiptStates.waiting_for_service)
async def process_service(message: types.Message, state: FSMContext):
    data = await state.get_data()
    object_type = data['object']
    valid_services = list(SERVICE_IDENTIFIERS[object_type].keys())
    if message.text not in valid_services:
        await message.answer("Будь ласка, оберіть послугу із клавіатури.")
        return
    await state.update_data(category=message.text)
    if not data.get('month'):
        await message.answer("Оберіть місяць оплати:", reply_markup=month_kb)
        await state.set_state(ReceiptStates.waiting_for_month)
    elif not data.get('amount'):
        await message.answer("Введіть суму платежу (тільки число, наприклад, 123.45):")
        await state.set_state(ReceiptStates.waiting_for_amount)
    else:
        caption = f"{message.text} | {data['month']} {data['year']} | {data['amount']:.2f} грн"
        await message.answer(f"Перевірте дані:\n{caption}\n\nВсе правильно?", reply_markup=confirm_kb)
        await state.set_state(ReceiptStates.waiting_for_confirmation)

@dp.message(ReceiptStates.waiting_for_month)
async def process_month(message: types.Message, state: FSMContext):
    logger.debug("Отримано повідомлення в стані waiting_for_month: %s", message.text)
    if message.text not in VALID_MONTHS:
        await message.answer("Будь ласка, виберіть місяць за допомогою кнопок.")
        return
    await state.update_data(month=message.text)
    data = await state.get_data()
    if data.get('amount') is None:
        await message.answer("Введіть суму платежу (тільки число, наприклад, 123.45):")
        await state.set_state(ReceiptStates.waiting_for_amount)
    else:
        caption = f"{data['category']} | {data['month']} {data['year']} | {data['amount']:.2f} грн"
        await message.answer(f"Перевірте дані:\n{caption}\n\nВсе правильно?", reply_markup=confirm_kb)
        await state.set_state(ReceiptStates.waiting_for_confirmation)

@dp.message(ReceiptStates.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    logger.debug("Отримано повідомлення в стані waiting_for_amount: %s", message.text)
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("Будь ласка, введіть коректну суму цифрами (наприклад, 123.45).")
        return
    
    await state.update_data(amount=amount)
    data = await state.get_data()
    caption = f"{data['category']} | {data['month']} {data['year']} | {amount:.2f} грн"
    await message.answer(f"Перевірте дані:\n{caption}\n\nВсе правильно?", reply_markup=confirm_kb)
    await state.set_state(ReceiptStates.waiting_for_confirmation)

@dp.callback_query(lambda c: c.data == "confirm", ReceiptStates.waiting_for_confirmation)
async def confirm_receipt(callback: types.CallbackQuery, state: FSMContext):
    logger.debug("Отримано підтвердження в стані waiting_for_confirmation")
    data = await state.get_data()
    category, month, obj, amount, year = data['category'], data['month'], data['object'], data['amount'], data['year']
    file_id, file_type = data['file_id'], data['file_type']
    chat_id = CHAT_ID_HOME if obj == "Дім" else CHAT_ID_DACHA
    caption = f"{category} | {month} {year} | {amount:.2f} грн"
    
    if file_type == "photo":
        await bot.send_photo(chat_id, file_id, caption=caption)
    else:
        await bot.send_document(chat_id, file_id, caption=caption)
    
    # Зберігаємо суму для підсумку
    month_year_key = f"{year}-{VALID_MONTHS.index(month) + 1:02d}"
    PAYMENT_SUMS[month_year_key] = PAYMENT_SUMS.get(month_year_key, 0) + amount
    logger.debug("Додано суму %s до %s: %s", amount, month_year_key, PAYMENT_SUMS[month_year_key])
    
    await callback.message.edit_text("Квитанцію успішно відправлено!")
    await state.clear()
    logger.debug("Стан очищено після відправки")

@dp.callback_query(lambda c: c.data == "cancel", ReceiptStates.waiting_for_confirmation)
async def cancel_confirmation(callback: types.CallbackQuery, state: FSMContext):
    logger.debug("Отримано скасування в стані waiting_for_confirmation")
    await callback.message.edit_text("Відправку скасовано. Надішли нову квитанцію через /start!")
    await state.clear()
    logger.debug("Стан очищено після скасування")

@dp.message()
async def handle_other(message: types.Message):
    logger.debug("Отримано неочікуване повідомлення: %s", message.text)
    await message.answer("Будь ласка, надішли фотографію або документ квитанції для початку!")

async def send_monthly_summary():
    while True:
        now = datetime.now()
        # Перевіряємо, чи 26 число
        if now.day == 26:
            last_month = now - timedelta(days=26)  # Приблизно попередній місяць
            month_key = f"{last_month.year}-{last_month.month:02d}"
            total = PAYMENT_SUMS.get(month_key, 0)
            if total > 0:
                month_name = VALID_MONTHS[last_month.month - 1]
                hryvnias = int(total)
                kopecks = int((total - hryvnias) * 100)
                await bot.send_message(
                    SUMMARY_CHAT_ID,
                    f"Всього за {month_name.lower()} {last_month.year} року заплачено {hryvnias} грн {kopecks:02d} коп."
                )
                logger.info("Надіслано підсумок за %s: %s грн %s коп", month_key, hryvnias, kopecks)
            # Чекаємо до наступного дня
            await asyncio.sleep(24 * 60 * 60)
        else:
            # Чекаємо до наступної перевірки (щогодини)
            await asyncio.sleep(60 * 60)

async def main():
    logger.info("Запускаємо бота...")
    asyncio.create_task(send_monthly_summary())  # Запускаємо підсумки в фоновому режимі
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
