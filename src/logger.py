"""  Logger configuration """

# System
from typing import Optional
import logging
import sys


def init_logging(log_file: Optional[str]):
    """Creates the basic configuration"""
    handlers = []

    if log_file:
        # Use UTF-8 encoding for file handlers to support Unicode characters
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    else:
        # Configure StreamHandler with UTF-8 encoding for console output
        stream_handler = logging.StreamHandler(sys.stdout)
        # Reconfigure stdout to use UTF-8 on Windows
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        handlers.append(stream_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
        handlers=handlers
    )


def setup_logger(name: str):
    """ Configures a new logger"""
    return logging.getLogger(name)
