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

## Testing Resources

### Unit Testing
- **Framework**: pytest
- **Test Files Location**: app/tests/
- **Key Test Areas**:
  - SOAP message handling
  - Helper functions
  - Main application routes

### Integration Testing
- **NHS Digital Testing**:
  - GP Connect Provider Simulator
  - PDS Sandbox Environment
  - FHIR Validation

## Deployment

### Docker
- **Containerization**: Application and Redis
- **Configuration**:
  - Environment variables
  - Volume mounts for keys
  - Network configuration

### Environment Variables
- `REGISTRY_ID`: Unique identifier for the service instance
- `JWTKEY`: Private key for JWT signing
- Additional configuration as needed

## Security Implementation

### JWT Security
- **Key Management**:
  - Private key storage: PEM format
  - Public JWK endpoint
  - Key rotation support
- **Token Claims**:
  - Issuer (iss)
  - Subject (sub)
  - Audience (aud)
  - Expiration (exp)
  - Issued At (iat)

### Request Security
- **Content Validation**:
  - NHS Number validation
  - SOAP message structure validation
  - Content-Type verification
- **Logging**:
  - Request/Response logging
  - Client IP tracking
  - Timestamp recording

## Monitoring

### Application Logging
- **Implementation**: Python logging
- **Log Details**:
  - Client IP
  - Request/Response bodies
  - Timestamps
  - Method calls
  - Status codes

### Error Handling
- **HTTP Exceptions**:
  - 400: Invalid requests
  - 404: Resource not found
- **Custom error responses for SOAP faults**

## Additional Resources

### Code Quality
- **Pre-commit hooks**: .pre-commit-config.yaml
- **Style Guide**: PEP 8
- **Type Hints**: Python typing module

### Version Control
- **Repository**: GitHub
- **Branch Strategy**:
  - main: Production
  - dev: Development
  - feature branches: New features
