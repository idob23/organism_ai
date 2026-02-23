import traceback
import sys
import logging
from pathlib import Path
from datetime import datetime
from config.settings import settings

# Настраиваем стандартный logging
log_dir = Path(settings.log_dir)
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(
            log_dir / f"errors-{datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ]
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, context: str, exc: Exception) -> str:
    """Log full exception with traceback. Returns formatted error string."""
    tb = traceback.format_exc()
    error_msg = f"{context}: {type(exc).__name__}: {exc}"
    logger.error(f"{error_msg}\n{tb}")
    return error_msg


def log_warning(logger: logging.Logger, context: str, message: str) -> None:
    logger.warning(f"{context}: {message}")
