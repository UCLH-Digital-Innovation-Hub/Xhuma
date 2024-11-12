"""
Custom logging handlers for the Xhuma application.

This module provides custom logging handlers for enhanced logging capabilities,
particularly focused on request tracing and correlation ID management with
Postgres storage support.
"""

import logging
import json
from typing import Optional, Dict, Any, Tuple
from contextvars import ContextVar, Token
from datetime import datetime
import psycopg2
from psycopg2.extras import Json, DictCursor
from uuid import UUID, uuid4

# Context variables for request tracking
correlation_id_ctx_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
nhs_number_ctx_var: ContextVar[Optional[str]] = ContextVar("nhs_number", default=None)
request_type_ctx_var: ContextVar[Optional[str]] = ContextVar("request_type", default=None)

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
        request_type = request_type_ctx_var.get(None)
        
        record.correlation_id = correlation_id or "N/A"
        record.nhs_number = nhs_number or "N/A"
        record.request_type = request_type or "N/A"
        
        return True

class RequestContextManager:
    """
    Context manager for handling request-specific context variables.
    """
    
    def __init__(self, correlation_id: str, nhs_number: Optional[str] = None, 
                 request_type: Optional[str] = None):
        """
        Initialize the context manager with correlation ID and optional NHS number.
        
        :param correlation_id: The correlation ID for the request
        :type correlation_id: str
        :param nhs_number: The NHS number associated with the request
        :type nhs_number: Optional[str]
        :param request_type: The type of request being processed
        :type request_type: Optional[str]
        """
        self.correlation_id = correlation_id
        self.nhs_number = nhs_number
        self.request_type = request_type
        self.tokens: Dict[str, Token] = {}
    
    def __enter__(self):
        """
        Set the correlation ID and NHS number in the context.
        
        :return: self for context manager protocol
        :rtype: RequestContextManager
        """
        self.tokens['correlation_id'] = correlation_id_ctx_var.set(self.correlation_id)
        
        if self.nhs_number is not None:
            self.tokens['nhs_number'] = nhs_number_ctx_var.set(self.nhs_number)
            
        if self.request_type is not None:
            self.tokens['request_type'] = request_type_ctx_var.set(self.request_type)
            
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Clear the context variables using their respective tokens.
        """
        for var_name, token in self.tokens.items():
            if var_name == 'correlation_id':
                correlation_id_ctx_var.reset(token)
            elif var_name == 'nhs_number':
                nhs_number_ctx_var.reset(token)
            elif var_name == 'request_type':
                request_type_ctx_var.reset(token)

def setup_request_context(correlation_id: str, nhs_number: Optional[str] = None,
                        request_type: Optional[str] = None) -> RequestContextManager:
    """
    Create a context manager for request-specific logging context.
    
    :param correlation_id: The correlation ID for the request
    :type correlation_id: str
    :param nhs_number: The NHS number associated with the request
    :type nhs_number: Optional[str]
    :param request_type: The type of request being processed
    :type request_type: Optional[str]
    :return: A context manager for the request context
    :rtype: RequestContextManager
    """
    return RequestContextManager(correlation_id, nhs_number, request_type)

class CorrelationManager:
    """
    Manages correlation IDs and their reuse for repeated requests.
    """
    
    def __init__(self, dsn: str):
        """
        Initialize the correlation manager.
        
        :param dsn: Database connection string
        :type dsn: str
        """
        self.dsn = dsn
        self.conn = None
        self.connect()
    
    def connect(self) -> None:
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(self.dsn)
            self.conn.autocommit = True
        except psycopg2.Error as e:
            print(f"Failed to connect to PostgreSQL: {e}")
            raise
    
    def get_or_create_correlation_id(self, nhs_number: str, request_type: str) -> Tuple[UUID, bool]:
        """
        Get existing correlation ID or create new one for NHS number and request type.
        
        :param nhs_number: NHS number for the request
        :type nhs_number: str
        :param request_type: Type of request (e.g., ITI-47, ITI-38)
        :type request_type: str
        :return: Tuple of (correlation_id, is_new)
        :rtype: Tuple[UUID, bool]
        """
        if self.conn is None or self.conn.closed:
            self.connect()
        
        try:
            with self.conn.cursor(cursor_factory=DictCursor) as cur:
                # Try to get existing correlation ID
                cur.execute("""
                    SELECT correlation_id 
                    FROM correlation_mappings 
                    WHERE nhs_number = %s AND request_type = %s
                """, (nhs_number, request_type))
                
                result = cur.fetchone()
                
                if result:
                    return UUID(result['correlation_id']), False
                
                # Create new correlation ID
                new_correlation_id = uuid4()
                cur.execute("""
                    INSERT INTO correlation_mappings 
                    (nhs_number, correlation_id, request_type)
                    VALUES (%s, %s, %s)
                """, (nhs_number, str(new_correlation_id), request_type))
                
                return new_correlation_id, True
                
        except Exception as e:
            print(f"Error managing correlation ID: {e}")
            raise
    
    def close(self) -> None:
        """Close the database connection."""
        if self.conn is not None:
            self.conn.close()
