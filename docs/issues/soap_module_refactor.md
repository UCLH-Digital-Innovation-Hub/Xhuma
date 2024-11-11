# Proposed SOAP Module Improvements

## Current State
The SOAP module currently has basic logging and Redis operations mixed into the main request handling logic.

## Proposed Changes

### Logging Improvements
- Move logging logic to a dedicated middleware
- Add structured logging format
- Include request/response correlation IDs
- Add timing information for requests

### Redis Operations
- Move Redis operations to a dedicated helper class
- Add proper error handling for Redis operations
- Implement consistent TTL for CEID to NHS number mappings
- Add Redis connection error recovery

These changes will improve code organization and make the module more maintainable while keeping its core functionality intact.
