# Xhuma Observability Documentation

## Overview

Xhuma implements comprehensive observability through:
- Structured logging with correlation IDs
- Prometheus metrics
- OpenTelemetry tracing
- Grafana dashboards

## Components

### 1. Logging Infrastructure

#### Configuration
- Centralised in `config.py`
- Environment-based configuration (development/production)
- JSON formatting in production, human-readable in development
- Automatic log rotation with size limits

#### Correlation IDs
- Automatically generated for each request
- Propagated through all service interactions
- Reused for repeated requests from same NHS number
- Stored in PostgreSQL for traceability

#### Security Logging
- Authentication events
- Token operations
- Access violations
- Sensitive data masking

#### CCDA Conversion Logging
- Conversion steps
- Section-by-section tracking
- Error handling
- Performance metrics

### 2. Metrics

#### HTTP Metrics
- Request counts by endpoint
- Response times
- Error rates
- Status code distribution

#### Business Metrics
- ITI transaction rates
- CCDA conversion durations
- Token operation success rates
- Cache hit/miss rates

#### System Metrics
- CPU usage
- Memory utilization
- Network I/O
- Database connections

### 3. Tracing

OpenTelemetry provides distributed tracing across:
- HTTP requests
- Database operations
- Token creation
- CCDA conversions
- Cache operations

## Querying Logs

### By Correlation ID

```sql
-- Get all events for a specific correlation ID
SELECT *
FROM application_logs
WHERE correlation_id = 'your-correlation-id'
ORDER BY timestamp ASC;

-- Get trace summary
SELECT 
    correlation_id,
    min(timestamp) as start_time,
    max(timestamp) as end_time,
    count(*) as event_count
FROM application_logs
WHERE correlation_id = 'your-correlation-id'
GROUP BY correlation_id;
```

### By NHS Number

```sql
-- Get all events for an NHS number
SELECT *
FROM application_logs
WHERE nhs_number = 'nhs-number'
ORDER BY timestamp DESC;

-- Get correlation IDs for an NHS number
SELECT DISTINCT correlation_id
FROM application_logs
WHERE nhs_number = 'nhs-number';

-- Get request summary by NHS number
SELECT 
    request_type,
    count(*) as request_count,
    avg(EXTRACT(EPOCH FROM (max(timestamp) - min(timestamp)))) as avg_duration_seconds
FROM application_logs
WHERE nhs_number = 'nhs-number'
GROUP BY request_type;
```

### Security Events

```sql
-- Get recent security events
SELECT *
FROM security_events
ORDER BY timestamp DESC
LIMIT 100;

-- Get failed authentication attempts
SELECT *
FROM application_logs
WHERE module = 'xhuma.security'
  AND level = 'ERROR'
  AND message LIKE '%Authentication failed%'
ORDER BY timestamp DESC;
```

### CCDA Conversion Events

```sql
-- Get conversion durations
SELECT 
    correlation_id,
    nhs_number,
    EXTRACT(EPOCH FROM (max(timestamp) - min(timestamp))) as duration_seconds
FROM application_logs
WHERE module = 'xhuma.ccda'
GROUP BY correlation_id, nhs_number
ORDER BY duration_seconds DESC;

-- Get conversion errors
SELECT *
FROM application_logs
WHERE module = 'xhuma.ccda'
  AND level = 'ERROR'
ORDER BY timestamp DESC;
```

## Grafana Dashboards

### Main Dashboard
- Request rates and latencies
- Error rates
- ITI transaction metrics
- CCDA conversion metrics

### Security Dashboard
- Authentication success/failure rates
- Token operations
- Access violations
- Security event timeline

### System Dashboard
- Resource utilization
- Cache performance
- Database connections
- Network metrics

## Best Practices

1. Always include correlation ID in logs:
```python
logger.info("Processing request", extra={
    "correlation_id": request.state.correlation_id,
    "nhs_number": nhs_number
})
```

2. Use appropriate log levels:
- DEBUG: Detailed information for debugging
- INFO: General operational events
- WARNING: Potential issues or repeated requests
- ERROR: Failed operations and security violations

3. Mask sensitive data:
- NHS numbers (except in specific fields)
- Personal identifiable information
- Authentication tokens
- System credentials

4. Add request context:
- Request type (ITI-47, ITI-38, etc.)
- Operation status
- Duration metrics
- Error details when applicable

## Monitoring Setup

1. Start monitoring stack:
```bash
docker-compose up -d
```

2. Access interfaces:
- Grafana: http://localhost:3000
- Prometheus: http://localhost:9090
- OpenTelemetry Collector: http://localhost:8888

3. Default credentials:
- Grafana:
  - Username: admin
  - Password: admin (change on first login)

## Troubleshooting

### Common Queries

1. Find recent errors:
```sql
SELECT timestamp, correlation_id, message, extra_data
FROM application_logs
WHERE level = 'ERROR'
ORDER BY timestamp DESC
LIMIT 10;
```

2. Track request flow:
```sql
SELECT timestamp, level, message
FROM application_logs
WHERE correlation_id = 'your-correlation-id'
ORDER BY timestamp ASC;
```

3. Monitor conversion performance:
```sql
SELECT 
    date_trunc('hour', timestamp) as time_bucket,
    count(*) as conversion_count,
    avg(EXTRACT(EPOCH FROM (max(timestamp) - min(timestamp)))) as avg_duration
FROM application_logs
WHERE module = 'xhuma.ccda'
GROUP BY time_bucket
ORDER BY time_bucket DESC;
```

### Health Checks

1. Check logging system:
```sql
SELECT count(*) as log_count
FROM application_logs
WHERE timestamp > now() - interval '1 hour'
GROUP BY level;
```

2. Verify correlation ID propagation:
```sql
SELECT correlation_id, count(distinct module) as service_count
FROM application_logs
GROUP BY correlation_id
HAVING count(distinct module) < 3;
```

3. Monitor security events:
```sql
SELECT event_type, count(*) as event_count
FROM security_events
WHERE timestamp > now() - interval '24 hours'
GROUP BY event_type;
