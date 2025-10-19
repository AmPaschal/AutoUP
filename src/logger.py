"""  Logger configuration """

# System
from datetime import datetime
import logging

def init_logging():
    """Creates the basic configuration"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
        handlers=[
            # logging.FileHandler(f"logs/app_{timestamp}.log"),
            logging.StreamHandler()
        ]
    )

def setup_logger(name: str):
    """ Contigures a new logger"""
    return logging.getLogger(name)
