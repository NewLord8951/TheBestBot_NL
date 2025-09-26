import json
import textwrap
from typing import Any, Dict, List, Optional
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loguru import logger

from controler import Controller


# Функция для перевода уровня логирования на русский
def russian_level(record):
    levels = {
        "TRACE": "ТРЕЙС",
        "DEBUG": "ОТЛАДКА",
        "INFO": "ИНФО",
        "SUCCESS": "УСПЕХ",
        "WARNING": "ПРЕДУПРЕЖДЕНИЕ",
        "ERROR": "ОШИБКА",
        "CRITICAL": "КРИТИЧНО",
    }
    record["level"].name = levels.get(record["level"].name, record["level"].name)
    return record


# Настройка loguru для вывода в терминал с русскими уровнями
logger.remove()  # Удаляем стандартный обработчик
logger.add(
    sink=lambda msg: print(msg, end=""),
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <12}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="DEBUG",
    colorize=True,
    filter=russian_level,
)

# Вывод в файл (опционально, без перевода — для анализа)
logger.add(
    "bot.log",
    rotation="10 MB",
    retention="10 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
)

bot_router = Router()
controller = Controller()


def _truncate(value: Optional[Any], length: int) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= length else s[: max(0, length - 1)] + "…"


def get_main_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Таблица", callback_data="action:show_table"),
            InlineKeyboardButton(text="➕ Новое заполнение", callback_data="action:new_entry"),
        ],
        [InlineKeyboardButton(text="📚 Инструкция", callback_data="action:instructions")]
    ])
    return keyboard


def _format_records_table(records: List[Dict[str, Any]]) -> str:
    """Форматирует список записей в компактный текст для отправки в чат.

    Если нет записей — возвращает очень краткий пример-рядок, чтобы пользователь
    понимал ожидаемые поля.
    """
    if not records:
        # Very short sample row to show expected fields (concise)
        return "BSSID | SSID | RSSI | FREQ | TIMESTAMP\n00:11:22:33:44:55 | MyWiFi | -50 | 2412 | 1698115200"

    lines = ["BSSID | SSID | RSSI | FREQ | TIMESTAMP"]
    for r in records:
        lines.append(
            f"{_truncate(r.get('bssid'),17)} | {_truncate(r.get('ssid'),20)} | {_truncate(r.get('rssi'),4)} | {_truncate(r.get('frequency'),5)} | {_truncate(r.get('timestamp'),10)}"
        )
    return "\n".join(lines)


def _validate_wifi_data(data: Dict[str, Any]) -> tuple[bool, str]:
    """Проверяет валидность данных WiFi-сети."""
    required_fields = ['bssid', 'frequency', 'rssi', 'ssid', 'timestamp']

    for field in required_fields:
        if field not in data:
            return False, f"Отсутствует обязательное поле: {field}"

    # Проверка типов данных
    if not isinstance(data['bssid'], str) or len(data['bssid']) == 0:
        return False, "BSSID должен быть непустой строкой"

    if not isinstance(data['ssid'], str):
        return False, "SSID должен быть строкой"

    if not isinstance(data['frequency'], (int, float)) or data['frequency'] <= 0:
        return False, "Frequency должен быть положительным числом"

    if not isinstance(data['rssi'], int) or data['rssi'] > 0:
        return False, "RSSI должен быть целым отрицательным числом"

    if not isinstance(data['timestamp'], (int, float)) or data['timestamp'] <= 0:
        return False, "Timestamp должен быть положительным числом"

    return True, ""


def _prepare_wifi_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Подготавливает данные WiFi-сети, обрабатывая пустые значения."""
    prepared_data = data.copy()
    
    # Обработка пустых или None значений
    for key in prepared_data:
        if prepared_data[key] is None:
            if key in ['frequency', 'rssi', 'timestamp']:
                prepared_data[key] = 0
            elif key in ['ssid', 'bssid', 'channel_bandwidth', 'capabilities']:
                prepared_data[key] = ""
        elif prepared_data[key] == "":
            if key in ['bssid']:
                prepared_data[key] = "00:00:00:00:00:00"
    
    # Гарантируем наличие всех обязательных полей
    required_fields = ['bssid', 'frequency', 'rssi', 'ssid', 'timestamp', 'channel_bandwidth', 'capabilities']
    for field in required_fields:
        if field not in prepared_data:
            if field in ['frequency', 'rssi', 'timestamp']:
                prepared_data[field] = 0
            else:
                prepared_data[field] = ""
    
    return prepared_data


async def _process_single_wifi_record(data: Dict[str, Any], message: types.Message) -> bool:
    """Обрабатывает одну запись WiFi-данных."""
    # Подготавливаем данные
    prepared_data = _prepare_wifi_data(data)

    # Глобальный словарь (вне функции)
    error_messages_sent = {}

    # В функции:
    chat_id = message.chat.id

    is_valid = _validate_wifi_data(prepared_data)
    if not is_valid:
        if chat_id not in error_messages_sent or not error_messages_sent[chat_id]:
            error_messages_sent[chat_id] = True
        return False
    else:
        # Сбрасываем флаг при успешной валидации
        error_messages_sent[chat_id] = False

    try:
        # Пробуем сохранить через контроллер
        network = controller.build_network(prepared_data)
        saved = controller.save_network(network)

        if saved:
            return True
        else:
            return False

    except Exception as e:
        logger.exception("Error processing WiFi record")
        return False


async def _process_multiple_wifi_records(records: List[Dict[str, Any]], message: types.Message) -> None:
    """Обрабатывает множественные записи WiFi-данных."""
    if not records:
        await message.answer("❌ Нет записей для обработки.", reply_markup=get_main_keyboard())
        return

    total_count = len(records)
    success_count = 0
    error_count = 0

    await message.answer(f"🔄 Начинаю обработку {total_count} записей...")

    for i, record in enumerate(records, 1):
        if not isinstance(record, dict):
            error_count += 1
            logger.warning(f"Запись #{i} имеет неверный формат: {type(record)}")
            continue

        try:
            if await _process_single_wifi_record(record, message):
                success_count += 1
            else:
                error_count += 1
        except Exception:
            error_count += 1
            logger.exception(f"Ошибка при обработке записи #{i}")

    # Формируем итоговое сообщение
    result_message = (
        f"Обработка завершена!\n\n"
        f"• Успешно: {success_count}/{total_count}\n"
        f"• Ошибки: {error_count}/{total_count}"
    )

    await message.answer(result_message, reply_markup=get_main_keyboard())


async def _process_json_file_content(content: str, message: types.Message) -> None:
    """Обрабатывает содержимое JSON-файла."""
    try:
        parsed_data = json.loads(content)
    except json.JSONDecodeError as e:
        await message.answer(f"❌ Ошибка парсинга JSON: {e}", reply_markup=get_main_keyboard())
        return

    # Определяем тип данных: одиночная запись или массив записей
    # If the parsed JSON matches the example sentinel values, inform user it's only an example
    def _is_example_payload(obj) -> bool:
        """Detect the example payload shown in the bot instructions.

        We consider it an example when BSSID equals the common placeholder
        '00:11:22:33:44:55' or when timestamp/frequency/rssi match the short example.
        """
        if not isinstance(obj, dict):
            return False
        bssid = obj.get('bssid', '')
        if isinstance(bssid, str) and bssid.strip() == '00:11:22:33:44:55':
            return True
        # other loose checks
        if obj.get('ssid') == 'MyWiFi':
            return True
        return False

    if isinstance(parsed_data, list):
        # Множественные записи
        # If any entry looks like the example, notify the user instead of processing
        if any(_is_example_payload(item) for item in parsed_data if isinstance(item, dict)):
            await message.answer(
                "⚠️ Похоже, вы прислали пример из инструкции, а не реальные данные. Пожалуйста, пришлите реальные записи.",
                reply_markup=get_main_keyboard(),
            )
            return

        await _process_multiple_wifi_records(parsed_data, message)
    elif isinstance(parsed_data, dict):
        # Одиночная запись
        if _is_example_payload(parsed_data):
            await message.answer(
                "⚠️ Похоже, вы прислали пример из инструкции, а не реальные данные. Пожалуйста, пришлите реальные записи.",
                reply_markup=get_main_keyboard(),
            )
            return

        success = await _process_single_wifi_record(parsed_data, message)
        if success:
            await message.answer("✅ Данные из файла успешно сохранены в таблицу!", reply_markup=get_main_keyboard())
    else:
        await message.answer("❌ Неподдерживаемый формат JSON. Ожидается объект или массив.", reply_markup=get_main_keyboard())


# Обработчик команды /start
@bot_router.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "Добро пожаловать в WiFi Data Bot! 🌐\n"
        "Я помогаю собирать и хранить данные о WiFi-сетях.\n"
        "Вы можете присылать данные как текстом в JSON формате, так и JSON-файлами.\n\n"
        "📋 Поддерживаемые форматы:\n"
        "• Одиночная запись: JSON-объект\n"
        "• Множественные записи: JSON-массив объектов\n\n"
        "Используйте кнопки ниже для работы с ботом:"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())


# Обработчик кнопки "Таблица"
@bot_router.callback_query(F.data == "action:show_table")
async def show_table(callback: types.CallbackQuery) -> None:
    """Отправляет пользователю текстовую таблицу со всеми записями."""
    try:
        records = controller.get_all_networks()
        logger.info(f"Получено {len(records)} записей из базы данных")
    except Exception as exc:
        logger.exception("Failed to read records from DB")
        await callback.message.answer(f"❌ Ошибка при получении таблицы: {exc}")
        await callback.answer()
        return

    if not records:
        await callback.message.answer("Таблица пуста. Добавьте данные через '➕ Новое заполнение'.")
        await callback.answer()
        return

    table_text = _format_records_table(records)
    
    # Разбиваем длинные сообщения на части
    if len(table_text) > 4000:
        parts = [table_text[i:i+4000] for i in range(0, len(table_text), 4000)]
        for part in parts:
            await callback.message.answer(f"```\n{part}\n```", parse_mode="Markdown")
    else:
        await callback.message.answer(f"```\n{table_text}\n```", parse_mode="Markdown", reply_markup=get_main_keyboard())
    
    await callback.answer()


# Обработчик кнопки "Начать новое заполнение"
@bot_router.callback_query(F.data == "action:new_entry")
async def new_entry_prompt(callback: types.CallbackQuery) -> None:
    """Просит пользователя прислать JSON с данными о WiFi-сети."""
    example_single = textwrap.dedent('''
        📋 *Одиночная запись:*
        ```json
        {
            "bssid": "00:11:22:33:44:55",
            "frequency": 2412,
            "rssi": -50,
            "ssid": "MyWiFi",
            "timestamp": 1698115200,
            "channel_bandwidth": "20",
            "capabilities": "WPA2-PSK"
        }
        ```
    ''')
    
    example_multiple = textwrap.dedent('''
        📋 *Множественные записи (массив):*
        ```json
        [
            {
                "bssid": "00:11:22:33:44:55",
                "frequency": 2412,
                "rssi": -50,
                "ssid": "MyWiFi",
                "timestamp": 1698115200
            },
            {
                "bssid": "AA:BB:CC:DD:EE:FF",
                "frequency": 5180,
                "rssi": -65,
                "ssid": "OfficeNet",
                "timestamp": 1698115300
            }
        ]
        ```
    ''')

    instruction_text = (
        "📝 Введите данные WiFi-сети в формате JSON или отправьте JSON-файл.\n\n"
        "Вы можете отправить:\n"
        "• **Одиночную запись** - один JSON-объект\n"
        "• **Множественные записи** - JSON-массив объектов\n\n"
    )

    await callback.message.answer(instruction_text, parse_mode="Markdown")
    await callback.message.answer(example_single, parse_mode="Markdown")
    await callback.message.answer("... или ...", parse_mode="Markdown")
    await callback.message.answer(example_multiple, parse_mode="Markdown")
    await callback.answer()


# Обработчик JSON-файлов
@bot_router.message(F.document)
async def handle_json_file(message: types.Message) -> None:
    """Обрабатывает загруженные JSON-файлы."""
    if not message.document:
        return

    # Проверяем, что это JSON файл
    if not message.document.file_name.endswith('.json'):
        await message.answer("❌ Пожалуйста, отправьте файл в формате JSON.", reply_markup=get_main_keyboard())
        return

    try:
        # Скачиваем файл
        file = await message.bot.get_file(message.document.file_id)
        file_content = await message.bot.download_file(file.file_path)
        content_text = file_content.read().decode('utf-8')

        await message.answer("📥 Файл получен, начинаю обработку...")

        # Обрабатываем содержимое файла
        await _process_json_file_content(content_text, message)

    except Exception as e:
        logger.exception("Error processing JSON file")
        await message.answer(f"❌ Ошибка при обработке файла: {e}", reply_markup=get_main_keyboard())


# Обработчик текстового ввода
@bot_router.message()
async def handle_text_or_file(message: types.Message) -> None:
    """Обрабатывает текстовое сообщение как JSON-пэйлоад."""
    logger.info(f"Обработка сообщения от пользователя {message.from_user.id}")
    
    payload_text = message.text or ""

    try:
        parsed_data = json.loads(payload_text)
    except json.JSONDecodeError as e:
        await message.answer(f"❌ Некорректный JSON: {e}", reply_markup=get_main_keyboard())
        return

    # Определяем тип данных: одиночная запись или массив записей
    def _is_example_payload(obj) -> bool:
        if not isinstance(obj, dict):
            return False
        bssid = obj.get('bssid', '')
        if isinstance(bssid, str) and bssid.strip() == '00:11:22:33:44:55':
            return True
        if obj.get('ssid') == 'MyWiFi':
            return True
        return False

    if isinstance(parsed_data, list):
        # Множественные записи
        if any(_is_example_payload(item) for item in parsed_data if isinstance(item, dict)):
            await message.answer(
                "⚠️ Похоже, вы прислали пример из инструкции, а не реальные данные. Пожалуйста, пришлите реальные записи.",
                reply_markup=get_main_keyboard(),
            )
            return

        await _process_multiple_wifi_records(parsed_data, message)
    elif isinstance(parsed_data, dict):
        # Одиночная запись
        if _is_example_payload(parsed_data):
            await message.answer(
                "⚠️ Похоже, вы прислали пример из инструкции, а не реальные данные. Пожалуйста, пришлите реальные записи.",
                reply_markup=get_main_keyboard(),
            )
            return

        success = await _process_single_wifi_record(parsed_data, message)
        if success:
            await message.answer("✅ Данные успешно сохранены в таблицу!", reply_markup=get_main_keyboard())
    else:
        await message.answer("❌ Неподдерживаемый формат JSON. Ожидается объект или массив.", reply_markup=get_main_keyboard())


# Обработчик кнопки "Инструкция"
@bot_router.callback_query(F.data == "action:instructions")
async def show_instructions(callback: types.CallbackQuery) -> None:
    logger.debug("Получен запрос на отображение инструкции")
    instructions = textwrap.dedent(
        """
        📚 *Инструкция по использованию WiFi Data Bot*

        Этот бот предназначен для сбора и хранения данных о WiFi-сетях.

        *Функционал бота:*
        - *Таблица*: Показывает все сохранённые данные о WiFi-сетях
        - *Начать новое заполнение*: Добавить новые WiFi-сети
        - *Инструкция*: Это сообщение

        *Способы добавления данных:*
        1. *Текстовый JSON*: Отправьте JSON-строку как текстовое сообщение
        2. *JSON-файл*: Отправьте файл с расширением .json

        *Форматы данных:*
        - *Одиночная запись*: JSON-объект с данными одной сети
        - *Множественные записи*: JSON-массив объектов

        *Пример одиночной записи:*
        ```json
        {
            "bssid": "00:11:22:33:44:55",
            "frequency": 2412,
            "rssi": -50,
            "ssid": "MyWiFi",
            "timestamp": 1698115200,
            "channel_bandwidth": "20",
            "capabilities": "WPA2-PSK"
        }
        ```

        *Обязательные поля:* bssid, frequency, rssi, ssid, timestamp

        Если возникли ошибки, проверьте формат JSON.
        """
    ).strip()

    await callback.message.answer(
        instructions,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()


@bot_router.callback_query(F.data == "action:new_entry")
async def new_entry_prompt(callback: types.CallbackQuery) -> None:
    example_single = textwrap.dedent('''
        📋 *Одиночная запись:*
        ```json
        {
            "bssid": "00:11:22:33:44:55",
            "frequency": 2412,
            "rssi": -50,
            "ssid": "MyWiFi",
            "timestamp": 1698115200,
            "channel_bandwidth": "20",
            "capabilities": "WPA2-PSK"
        }
        ```
    ''')
    await callback.message.answer("Отправьте JSON (как текст или файл .json) с данными сети.")
    await callback.message.answer(example_single, parse_mode="Markdown")
    await callback.answer()


@bot_router.message(F.document)
async def handle_json_file(message: types.Message) -> None:
    if not message.document:
        return

    if not message.document.file_name.lower().endswith('.json'):
        await message.answer("❌ Пожалуйста, отправьте файл в формате JSON.", reply_markup=get_main_keyboard())
        return

    try:
        file = await message.bot.get_file(message.document.file_id)
        file_content = await message.bot.download_file(file.file_path)
        content_text = file_content.read().decode('utf-8')
        await message.answer("📥 Файл получен, начинаю обработку...")
        await _process_json_file_content(content_text, message)
    except Exception as e:
        logger.exception("Error processing JSON file")
        await message.answer(f"❌ Ошибка при обработке файла: {e}", reply_markup=get_main_keyboard())


@bot_router.message()
async def handle_text_or_file(message: types.Message) -> None:
    logger.info(f"Обработка сообщения от пользователя {message.from_user.id}")
    payload_text = message.text or ""

    try:
        parsed_data = json.loads(payload_text)
    except json.JSONDecodeError as e:
        await message.answer(f"❌ Некорректный JSON: {e}", reply_markup=get_main_keyboard())
        return

    if isinstance(parsed_data, list):
        await _process_multiple_wifi_records(parsed_data, message)
        return

    if isinstance(parsed_data, dict):
        success = await _process_single_wifi_record(parsed_data, message)
        if success:
            await message.answer("✅ Данные успешно сохранены в таблицу!", reply_markup=get_main_keyboard())
        return

    await message.answer("❌ Неподдерживаемый формат JSON. Ожидается объект или массив.", reply_markup=get_main_keyboard())
