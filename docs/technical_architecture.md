# Technical Architecture Documentation

## System Overview
Xhuma is a stateless middleware service designed to facilitate the conversion of GP Connect structured records into CCDA (Consolidated Clinical Document Architecture) format. The service acts as an intermediary between Electronic Health Record (EHR) systems and NHS APIs, providing seamless integration and data transformation capabilities.

## Core Components

### 1. API Layer (FastAPI)
- Handles incoming ITI (IHE IT Infrastructure) requests
- Manages request/response lifecycle
- Implements REST endpoints for health systems integration
- Provides async operation support for improved performance

### 2. Authentication & Security (`app/security.py`)
- Implements JWT token validation
- Manages API authentication
- Handles request signing for NHS API interactions
- Ensures secure communication channels

### 3. GP Connect Integration (`app/gpconnect.py`)
- Manages communication with GP Connect APIs
- Handles structured record retrieval
- Implements error handling and retry logic
- Maintains API version compatibility

### 4. CCDA Conversion Engine (`app/ccda/`)
- Transforms FHIR bundles to CCDA format
- Implements conversion logic for clinical data
- Manages document structure and formatting
- Handles various clinical entry types

### 5. PDS Integration (`app/pds/`)
- Manages Patient Demographic Service lookups
- Handles patient identity verification
- Implements NHS number validation
- Manages demographic data retrieval

### 6. SOAP Handler (`app/soap/`)
- Processes SOAP requests and responses
- Manages XML transformations
- Implements ITI transaction support
- Handles SOAP fault scenarios

### 7. Caching Layer (Redis)
- Implements distributed caching
- Manages temporary data storage
- Optimizes repeated requests
- Handles cache invalidation

## Technical Stack

### Core Technologies
- Python 3.9+
- FastAPI (ASGI Framework)
- Redis (Caching)
- Docker (Containerization)
- Uvicorn (ASGI Server)

### Key Dependencies
- pydantic: Data validation
- python-jose: JWT handling
- requests: HTTP client
- lxml: XML processing
- pytest: Testing framework

## System Requirements

### Minimum Requirements
- Python 3.9 or higher
- Redis 6.0 or higher
- 2GB RAM
- 1 CPU core

### Recommended Requirements
- Python 3.11
- Redis 7.0
- 4GB RAM
- 2 CPU cores
- SSD storage

## Deployment Architecture

### Container Structure
```
├── Application Container
│   ├── FastAPI Application
│   ├── Uvicorn Server
│   └── Application Dependencies
└── Redis Container
    └── Redis Server
```

### Network Configuration
- Internal network for container communication
- Exposed ports:
  - 8000: Application API
  - 6379: Redis (internal only)

## Security Considerations

### Authentication
- JWT-based authentication
- NHS API authentication
- Token validation and verification

### Data Protection
- TLS 1.2+ for all communications
- Data encryption at rest
- Secure header handling
- Input validation and sanitization

### Compliance
- NHS Digital Standards
- NHS Supplier Conformance Assessment List: Structured Extended Testing, Spine Integration Testing, PDS onboarding
- GDPR requirements
- Health data protection regulations

## Performance Considerations

### Caching Strategy
- SDS endpoint caching
- CCDA document caching
- Cache invalidation policies
- Redis persistence configuration

### Optimization
- Async operations
- Connection pooling
- Resource optimization
- Response compression

## Monitoring and Logging

### Metrics Collection
- Request/Response timing
- Cache hit rates
- Error rates
- System resource usage

### Logging
- Application logs
- Error tracking
- Audit trails
- Performance metrics

## Error Handling

### Error Categories
- NHS API errors
- Conversion failures
- Authentication errors
- Network issues

### Recovery Procedures
- Automatic retries
- Fallback mechanisms
- Error reporting
- System recovery

## Maintenance and Support

### Regular Maintenance
- Cache cleanup
- Log rotation
- Performance monitoring
- Security updates

### Support Procedures
- Issue tracking
- Version control
- Documentation updates
- Dependency management
