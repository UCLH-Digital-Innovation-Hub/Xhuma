"""
Structured JSON Logging Configuration

This module sets up structured JSON logging with correlation ID support
for the Xhuma application.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

# Context variable for correlation ID (thread-safe)
correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.
    
    Outputs log records as JSON with timestamp, level, message,
    and correlation_id if available.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as JSON.
        
        Args:
            record: The log record to format
            
        Returns:
            JSON string representation of the log record
        """
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add correlation_id if available
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_data["correlation_id"] = correlation_id
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class CorrelationIDFilter(logging.Filter):
    """
    Logging filter that adds correlation ID from context to log records.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add correlation ID to the log record if available in context.
        
        Args:
            record: The log record to process
            
        Returns:
            Always returns True to allow the record through
        """
        correlation_id = correlation_id_var.get(None)
        record.correlation_id = correlation_id
        return True


def setup_logging() -> None:
    """
    Set up structured JSON logging for the application.
    
    Configures:
    - JSON formatter for structured logs
    - Console handler (stdout)
    - File handler (xhuma.log)
    - Correlation ID filter for all handlers
    """
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove any existing handlers
    root_logger.handlers = []
    
    # Create JSON formatter
    json_formatter = JSONFormatter()
    
    # Create correlation ID filter
    correlation_filter = CorrelationIDFilter()
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(json_formatter)
    console_handler.addFilter(correlation_filter)
    root_logger.addHandler(console_handler)
    
    # File handler (xhuma.log)
    file_handler = logging.FileHandler("xhuma.log")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(json_formatter)
    file_handler.addFilter(correlation_filter)
    root_logger.addHandler(file_handler)
    
    print("✓ Structured JSON logging configured")
