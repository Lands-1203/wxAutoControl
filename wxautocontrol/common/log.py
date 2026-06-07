from __future__ import annotations

import io
import logging
import sys
from datetime import datetime
from pathlib import Path

import colorama

from .types import ControlConfig


colorama.init()

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="ignore")


LOG_COLORS = {
    "DEBUG": colorama.Fore.CYAN,
    "INFO": colorama.Fore.GREEN,
    "WARNING": colorama.Fore.YELLOW,
    "ERROR": colorama.Fore.RED,
    "CRITICAL": colorama.Fore.MAGENTA,
}


class ColoredFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        return f"{LOG_COLORS.get(record.levelname, '')}{message}{colorama.Style.RESET_ALL}"


class ControlLogger:
    name = "wxAutoControl"

    def __init__(self):
        self.file_handler = None
        self.logger = self._setup_logger()
        self.set_debug(False)

    def _setup_logger(self) -> logging.Logger:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("comtypes").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        root_logger.handlers.clear()

        fmt = "%(asctime)s [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d]  %(message)s"
        handler = logging.StreamHandler()
        handler.setFormatter(ColoredFormatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S"))
        handler.setLevel(logging.DEBUG)
        self.console_handler = handler
        root_logger.addHandler(handler)
        return logging.getLogger(self.name)

    def _ensure_file_logger(self):
        if not ControlConfig.ENABLE_FILE_LOGGER or self.file_handler is not None:
            return
        log_dir = Path("wxauto_logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
        self.file_handler = logging.FileHandler(log_file, encoding="utf-8")
        self.file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d]  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.file_handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(self.file_handler)

    def set_debug(self, debug: bool = False):
        self.console_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    def debug(self, msg: str, stacklevel: int = 2, *args, **kwargs):
        self._ensure_file_logger()
        self.logger.debug(msg, *args, stacklevel=stacklevel, **kwargs)

    def info(self, msg: str, stacklevel: int = 2, *args, **kwargs):
        self._ensure_file_logger()
        self.logger.info(msg, *args, stacklevel=stacklevel, **kwargs)

    def warning(self, msg: str, stacklevel: int = 2, *args, **kwargs):
        self._ensure_file_logger()
        self.logger.warning(msg, *args, stacklevel=stacklevel, **kwargs)

    def error(self, msg: str, stacklevel: int = 2, *args, **kwargs):
        self._ensure_file_logger()
        self.logger.error(msg, *args, stacklevel=stacklevel, **kwargs)


log = ControlLogger()
