"""
Correlation ID Middleware

This module provides FastAPI middleware to handle correlation IDs for request tracing.
"""

from uuid import uuid4
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .logging_config import correlation_id_var


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to manage correlation IDs for requests.
    
    Extracts correlation ID from X-Correlation-ID header or generates a new UUID.
    Sets the correlation ID in context for logging and adds it to response headers.
    """
    
    async def dispatch(self, request: Request, call_next):
        """
        Process the request and manage correlation ID.
        
        Args:
            request: The incoming request
            call_next: The next middleware/handler in the chain
            
        Returns:
            Response with X-Correlation-ID header added
        """
        # Extract correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            correlation_id = str(uuid4())
        
        # Set correlation ID in context for logging
        token = correlation_id_var.set(correlation_id)
        
        try:
            # Process request
            response = await call_next(request)
            
            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            
            return response
        finally:
            # Reset context variable
            correlation_id_var.reset(token)
