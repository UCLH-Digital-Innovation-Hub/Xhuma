# Technical Resources

## NHS Digital Services Integration

### Authentication & Security
- **JWT Implementation**: Used for NHS API authentication
  - Algorithm: RS512
  - Key Storage: PEM format
  - Token Lifetime: 300 seconds
  - Headers Required:
    - `typ`: JWT
    - `kid`: Key ID

### SOAP Services
- **IHE ITI Profiles Implementation**:
  - ITI-55: Cross Gateway Patient Discovery
  - ITI-47: Patient Demographics Query
  - ITI-38: Cross Gateway Query
  - ITI-39: Cross Gateway Retrieve
- **SOAP Headers**:
  - Content-Type: application/soap+xml
- **Namespaces**:
  - SOAP Envelope: http://www.w3.org/2003/05/soap-envelope
  - Addressing: http://www.w3.org/2005/08/addressing
  - ebXML RegRep: urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0
  - IHE XDS.b: urn:ihe:iti:xds-b:2007

### GP Connect
- **Service Description**: GP Connect enables information sharing between different clinical systems
- **Documentation**: [GP Connect Documentation](https://digital.nhs.uk/developer/api-catalogue/gp-connect)
- **Environment URLs**:
  - Integration: https://orange.testlab.nhs.uk/
- **Headers Required**:
  - JWT Token
  - Content-Type
- **Authentication**: JWT-based

### Patient Demographics Service (PDS)
- **Service Description**: National demographics service
- **Documentation**: [PDS Documentation](https://digital.nhs.uk/developer/api-catalogue/personal-demographics-service-fhir)
- **Implementation**: FHIR-based REST API
- **Authentication**: JWT-based

## Standards and Specifications

### HL7 CCDA
- **Standard**: Consolidated Clinical Document Architecture
- **Documentation**: [HL7 CCDA Documentation](http://www.hl7.org/implement/standards/product_brief.cfm?product_id=492)
- **Implementation**: Custom conversion from FHIR to CCDA

### FHIR
- **Version**: STU3 (for GP Connect compatibility)
- **Documentation**: [FHIR STU3](http://hl7.org/fhir/STU3/)
- **Implementation**: Used for GP Connect and PDS interactions

### IHE ITI
- **Profiles Implemented**:
  - ITI-55: Cross Gateway Patient Discovery
  - ITI-47: Patient Demographics Query
  - ITI-38: Cross Gateway Query
  - ITI-39: Cross Gateway Retrieve
- **Documentation**: [IHE ITI Technical Framework](https://www.ihe.net/resources/technical_frameworks/#IT)

## Development Stack

### FastAPI
- **Usage**: Main web framework
- **Documentation**: [FastAPI Documentation](https://fastapi.tiangolo.com/)
- **Key Features Used**:
  - Custom routing for SOAP endpoints
  - Request/Response handling
  - Background tasks
  - Logging middleware

### Redis
- **Usage**: Caching and temporary storage
- **Documentation**: [Redis Documentation](https://redis.io/documentation)
- **Key Features Used**:
  - Key-value storage for documents
  - NHS number to CEID mapping
  - Session management

### Python Libraries
- **xmltodict**: XML parsing and generation
- **jwcrypto**: JWT key management
- **pydantic**: Data validation
- **fhirclient**: FHIR data models

## Monitoring & Observability

### Azure Application Insights
- **Purpose**: Metrics collection, application logging, and visualization
- **Key Metrics**:
  - Request counts, response times, error rates
  - Cache hit/miss rates, resource utilization
- **Features**:
  - Custom KQL dashboards
  - Alert management and data exploration
  - Automated exception mapping

### OpenTelemetry
- **Purpose**: Distributed tracing
- **Documentation**: [OpenTelemetry Docs](https://opentelemetry.io/docs/)
- **Features**:
  - Request tracing
  - Performance monitoring
  - Error tracking
  - Service dependencies

## Testing Tools

### Unit Testing
- **pytest**: Primary testing framework
  - [Documentation](https://docs.pytest.org/)
  - Test discovery
  - Fixture support
  - Parameterized testing

### Integration Testing
- **pytest-asyncio**: Async test support
- **aiohttp**: HTTP client testing

### Performance Testing
- **locust**: Load testing
  - [Documentation](https://docs.locust.io/)
  - Concurrent user simulation
  - Performance metrics
  - Real-time monitoring

### Code Quality
- **ruff**: Primary Python linter and formatter (replaces black/flake8)
- **CodeQL**: Security analysis and vulnerability scanning

## Deployment & Infrastructure

### Docker
- **Documentation**: [Docker Docs](https://docs.docker.com/)
- **Components**:
  - Multi-stage builds
  - Health checks
  - Volume management
  - Network configuration

### Terraform
- **Documentation**: [Terraform Docs](https://developer.hashicorp.com/terraform/docs)
- **Features**:
  - Shared-Nothing isolated state files per NHS Trust
  - Azure Resource Manager provider
  - VNet and Subnet orchestration

### Health Checks
- **Endpoints**:
  - /health/live: Liveness probe
  - /health/ready: Readiness probe
  - /metrics: Prometheus metrics
- **Implementation**: FastAPI endpoints

## Security Tools

### JWT Management
- **jwcrypto**: JWT implementation and key management
- **cryptography**: Cryptographic operations
- **Key rotation**: Automated key management

### API Security
- **Rate limiting**: FastAPI middleware
- **Input validation**: Pydantic models
- **CORS**: FastAPI CORS middleware
- **Security headers**: Custom middleware

## Additional Resources

### Documentation Tools
- **mkdocs**: Documentation generation
- **OpenAPI**: API documentation

### Development Tools
- **pre-commit**: Git hooks
- **git-flow**: Version control workflow
