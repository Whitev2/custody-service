import logging
import os
import sys
from os import getenv

from pathlib import Path


# Определим корень проекта
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class RelativePathFormatter(logging.Formatter):
    def __init__(self, fmt=None):
        super().__init__(fmt)

    def format(self, record):
        try:
            # Получаем относительный путь
            rel_path = os.path.relpath(record.pathname, PROJECT_ROOT)
        except ValueError:
            rel_path = record.pathname

        # Сохраняем оригинальный pathname
        record.relative_path = rel_path
        return super().format(record)


def setup_logging(log_level: str = None):
    """Настройка единого логгера"""

    # Проверяем, был ли уже настроен логгер
    if logging.getLogger("app").hasHandlers():
        return

    # Определяем уровень логирования
    if log_level is None:
        log_level = getenv("LOG_LEVEL", "DEBUG")
    
    # Преобразуем строку в уровень логирования
    numeric_level = getattr(logging, log_level.upper(), logging.DEBUG)

    # Форматтер
    formatter = RelativePathFormatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(relative_path)s:%(funcName)s:%(lineno)d | %(message)s"
    )

    # Обработчик для вывода в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Единый логгер для приложения
    app_logger = logging.getLogger("app")
    app_logger.setLevel(numeric_level)
    app_logger.addHandler(console_handler)
    app_logger.propagate = False
