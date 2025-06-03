import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from settings import LOG_LEVEL, ENABLE_CONSOLE_LOG


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


# Log formatting style
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FORMAT_CONSOLE = "\033[92m%(asctime)s\033[0m - \033[94m%(name)s\033[0m - %(levelname)s - %(message)s"

# Convert string level from settings to actual logging constant
LOG_LEVEL = getattr(logging, LOG_LEVEL, logging.DEBUG)

def setup_logger(name: str) -> logging.Logger:
    # Get or create a logger instance with the given name
    logger = logging.getLogger(name)

    # Set the logging level based on config (e.g. DEBUG, INFO, etc.)
    logger.setLevel(LOG_LEVEL)

    # Prevent adding duplicate handlers if logger was already set up
    if not logger.handlers:
        # Optionally create a handler that logs to the console (stdout)
        if ENABLE_CONSOLE_LOG:
            console_handler = SafeStreamHandler(sys.stdout)
            # Apply a consistent format to console logs
            console_handler.setFormatter(logging.Formatter(LOG_FORMAT_CONSOLE))
            # Attach the console handler to the logger
            logger.addHandler(console_handler)

        # Replace dots in logger name to create a safe filename
        # e.g. "server.main" becomes "server_main.log"
        safe_name = name.replace('.', '_')

        # Create a rotating file handler that writes to a file
        # - Limits log file size to ~1MB
        # - Keeps up to 3 old backups (e.g. server_main.log.1, .2, .3)
        file_handler = RotatingFileHandler(
            f"{safe_name}.log", maxBytes=1_000_000, backupCount=3
        )

        # Apply the same log format to the file output
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

        # Attach the file handler to the logger
        logger.addHandler(file_handler)

    # Return the fully configured logger instance
    return logger
