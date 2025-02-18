import os
import logging
import asyncio
import aiohttp
import io
import pytesseract
from PIL import Image, ImageEnhance
from langdetect import detect
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from dotenv import load_dotenv
import re
import random
import redis.asyncio as redis
from tenacity import retry, stop_after_attempt, wait_exponential

# Загружаем переменные окружения
load_dotenv("bot.env")
API_TOKEN = os.getenv("API_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", 'ru')
TESSERACT_PATH = os.getenv("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")

if not API_TOKEN:
    raise ValueError("API_TOKEN не найден. Проверьте .env")
if not MISTRAL_API_KEY:
    raise ValueError("MISTRAL_API_KEY не найден. Проверьте .env")

# Указываем путь к Tesseract (только для Windows!)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем бота и диспетчер
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Initialize Redis (for user profiles)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

# Константы
SOLUTION_PREFIX = "💡 **Эврика! Решение найдено:**\n\n"
ABOUT_TEXT = (
    "🤖 **Привет, я Квантовый Квест!**\n\n"
    "Я — твой верный помощник в мире сложных задач и головоломок! 🚀\n\n"
    "Просто брось мне текст или фото, и я сделаю все возможное, чтобы найти ответ! 🔍\n\n"
    "Хочешь узнать больше о моих возможностях? Жми кнопки внизу! 😉"
)
SUPPORT_TEXT = (
    "🆘 **Нужна помощь? Не беда!**\n\n"
    "Если что-то пошло не так или у тебя есть идеи, как я могу стать лучше, пиши сюда: xcommander67@gmail.com.\n\n"
    "Или просто нажми /start, чтобы вернуться в главное меню! ✨"
)
PHOTO_REQUEST_TEXT = "📸 Отлично! Отправь мне фото, и я попробую вытащить из него текст! 🤓"
ERROR_MESSAGE = "🚧 Упс! Что-то пошло не так при обработке изображения... Попробуй другое фото, или убедись, что текст хорошо видно! 🧐"

# Разнообразные ответы
GREETINGS = [
    "Приветствую, искатель знаний! 🌟",
    "Здравствуй, мой друг! Готов к новым открытиям? 🚀",
    "Привет! Что привело тебя в мой квантовый мир? ✨",
    "И снова здравствуйте! Чем могу помочь сегодня? 😉"
]
PROCESSING_MESSAGES = [
    "⏳ Запускаю свои квантовые процессоры... Щас подумаем! 🧠",
    "🤔 Хм... Дайте-ка подумать... 💡",
    "🧐 Сейчас посмотрим, что тут у нас... 🔍",
    "⚙️ Шестеренки крутятся, алгоритмы работают... 🤖"
]
OCR_ERROR_MESSAGES = [
    "😞 Увы, не смог разобрать текст на картинке! Может, попробуем еще раз? 🖼️",
    "😕 Что-то не получается с OCR... Попробуйте другое фото! 📸",
    "😔 Не могу прочитать этот текст... Может, он написан на другом языке? 🌐"
]
NO_TEXT_MESSAGES = [
    "🤔 Хм... Кажется, на фото нет текста! 🤷‍♂️",
    "🧐 Не вижу здесь никакого текста... Может, попробуем что-то другое? 🖼️",
    "🙄 Где же текст? Может, он спрятался? 🙈"
]
API_ERROR_MESSAGES = [
    "🔌 Ой-ой! Проблемы с подключением... Попробуйте еще раз! 😬",
    "🚫 Что-то не так с сетью... Сейчас починю! 🛠️",
    "🚧 Кажется, сервер немного занят... Попробуйте через минутку! ⏳"
]
UNEXPECTED_ERROR_MESSAGES = [
    "💥 Бабах! Что-то пошло не по плану... Я уже разбираюсь! 👨‍💻",
    "🤯 Ой, что-то сломалось! Но я уже на пути к восстановлению! 🚀",
    "😕 Упс! Произошла какая-то ошибка... Не волнуйтесь, я все исправлю! 😉"
]

# Клавиатура
kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🖼 Распознать текст с фото")],
        [KeyboardButton(text="ℹ️ Кто ты, бот?"), KeyboardButton(text="🆘 SOS! Нужна помощь!"), KeyboardButton(text="🌐 Сменить язык")]
    ], resize_keyboard=True,
    input_field_placeholder="Выберите действие..."
)

def determine_subject(text):
    subject_keywords = {
        "физика": ["сила", "скорость", "ускорение", "энергия", "закон"],
        "химия": ["молекула", "атом", "реакция", "валентность", "молярная"],
        "математика": ["функция", "производная", "интеграл", "уравнение", "теорема"]
    }
    text = text.lower()
    subject_counts = {}
    for subject, keywords in subject_keywords.items():
        count = 0
        for keyword in keywords:
            if keyword in text:
                count += 1
        subject_counts[subject] = count

    best_subject = max(subject_counts, key=subject_counts.get)
    return best_subject

def get_prompt(subject, task):
    if subject == "физика":
        instructions = f"Ты — опытный преподаватель физики. Помоги решить следующую задачу. Объясни решение по шагам, используя понятные термины и законы физики.  {task}"
    elif subject == "химия":
        instructions = f"Ты — опытный преподаватель химии. Помоги решить следующую задачу. Объясни решение по шагам, используя понятные термины и химические уравнения. {task}"
    elif subject == "математика":
        instructions = f"Ты — опытный преподаватель математики. Помоги решить следующую задачу. Объясни решение по шагам, используя понятные термины и математические теоремы. {task}"
    else:
        instructions = f"Ты — опытный преподаватель. Помоги решить следующую задачу. Объясни решение по шагам, используя понятные термины и знания. {task}"
    return instructions

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def solve_task_with_ai(task: str, input_language: str, user_style: str = "") -> str:
    """
    Решает задачу с помощью Mistral AI API и пытается адаптировать свой стиль к пользователю.
    """
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }

    user_style = user_style[:200]

    # Определение предмета по ключевым словам (пример)
    subject = determine_subject(task) #Нужно реализовать функцию

    if input_language != 'en':
        instructions = get_prompt(subject, task)
    else:
        instructions = get_prompt(subject, task)

    messages = [{"role": "user", "content": instructions}]

    data = {
        "model": "mistral-tiny",  # Use a smaller model
        "messages": messages,
        "temperature": 0.7
    }

    logging.info(f"Отправляю запрос в Mistral AI. Язык: {input_language}, Задача: {task[:50]}...")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=data, headers=headers, timeout=30) as response:
                if response.status == 200:
                    try:
                        result = await response.json()
                        logging.debug(f"Получен ответ от Mistral AI: {result}")
                        response_content = result["choices"][0]["message"]["content"].strip()
                        logging.info(f"Содержание ответа: {response_content[:100]}...")
                        return response_content
                    except (KeyError, TypeError, json.JSONDecodeError) as e:
                        logging.exception(f"Ошибка при обработке ответа от Mistral AI: {e}. Ответ: {result}")
                        raise  # re-raise to trigger retry
                else:
                    error_text = await response.text()
                    logging.error(f"Ошибка Mistral AI API. Статус: {response.status}, текст: {error_text}, Задача: {task[:50]}")
                    raise Exception(f"Mistral AI API error: {response.status} - {error_text}")  # Raise for retry

        except aiohttp.ClientError as e:
            logging.warning(f"Ошибка соединения с Mistral AI: {e}")
            raise  # re-raise to trigger retry
        except asyncio.TimeoutError:
            logging.error("Превышено время ожидания ответа от Mistral AI.")
            raise  # re-raise to trigger retry

def analyze_user_style(text: str) -> str:
    """Анализирует текст пользователя и возвращает инструкции для Mistral AI."""
    emoji_pattern = re.compile(r"["u"\U0001F600-\U0001F64F"u"\U0001F300-\U0001F5FF"u"\U0001F680-\U0001F6FF"u"\U0001F1E0-\U0001F1FF"u"\U00002702-\U000027B0"u"\U000024C2-\U0001F251""]+", flags=re.UNICODE)
    emojis = emoji_pattern.findall(text)
    num_emojis = len(emojis)
    num_exclamations = text.count("!")
    sentences = re.split(r"[.!?]+", text)
    num_sentences = len(sentences)
    total_words = sum(len(s.split()) for s in sentences)
    avg_sentence_length = total_words / num_sentences if num_sentences > 0 else 0

    style_instructions = ""

    if num_emojis > 2:
        style_instructions += "Используй много эмодзи. "
    elif num_emojis > 0:
        style_instructions += "Используй эмодзи умеренно. "

    if num_exclamations > 3:
        style_instructions += "Будь очень эмоциональным. "
    elif num_exclamations > 1:
        style_instructions += "Будь эмоциональным. "

    if avg_sentence_length < 5:
        style_instructions += "Используй короткие предложения. "
    elif avg_sentence_length > 15:
        style_instructions += "Используй длинные предложения. "

    if not style_instructions:
        style_instructions += "Общайся в нейтральном стиле. "

    return f"Пожалуйста, ответь в том же стиле, что и пользователь. {style_instructions}"

async def get_user_profile(user_id: int) -> dict | None:
    try:
        key = f"user:{user_id}"
        profile = await redis_client.hgetall(key)
        if profile:
            return {k.decode(): v.decode() for k, v in profile.items()}
        return None
    except redis.exceptions.ConnectionError as e:
        logging.error(f"Redis connection error: {e}")
        return None  # Or handle the error appropriately

async def set_user_profile(user_id: int, name: str, language: str, style: str):
    try:
        key = f"user:{user_id}"
        await redis_client.hset(key, mapping={"name": name, "language": language, "style": style})
        await redis_client.expire(key, 3600 * 24)
    except redis.exceptions.ConnectionError as e:
        logging.error(f"Redis connection error: {e}")
        # Handle the error appropriately (e.g., retry, log, inform the user)

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    user_profile = await get_user_profile(user_id)

    if user_profile:
        await message.answer(
            f"{random.choice(GREETINGS)}\nРад снова видеть тебя, {user_profile['name']}! 😉",
            reply_markup=kb
        )
    else:
        await message.answer(
            "🌟 Приветствую, искатель знаний! 🌟\n\n"
            "Я — Квантовый Квест, бот, который поможет тебе найти ответы на самые сложные вопросы! 📚\n\n"
            "Как я могу к тебе обращаться? ✨",
            reply_markup=ReplyKeyboardRemove()
        )
        await dp.register_message_handler(get_name, F.text, ~Command("start"))

async def get_name(message: types.Message):
    name = message.text.strip()
    user_style = analyze_user_style(message.text)
    user_id = message.from_user.id

    await set_user_profile(user_id=user_id, name=name, language=DEFAULT_LANGUAGE, style=user_style)

    await message.answer(
        f"Приятно познакомиться, {name}! Что будем делать сегодня? 😉",
        reply_markup=kb
    )

@dp.message(F.text == "🌐 Сменить язык")
async def language_button_handler(message: types.Message):
    await message.answer("Выберите язык (en, ru):", reply_markup=ReplyKeyboardRemove())
    await dp.register_message_handler(set_language, F.text)

async def set_language(message: types.Message):
    language = message.text.strip().lower()
    if language in ["en", "ru"]:
        user_id = message.from_user.id
        user_profile = await get_user_profile(user_id)
        if user_profile:
            await set_user_profile(user_id=user_id, name=user_profile['name'], language=language, style=user_profile['style'])
            await message.answer(f"Язык изменен на {language}!", reply_markup=kb)
        else:
            await message.answer("Пожалуйста, сначала используйте /start.", reply_markup=kb)
    else:
        await message.answer("Неверный язык. Пожалуйста, выберите 'en' или 'ru'.", reply_markup=kb)

@dp.message(F.text == "🖼 Распознать текст с фото")
async def photo_button_handler(message: types.Message):
    await message.answer("📸 Отправь фото с текстом, и я помогу распознать его!",
                         reply_markup=ReplyKeyboardRemove())

@dp.message(F.text == "ℹ️ Кто ты, бот?")
async def about_button_handler(message: types.Message):
    await message.answer(ABOUT_TEXT, reply_markup=kb)

@dp.message(F.text == "🆘 SOS! Нужна помощь!")
async def support_button_handler(message: types.Message):
    await message.answer(SUPPORT_TEXT, reply_markup=kb)

# Image preprocessing
def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Preprocesses the image to improve OCR accuracy.
    """
    # Convert to grayscale
    image = image.convert("L")

    # Enhance contrast
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)

    # Further steps can be added: sharpening, noise reduction, etc.

    return image

# Обработчик фото (OCR)
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    user_profile = await get_user_profile(user_id)
    user_style = ""
    language = DEFAULT_LANGUAGE # default language

    if user_profile:
        user_style = user_profile.get("style", "") # Use .get() for safety
        language = user_profile.get("language", DEFAULT_LANGUAGE)

    if message.caption:
        user_style = analyze_user_style(message.caption)
    else:
        logging.info("No caption found in the photo. Using default style.")

    await message.answer(random.choice(PROCESSING_MESSAGES))

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_path = file.file_path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}") as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
                    image = Image.open(io.BytesIO(image_bytes))

                    # Preprocess the image
                    image = preprocess_image(image)

                    try:
                        extracted_text = pytesseract.image_to_string(image, lang=f"{language}+eng").strip()
                    except Exception as ocr_error:
                        logging.error(f"OCR error: {ocr_error}")
                        await message.answer(random.choice(OCR_ERROR_MESSAGES), reply_markup=kb)
                        return

                    if extracted_text:
                        # Removed redundant language detection
                        solution = await solve_task_with_ai(extracted_text, language, user_style)
                        await message.answer(solution, reply_markup=kb)
                    else:
                        await message.answer(random.choice(NO_TEXT_MESSAGES), reply_markup=kb)
                else:
                    await message.answer(random.choice(API_ERROR_MESSAGES), reply_markup=kb)
    except aiohttp.ClientError as e:
        logging.error(f"AIOHTTP error: {e}")
        await message.answer(random.choice(API_ERROR_MESSAGES), reply_markup=kb)
    except Exception as e:
        logging.exception(f"Unexpected error: {e}")
        await message.answer(random.choice(UNEXPECTED_ERROR_MESSAGES), reply_markup=kb)

# Обработчик текстовых сообщений (решение задач)
@dp.message(F.text)
async def solve_task_handler(message: types.Message):
     if message.text in {"🖼 Распознать текст с фото", "ℹ️ Кто ты, бот?", "🆘 SOS! Нужна помощь!", "🌐 Сменить язык"}:
         return
     if message.text == "/start":
         return
     task = message.text.strip()
     user_id = message.from_user.id
     user_profile = await get_user_profile(user_id)
     user_style = ""
     language = DEFAULT_LANGUAGE

     if user_profile:
         user_style = user_profile.get("style", "")
         language = user_profile.get("language", DEFAULT_LANGUAGE)

     if not task:
         await message.answer("✍️ Эй! А где же текст задачи? Напишите мне что-нибудь! 😉")
         return

     await message.answer(random.choice(PROCESSING_MESSAGES))

     # Определение предмета по ключевым словам (пример)
     subject = determine_subject(task)  # Нужно реализовать функцию

     #Создание промпта
     instructions = get_prompt(subject, task)

     solution = await solve_task_with_ai(task, language, user_style)
     await message.answer(solution, reply_markup=kb)

@dp.message(F.video)
async def handle_video(message: types.Message):
    await message.answer("Извините, я пока не умею обрабатывать видео. Попробуйте отправить фотографию! 📸")

async def main():
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.exception("Бот остановлен с ошибкой!")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())