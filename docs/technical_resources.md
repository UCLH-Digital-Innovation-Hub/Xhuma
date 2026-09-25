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
  - ITI-38: Cross Gateway Query
  - ITI-39: Cross Gateway Retrieve
- **Documentation**: [IHE ITI Technical Framework](https://www.ihe.net/resources/technical_frameworks/#IT)

## System Architecture & Technologies

### Production / Target Runtime
Technologies actively used in deployed Azure target environments:

- **FastAPI / Python**: Main web framework and application language.
- **Azure App Service**: Container hosting environment.
- **Azure Managed PostgreSQL**: Persistent storage for audit logs and system configuration.
- **Azure Managed Redis**: Transient caching for NHS number mappings, documents, and PDS/SDS results.
- **Azure Key Vault**: Secure storage for secrets, certificates (Epic mTLS CA), and tokens.
- **Application Insights & Log Analytics**: Azure Monitor components for production telemetry, distributed tracing, and request logging.
- **OpenTelemetry**: Instrumentation for distributed tracing and performance metrics.
- **GitHub Actions**: Matrix CI/CD pipeline orchestration.
- **Terraform**: Infrastructure as Code (IaC) for deterministic target provisioning.
- **GHCR (GitHub Container Registry)**: Immutable container image storage and digest verification.

### Local Development / Test Tooling
Technologies used strictly for local engineering, testing, and continuous integration:

- **Docker Compose**: Local service orchestration and environment parity.
- **Prometheus & Grafana**: Local metric collection and visualisation stack (not deployed to production targets).
- **pytest & pytest-asyncio**: Primary testing framework for unit and async integration tests.
- **schemathesis**: API Fuzzing framework generating edge-case requests from the OpenAPI schema.
- **hypothesis**: Property-Based Testing for internal FHIR/C-CDA mapping resilience.
- **testcontainers**: Container-based testing for Redis integration.
- **locust**: Load and performance testing.
- **black, flake8, mypy, bandit**: Code formatting, style enforcement, type checking, and security linting.

### Health Checks
- **Endpoints**:
  - `/health`: Primary coarse liveness probe checked by the deployment pipeline.
- **Implementation**: FastAPI endpoint.

## Security Tools

### JWT Management
- **python-jose**: JWT implementation
- **cryptography**: Cryptographic operations

### API Security
- **Rate limiting**: FastAPI middleware
- **Input validation**: Pydantic models
- **CORS**: FastAPI CORS middleware
- **Security headers**: Custom middleware

## Additional Resources

### Documentation Tools
- **mkdocs**: Documentation generation
- **OpenAPI**: API documentation
- **sphinx**: Python documentation

### Development Tools
- **pre-commit**: Git hooks
- **dependabot**: Dependency updates
- **renovate**: Package management
- **git-flow**: Version control workflow
