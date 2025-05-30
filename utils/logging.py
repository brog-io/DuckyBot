import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Constants
LOG_DIR = "logs"
LOG_FILE_SIZE = 10_000_000
LOG_BACKUP_COUNT = 30
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def setup_logging() -> logging.Logger:
    """
    Set up human-readable logging for both console and .log file with rotation.
    """

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    timestamp = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(LOG_DIR, f"discord_{timestamp}.log")

    root_logger = logging.getLogger()
    log_level = getattr(logging, DEFAULT_LOG_LEVEL, logging.INFO)
    root_logger.setLevel(log_level)

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_format = logging.Formatter("%(levelname)-8s %(name)-15s: %(message)s")
    console_handler.setFormatter(console_format)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_FILE_SIZE, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s"
    )
    file_handler.setFormatter(file_format)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for logger_name in ["discord", "discord.http", "discord.gateway", "aiohttp"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    return logging.getLogger(__name__)
