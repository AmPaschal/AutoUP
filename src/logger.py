"""  Logger configuration """

# System
from datetime import datetime
import logging

def init_logging(base: str):
    """Creates the basic configuration"""
    print("PUNTO A: ", base)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print("PUNTO B")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
        handlers=[
            logging.FileHandler(f"logs/{base}_{timestamp}.log"),
            logging.StreamHandler()
        ]
    )
    print("PUNTO C")

def setup_logger(name: str):
    """ Contigures a new logger"""
    return logging.getLogger(name)
