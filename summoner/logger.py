"""
Handle logging specifics
to this context
like details of how things are consistently formatted
"""
import sys
import os
import json
import logging
import datetime
import re

from typing import Optional, Any
from logging.handlers import RotatingFileHandler

# This makes Logger importable from logger.py
#pylint:disable=unused-import
from logging import Logger


# Log formatting style
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FORMAT_CONSOLE = \
    "\033[92m%(asctime)s\033[0m - \033[94m%(name)s\033[0m - %(levelname)s - %(message)s"

class SafeStreamHandler(logging.StreamHandler):
    """
    A StreamHandler that suppresses BlockingIOError during stdout congestion.

    Useful in high-throughput async systems where stdout may block under load.
    Silently drops log messages instead of crashing or flooding with logging errors.

    Example:
        logger = logging.getLogger("MyAgent")
        handler = SafeStreamHandler(sys.stdout)
        logger.addHandler(handler)
    """
    def emit(self, record):
        try:
            super().emit(record)
        except BlockingIOError:
            # Prevent stdout congestion from crashing the logger.
            # Message is dropped, but system remains stable.
            pass

# class BaseFormatter(logging.Formatter):
#     """
#     Base formatter that uses datetime.strftime to support '%f' for microseconds.
#     """
#     def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
#         # Use datetime to allow %f in format strings
#         dt = datetime.datetime.fromtimestamp(record.created)
#         if datefmt:
#             return dt.strftime(datefmt)
#         return dt.strftime("%Y-%m-%d %H:%M:%S")

class BaseFormatter(logging.Formatter):
    """
    Base formatter supporting configurable microsecond precision via '%<n>f',
    caching the parsed datefmt to avoid repetitive regex work.

    Example: datefmt '%Y-%m-%d %H:%M:%S.%3f' yields three-digit milliseconds.
    """
    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None):
        super().__init__(fmt=fmt, datefmt=None)
        # Cache raw datefmt and parsing results
        self._raw_datefmt = datefmt
        self._precision: Optional[int] = None
        self._prefix_fmt: Optional[str] = None
        self._suffix_fmt: Optional[str] = None
        self._use_full_micro: bool = False

        if datefmt:
            # Try matching '%<n>f'
            match = re.search(r'%(\d+)f', datefmt)
            if match:
                self._precision = max(0, min(6, int(match.group(1))))
                parts = re.split(r'%\d+f', datefmt)
                self._prefix_fmt = parts[0]
                self._suffix_fmt = parts[1] if len(parts) > 1 else ''
            elif '%f' in datefmt:
                # Standard '%f' -> three-digit ms
                self._precision = 3
                parts = datefmt.split('%f')
                self._prefix_fmt = parts[0]
                self._suffix_fmt = parts[1] if len(parts) > 1 else ''
                self._use_full_micro = False
            else:
                # No micro directive, use raw fmt
                self._precision = None

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.datetime.fromtimestamp(record.created)
        dfmt = self._raw_datefmt
        if dfmt and self._precision is not None:
            # Prefix timestamp
            ts = dt.strftime(self._prefix_fmt or '')
            # Compute micro or milli
            if self._use_full_micro:
                ms = f"{dt.microsecond:06d}"[:self._precision]
            else:
                factor = 10 ** (6 - self._precision)
                ms = f"{dt.microsecond // factor:0{self._precision}d}"
            # Suffix timestamp
            return ts + ms + (self._suffix_fmt or '')
        # Fallback
        return dt.strftime(dfmt) if dfmt else dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

class TextFormatter(BaseFormatter):
    """
    Text formatter that can filter dict-messages by log_keys.
    """
    def __init__(self, fmt: str, datefmt: Optional[str], log_keys: Optional[list[str]]):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.log_keys = log_keys

    # def format(self, record: logging.LogRecord) -> str:
    #     # If the message is a dict and we have a whitelist, apply it
    #     if isinstance(record.msg, dict) and self.log_keys is not None:
    #         record.msg = {k: record.msg.get(k) for k in self.log_keys if k in record.msg}
    #     return super().format(record)

    def format(self, record: logging.LogRecord) -> str:
        # Filter dict-messages without mutating the original
        if isinstance(record.msg, dict) and self.log_keys is not None:
            original = record.msg
            filtered = {k: original.get(k) for k in self.log_keys if k in original}
            record.msg = filtered
            result = super().format(record)
            record.msg = original
            return result
        return super().format(record)

class JsonFormatter(BaseFormatter):
    """
    Formatter that emits logs as JSON objects, filtering by specified log_keys.
    """
    def __init__(self, fmt: str, datefmt: Optional[str], log_keys: Optional[list[str]]):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.log_keys = log_keys

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": self.formatTime(record, self._raw_datefmt),
            "name":      record.name,
            "level":     record.levelname,
        }
        if isinstance(record.msg, dict):
            payload = (record.msg if self.log_keys is None
                       else {k: record.msg.get(k) for k in self.log_keys if k in record.msg})
        else:
            payload = record.getMessage()
        base["message"] = payload # type: ignore
        return json.dumps(base, default=str)

def get_logger(name: str) -> logging.Logger:
    """
    Create or retrieve a logger by name without handlers.
    Handlers will be added later via configure_logger.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.NOTSET)
    logger.propagate = False
    return logger

def configure_logger(logger: logging.Logger, logger_cfg: dict[str, Any]) -> None:
    """
    Configure the given logger according to the provided config dict.

    Expected logger_cfg keys:
      - log_level (str)
      - enable_console_log (bool)
      - console_log_format (str)
      - enable_file_log (bool)
      - enable_json_log (bool)
      - log_file_path (str)
      - log_format (str)
      - date_format (str)
      - max_file_size (int)
      - backup_count (int)
      - log_keys (list[str] or None)
    """
    # 0) Remove handlers managed in the core SDK (else use handler._keep = True)
    for handler in list(logger.handlers):
        if getattr(handler, "_keep", False):
            continue
        logger.removeHandler(handler)

    # 1) Set the logger's level once
    log_level = getattr(logging, logger_cfg.get("log_level", "DEBUG").upper(), logging.DEBUG)
    logger.setLevel(log_level)

    # 2) Attach console/file handlers according to config.

    # console
    if logger_cfg.get("enable_console_log", True):

        console_log_format = logger_cfg.get("console_log_format", LOG_FORMAT_CONSOLE)
        date_format = logger_cfg.get("date_format")

        console_handler = SafeStreamHandler(sys.stdout)
        console_handler.setFormatter(
            TextFormatter(console_log_format, date_format, logger_cfg.get("log_keys")))
        logger.addHandler(console_handler)

    # file
    if logger_cfg.get("enable_file_log", False):

        log_dir = logger_cfg.get("log_file_path", "")
        if log_dir and not os.path.isdir(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        safe_name = logger.name.replace(".", "_")
        path = os.path.join(log_dir or ".", f"{safe_name}.log")

        max_file_size   = logger_cfg.get("max_file_size", 1_000_000)
        backup_count    = logger_cfg.get("backup_count", 3)
        log_format      = logger_cfg.get("log_format", LOG_FORMAT)
        date_format     = logger_cfg.get("date_format")

        file_handler = RotatingFileHandler(path,
                                           maxBytes=max_file_size,
                                           backupCount=backup_count)
        if logger_cfg.get("enable_json_log", False):
            file_handler.setFormatter(
                JsonFormatter(log_format, date_format, logger_cfg.get("log_keys")))
        else:
            file_handler.setFormatter(
                TextFormatter(log_format, date_format, logger_cfg.get("log_keys")))
        logger.addHandler(file_handler)
