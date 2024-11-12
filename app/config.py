"""
Xhuma Logging Configuration Module

This module provides centralized logging configuration for the Xhuma middleware service.
It configures structured logging with correlation IDs, security event tracking, and 
integration with fastapi-observability for comprehensive system observability.
"""

import logging
import os
from enum import Enum
from typing import Dict, Any
from uuid import uuid4

# Environment-based configuration
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

class LogLevel(str, Enum):
    """Enum for log levels to ensure consistent usage across the application"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

# Logging configuration dictionary
LOGGING_CONFIG: Dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": """
                timestamp: %(asctime)s
                name: %(name)s
                level: %(levelname)s
                correlation_id: %(correlation_id)s
                nhs_number: %(nhs_number)s
                message: %(message)s
                module: %(module)s
                function: %(funcName)s
                line: %(lineno)d
                path: %(pathname)s
            """.replace("\n", " ").strip(),
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "standard": {
            "format": "[%(asctime)s] [%(correlation_id)s] [%(levelname)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard" if ENVIRONMENT == "development" else "json",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": "logs/xhuma.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
        }
    },
    "loggers": {
        "xhuma": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.security": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "xhuma.soap": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.pds": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.gpconnect": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.ccda": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# FastAPI Observability Configuration
# Based on https://github.com/blueswen/fastapi-observability
FASTAPI_OBSERVABILITY_CONFIG = {
    "metrics_route": "/metrics",
    "metrics_route_name": "metrics",
    "should_group_status_codes": True,
    "should_ignore_untemplated": True,
    "should_group_untemplated": True,
    "should_round_latency_decimals": True,
    "excluded_handlers": ["/metrics", "/health"],
    "buckets": [0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
    "should_include_handler_name": True,
    "should_include_method": True,
    "should_include_status": True,
    "should_include_hostname": True,
    "hostname_label": "instance",
    "label_names": ("method", "handler", "status", "hostname"),
    "namespace": "fastapi",
    "subsystem": "xhuma",
    "env_var_name": "ENABLE_METRICS",
    "metrics_route_format": "plaintext",
}

# Security Logging Configuration
SECURITY_LOG_CONFIG = {
    "log_failed_attempts": True,
    "log_successful_attempts": True,
    "sensitive_fields": [
        "password",
        "token",
        "authorization",
        "api_key",
    ],
}

# Correlation ID Configuration
CORRELATION_ID_CONFIG = {
    "header_name": "X-Correlation-ID",
    "validate_uuid": True,
    "generator": lambda: str(uuid4()),
}

def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance for the specified module.
    
    Args:
        name (str): The name of the module requesting the logger.
        
    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(f"xhuma.{name}")
    return logger

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)
