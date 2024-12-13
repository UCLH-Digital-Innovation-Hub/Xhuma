"""
PostgreSQL logging handlers for the Xhuma application.

This module provides PostgreSQL-specific logging handlers for storing
structured logs in the database with correlation IDs and request details.
"""

import logging
import json
from typing import Dict, Any
from datetime import datetime
import psycopg2
from psycopg2.extras import Json
from uuid import uuid4

from .handlers import (
    correlation_id_ctx_var,
    nhs_number_ctx_var,
    request_type_ctx_var
)

class PostgresLogHandler(logging.Handler):
    """
    Custom logging handler that writes log records to PostgreSQL database.
    
    :param dsn: Database connection string
    :type dsn: str
    :param table: Table name for storing logs
    :type table: str
    """
    
    def __init__(self, dsn: str, table: str = "application_logs"):
        super().__init__()
        self.dsn = dsn
        self.table = table
        self.conn = None
        self.connect()
    
    def connect(self) -> None:
        """Establish connection to PostgreSQL database."""
        try:
            self.conn = psycopg2.connect(self.dsn)
            self.conn.autocommit = True
        except psycopg2.Error as e:
            print(f"Failed to connect to PostgreSQL: {e}")
            raise
    
    def format_record(self, record: logging.LogRecord) -> Dict[str, Any]:
        """
        Format the log record into a structured dictionary for database storage.
        
        :param record: The log record to format
        :type record: logging.LogRecord
        :return: Formatted log record as a dictionary
        :rtype: Dict[str, Any]
        """
        correlation_id = correlation_id_ctx_var.get(None)
        nhs_number = nhs_number_ctx_var.get(None)
        request_type = request_type_ctx_var.get(None)
        
        # Generate a correlation ID if none exists
        if correlation_id is None:
            correlation_id = str(uuid4())
        
        # Don't store "N/A" values in the database
        if nhs_number == "N/A":
            nhs_number = None
        if request_type == "N/A":
            request_type = None
        
        request_details = getattr(record, 'request_details', None)
        response_details = getattr(record, 'response_details', None)
        
        extra = {
            'pathname': record.pathname,
            'funcName': record.funcName,
            'lineno': record.lineno,
        }
        
        # Add any extra attributes from the record
        for key, value in record.__dict__.items():
            if key not in ('correlation_id', 'nhs_number', 'msg', 'args', 
                         'exc_info', 'exc_text', 'request_details', 'response_details',
                         'request_type', 'level', 'module', 'message'):
                try:
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)
        
        # Format message
        message = {
            'message': record.getMessage(),
            'module': record.name,
            'level': record.levelname,
            'correlation_id': correlation_id,
            'nhs_number': nhs_number,
            'request_type': request_type,
            'timestamp': datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S'),
            **extra
        }
        
        return {
            'timestamp': datetime.fromtimestamp(record.created),
            'correlation_id': correlation_id,
            'nhs_number': nhs_number,
            'request_type': request_type,
            'level': record.levelname,
            'module': record.name,
            'message': Json(message),
            'request_details': Json(request_details) if request_details else None,
            'response_details': Json(response_details) if response_details else None,
            'extra_data': Json(extra)
        }
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a record to the PostgreSQL database.
        
        :param record: The log record to emit
        :type record: logging.LogRecord
        """
        if self.conn is None or self.conn.closed:
            self.connect()
        
        try:
            formatted_record = self.format_record(record)
            
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO application_logs 
                    (timestamp, correlation_id, nhs_number, request_type, level, 
                     module, message, request_details, response_details, extra_data)
                    VALUES (%(timestamp)s, %(correlation_id)s, %(nhs_number)s, 
                            %(request_type)s, %(level)s, %(module)s, %(message)s,
                            %(request_details)s, %(response_details)s, %(extra_data)s)
                """, formatted_record)
                
        except Exception as e:
            self.handleError(record)
    
    def close(self) -> None:
        """Close the database connection when the handler is closed."""
        if self.conn is not None:
            self.conn.close()
        super().close()
