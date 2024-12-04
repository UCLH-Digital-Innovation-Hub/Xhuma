# Xhuma - GP Connect to CCDA Middleware Service

![Python version](https://img.shields.io/github/pipenv/locked/python-version/JMathiszig-Lee/GPconnect)

## Overview

Xhuma is a stateless middleware service that facilitates the conversion of GP Connect structured records into CCDA (Consolidated Clinical Document Architecture) format. The service implements IHE ITI profiles for interoperability and uses Redis for efficient caching of responses.

### Key Features

- Stateless architecture for scalability and reliability
- Production-ready Redis caching with:
  - Memory management and persistence
  - Connection pooling and automatic retry
  - Comprehensive monitoring
- IHE ITI profile implementation (ITI-47, ITI-38, ITI-39)
- FHIR to CCDA conversion
- JWT-based authentication for NHS Digital services
- SOAP message handling for healthcare interoperability

## Technical Architecture

The service is built on FastAPI and follows a modular design pattern. For detailed technical documentation, see:

- [Technical Architecture](docs/technical_architecture.md)
- [Data Flow Documentation](docs/data_flow.md)
- [Technical Resources](docs/technical_resources.md)

## System Flow

```mermaid
sequenceDiagram
    participant EHR
    participant Service
    participant NHS API's
    participant Redis Cache
    
    EHR->>Service: ITI 47 Request
    Service->>NHS API's: FHIR PDS lookup
    NHS API's->>Service: PDS response
    Service->>EHR: ITI 47 Response
    Service->>Redis Cache: Check for cached SDS lookup
    Service-->>NHS API's: SDS lookup
    NHS API's-->>Service: SDS Response
    Redis Cache->>Service: Return ADIS and FHIR root
    EHR->>Service: ITI 38 Request
    Service->>Redis Cache: Check for cached response
    Service-->>NHS API's: GPconnect Structured SCR request
    NHS API's-->>Service: Return Structured SCR
    Service->>Service: convert SCR to CCDA
    Service->>Redis Cache: Cache CCDA
    Service->>EHR: ITI 38 Response
    EHR->>Service: ITI 39 Request
    Service->>EHR: ITI 39 Response
```

## Prerequisites

- Docker
- Docker Compose
- NHS Digital API access credentials

## Deployment

1. Clone the repository:
```bash
git clone https://github.com/UCLH-Digital-Innovation-Hub/Xhuma.git
cd Xhuma
```

2. Set up environment variables:
```bash
cp .env.example .env
```

3. Configure the following in your .env file:
```
# Required configurations
REDIS_PASSWORD=your_secure_redis_password
API_KEY=your_api_key
JWTKEY=your_jwt_key
REGISTRY_ID=your_registry_id

# Optional configurations (defaults provided)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
```

4. Deploy with Docker Compose:
```bash
docker-compose up -d
```

The service will be available at `http://localhost:8000`

## Redis Configuration

The service uses a production-ready Redis setup with:
- Memory management (256MB limit with volatile-lru policy)
- Data persistence (RDB snapshots and AOF)
- Connection pooling
- Automatic retry mechanism
- Comprehensive monitoring
- Security hardening

Redis metrics are available through Prometheus and can be visualized in Grafana.

## API Documentation

Access the interactive API documentation at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Branch Strategy

- `main`: Production releases
- `dev`: Development branch
- `feature/*`: Feature branches
- `integration`: Integration testing

## Contributing

1. Create a feature branch from `dev`
2. Implement changes
3. Add tests
4. Create a pull request to `dev`

## Testing

Tests are automatically run in the CI pipeline. To run tests locally using Docker:

```bash
docker-compose -f docker-compose.test.yml up --build
```

## License

This project is licensed under the terms of the license included in the [LICENSE](LICENSE) file.
