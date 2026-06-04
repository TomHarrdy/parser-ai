import logging.config
import sys

LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "[%(asctime)s] %(levelname)s %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "brief": {
            "format": "%(levelname)s | %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "default",
        },
    },
    "loggers": {
        "": {  # root
            "handlers": ["console"],
            "level": "INFO",
        },
        "src": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "httpx": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "aiogram": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}


def setup_logging(level: str = "INFO") -> None:
    LOG_CONFIG["loggers"][""]["level"] = level
    LOG_CONFIG["loggers"]["src"]["level"] = level
    logging.config.dictConfig(LOG_CONFIG)
