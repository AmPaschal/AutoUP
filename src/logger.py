"""  Logger configuration """

# System
from datetime import datetime
import logging
from typing import Optional

def init_logging(log_file: Optional[str]):
    """Creates the basic configuration"""
    handlers = []
    
    if log_file:
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
