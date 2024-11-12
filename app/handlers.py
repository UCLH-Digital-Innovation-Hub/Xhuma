"""
Custom logging handlers for the Xhuma application.

This module provides custom logging handlers for enhanced logging capabilities,
particularly focused on request tracing and correlation ID management.
"""

import logging
import threading
from typing import Optional
from contextvars import ContextVar

# Context variable to store correlation ID
correlation_id_ctx_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
nhs_number_ctx_var: ContextVar[Optional[str]] = ContextVar("nhs_number", default=None)

class ContextualFilter(logging.Filter):
    """
    Logging filter that adds correlation ID and NHS number from context to log records.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add correlation ID and NHS number to the log record if available in context.
        
        :param record: The log record to process
        :type record: logging.LogRecord
        :return: Always returns True to allow the record through
        :rtype: bool
        """
        correlation_id = correlation_id_ctx_var.get(None)
        nhs_number = nhs_number_ctx_var.get(None)
        
        record.correlation_id = correlation_id or "N/A"
        record.nhs_number = nhs_number or "N/A"
        
        return True

class RequestContextManager:
    """
    Context manager for handling request-specific context variables.
    """
    
    def __init__(self, correlation_id: str, nhs_number: Optional[str] = None):
        """
        Initialize the context manager with correlation ID and optional NHS number.
        
        :param correlation_id: The correlation ID for the request
        :type correlation_id: str
        :param nhs_number: The NHS number associated with the request
        :type nhs_number: Optional[str]
        """
        self.correlation_id = correlation_id
        self.nhs_number = nhs_number
        self.correlation_token = None
        self.nhs_number_token = None
    
    def __enter__(self):
        """
        Set the correlation ID and NHS number in the context.
        """
        self.correlation_token = correlation_id_ctx_var.set(self.correlation_id)
        if self.nhs_number:
            self.nhs_number_token = nhs_number_ctx_var.set(self.nhs_number)
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Clear the correlation ID and NHS number from the context.
        """
        correlation_id_ctx_var.reset(self.correlation_token)
        if self.nhs_number_token:
            nhs_number_ctx_var.reset(self.nhs_number_token)

def setup_request_context(correlation_id: str, nhs_number: Optional[str] = None) -> RequestContextManager:
    """
    Create a context manager for request-specific logging context.
    
    :param correlation_id: The correlation ID for the request
    :type correlation_id: str
    :param nhs_number: The NHS number associated with the request
    :type nhs_number: Optional[str]
    :return: A context manager for the request context
    :rtype: RequestContextManager
    """
    return RequestContextManager(correlation_id, nhs_number)
