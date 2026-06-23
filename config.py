import os
from dotenv import load_dotenv

load_dotenv()

# ========== СЕКРЕТЫ (из .env) ==========
API_KEY = os.getenv('MISTRAL_API_KEY', '')

# ========== ПУБЛИЧНЫЕ НАСТРОЙКИ (меняем здесь) ==========
# Режимы работы
TEST_MODE = False  # True - тестовый режим (5 задач), False - полный
TEST_SIZE = 5

# Задержки
DELAY = 1  # секунды между запросами
MAX_RETRIES = 3
RETRY_DELAY = 5

# Параметры генерации
MAX_TOKENS = 5000
TEMPERATURE = 0.3

# Пути
INPUT_FOLDER = 'input_data'
OUTPUT_FOLDER = 'output'
OUTPUT_FILE = os.path.join(OUTPUT_FOLDER, '5_6_class_shevkin_merged.xlsx')
TARGET_FILE = 'tekstovye_zadachi_po_matematike.docx'

# ========== Исключения ==========
EXCLUDE_TASKS = [
    'Натуральные числа',
    'Дроби',
    'Пропорции',
    'Проценты',
    'Уравнения',
    'Решение задач с помощью уравнений',
    'Задачи на повторение'
]  # задачи, которые не нужно обрабатывать

# ========== ПРОМПТ ДЛЯ ГЕНЕРАЦИИ РЕШЕНИЙ ==========
PROMPT_TEMPLATE = """Реши задачу {task_text} пошагово, шаги при этом облачи в html тэги,
не используй иксы в виде переменных. Выводи только теги.
"""
