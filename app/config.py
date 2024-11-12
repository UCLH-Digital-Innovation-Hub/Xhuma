"""
Xhuma Configuration Module

This module provides centralized configuration for the Xhuma middleware service,
including logging, metrics, and tracing settings.
"""

import logging
import os
from enum import Enum
from typing import Dict, Any

# Environment-based configuration
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Database configuration
DB_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/xhuma")

class LogLevel(str, Enum):
    """Enum for log levels to ensure consistent usage across the application"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

# OpenTelemetry Configuration
OTEL_CONFIG = {
    "service_name": "xhuma",
    "service_version": "1.0.0",
    "deployment_environment": ENVIRONMENT,
    "traces_exporter": {
        "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        "insecure": True
    }
}

# FastAPI Observability Configuration
FASTAPI_OBSERVABILITY_CONFIG = {
    "app_name": "xhuma",
    "metrics_path": "/metrics",
    "should_gzip": True,
    "should_include_status_code_metrics": True,
    "should_include_response_time_metrics": True,
    "excluded_urls": ["/metrics", "/health", "/docs", "/openapi.json"],
    "buckets": [0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0],
}

# Prometheus Metric Configurations
METRIC_CONFIGS = {
    "request_duration_buckets": [0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0],
    "default_labels": {
        "service": "xhuma",
        "environment": ENVIRONMENT
    }
}

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
                request_type: %(request_type)s
                message: %(message)s
                module: %(module)s
                function: %(funcName)s
                line: %(lineno)d
                path: %(pathname)s
                trace_id: %(otelTraceID)s
                span_id: %(otelSpanID)s
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
        },
        "database": {
            "class": "app.log_handlers.PostgresLogHandler",
            "formatter": "json",
            "dsn": DB_DSN,
        }
    },
    "loggers": {
        "xhuma": {
            "handlers": ["console", "file", "database"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.security": {
            "handlers": ["console", "file", "database"],
            "level": "INFO",
            "propagate": False,
        },
        "xhuma.soap": {
            "handlers": ["console", "file", "database"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.pds": {
            "handlers": ["console", "file", "database"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.gpconnect": {
            "handlers": ["console", "file", "database"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "xhuma.ccda": {
            "handlers": ["console", "file", "database"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# Correlation ID Configuration
CORRELATION_ID_CONFIG = {
    "header_name": "X-Correlation-ID",
    "validate_uuid": True,
    "generator": "uuid4"
}

# Request Type Mapping
REQUEST_TYPES = {
    "ITI-47": "Patient Demographics Query",
    "ITI-38": "Cross Gateway Query",
    "ITI-39": "Cross Gateway Retrieve",
    "CCDA": "CCDA Conversion",
    "SDS": "Spine Directory Service",
    "PDS": "Patient Demographics Service"
}

# Security Event Types
SECURITY_EVENTS = {
    "AUTH_SUCCESS": "Authentication Success",
    "AUTH_FAILURE": "Authentication Failure",
    "ACCESS_DENIED": "Access Denied",
    "TOKEN_EXPIRED": "Token Expired",
    "INVALID_TOKEN": "Invalid Token"
}

def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance for the specified module.
    
    :param name: The name of the module requesting the logger
    :type name: str
    :return: Configured logger instance
    :rtype: logging.Logger
    """
    return logging.getLogger(f"xhuma.{name}")

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)
