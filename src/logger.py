"""  Logger configuration """

# System
from typing import Optional
import logging
import os


def init_logging(log_file: Optional[str]):
    """Creates the basic configuration"""
    handlers = []

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    else:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
        handlers=handlers
    )


def setup_logger(name: str):
    """ Configures a new logger"""
    return logging.getLogger(name)
