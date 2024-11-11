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

## Monitoring & Observability Architecture

### 1. Metrics Collection (Prometheus)
- **Endpoint Metrics**
  - Request counts and rates
  - Response times
  - Error rates by type
  - Status code distribution

- **Cache Metrics**
  - Hit/miss rates
  - Cache size
  - Eviction rates
  - TTL statistics

- **Resource Metrics**
  - CPU usage
  - Memory utilization
  - Network I/O
  - Disk operations

### 2. Visualization (Grafana)
- **System Dashboards**
  - Real-time performance monitoring
  - Historical trends analysis
  - Resource utilization tracking
  - Error rate visualization

- **Business Metrics**
  - Transaction success rates
  - API usage patterns
  - Cache efficiency
  - Service availability

### 3. Logging Architecture (ELK Stack)
- **Log Collection**
  - Application logs
  - System logs
  - Access logs
  - Error logs

- **Log Processing**
  - Structured log formatting
  - Log enrichment
  - Pattern detection
  - Alert generation

- **Log Storage**
  - Indexed storage
  - Retention policies
  - Archival strategy
  - Search optimization

### 4. Distributed Tracing (OpenTelemetry)
- **Trace Collection**
  - Request tracing
  - Service dependencies
  - Performance bottlenecks
  - Error propagation

- **Trace Analysis**
  - Latency analysis
  - Error tracking
  - Service mapping
  - Performance optimization

## Testing Architecture

### 1. Unit Testing
- **Test Organization**
  - Feature-based test suites
  - Integration test suites
  - Mock implementations
  - Test fixtures

- **Test Coverage**
  - Code coverage tracking
  - Branch coverage
  - Integration points
  - Error scenarios

### 2. Integration Testing
- **Service Testing**
  - API endpoint testing
  - Database interactions
  - Cache operations
  - External service mocks

- **End-to-End Testing**
  - User flow testing
  - Error handling
  - Performance validation
  - Security testing

### 3. Performance Testing
- **Load Testing**
  - Concurrent user simulation
  - Resource utilization
  - Response time analysis
  - Bottleneck identification

- **Stress Testing**
  - System limits testing
  - Recovery testing
  - Failover scenarios
  - Resource exhaustion

## Security Architecture

### 1. Authentication
- JWT-based authentication
- NHS API authentication
- Token validation and verification

### 2. Data Protection
- TLS 1.2+ for all communications
- Data encryption at rest
- Secure header handling
- Input validation and sanitization

### 3. Compliance
- NHS Digital Standards
- NHS Supplier Conformance Assessment List: Structured Extended Testing, Spine Integration Testing, PDS onboarding
- GDPR requirements
- Health data protection regulations

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
  - 9090: Prometheus metrics
  - 3000: Grafana dashboards

## Error Handling Architecture

### 1. Error Categories
- NHS API errors
- Conversion failures
- Authentication errors
- Network issues

### 2. Recovery Procedures
- Automatic retries
- Fallback mechanisms
- Error reporting
- System recovery

## Maintenance Architecture

### 1. Regular Maintenance
- Cache cleanup
- Log rotation
- Performance monitoring
- Security updates

### 2. Support Procedures
- Issue tracking
- Version control
- Documentation updates
- Dependency management

## Health Check Architecture

### 1. Liveness Probes
- Basic application health
- Critical service checks
- Resource availability
- Error rate monitoring

### 2. Readiness Probes
- Service dependencies
- Cache availability
- External service status
- Resource thresholds

### 3. Startup Probes
- Initialization checks
- Configuration validation
- Resource allocation
- Service registration
