# logging conf of all services
import logging
import sys
import os
from dotenv import load_dotenv

# load environment variables from .env file
load_dotenv()


def setup_logging(service_name: str = "service"):
    # configure logging for a service.
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # convert string to logging level
    numeric_level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=f'%(asctime)s - [{service_name}] - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(service_name)
