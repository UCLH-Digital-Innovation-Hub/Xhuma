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
- Request counts by endpoint and method (`http_requests_total`)
- Request duration histograms (`http_request_duration_seconds`)
- Status code distribution
- Per-endpoint metrics

#### Business Metrics
- ITI transaction rates (`iti_transaction_total`)
- CCDA conversion durations (`ccda_conversion_duration_seconds`)
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

## Demo Commands

### 1. Load Testing & Metrics

#### Generate Load
```bash
# Generate CCDA conversion load
for i in {1..10}; do curl -s http://localhost:80/demo/9690937278 > /dev/null; sleep 1; done

# Generate ITI-47 requests with logging
for i in {1..5}; do 
    curl -s -X POST http://localhost:80/iti/47 \
    -H "Content-Type: application/json" \
    -d '{"nhs_number": "9690937278"}' > /dev/null; 
    sleep 1; 
done
```

#### View Metrics
```bash
# Access Prometheus UI
open http://localhost:9090

# Useful PromQL Queries:
# Total requests by endpoint
rate(http_requests_total[5m])

# Average request duration
rate(http_request_duration_seconds_sum[5m]) / rate(http_request_duration_seconds_count[5m])

# Error rate
sum(rate(http_requests_total{status_code=~"5.*"}[5m])) by (endpoint)
```

### 2. Tracing & Logs

#### View Recent Logs
```bash
# View latest logs with correlation IDs
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, correlation_id, nhs_number, request_type, 
       message::jsonb->>'message' as message, level 
FROM application_logs 
ORDER BY timestamp DESC LIMIT 5;"

# View logs for specific NHS number
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, correlation_id, request_type, 
       message::jsonb->>'message' as message 
FROM application_logs 
WHERE nhs_number = '9690937278' 
ORDER BY timestamp DESC LIMIT 5;"

# View error logs
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, correlation_id, message::jsonb->>'message' as message 
FROM application_logs 
WHERE level = 'ERROR' 
ORDER BY timestamp DESC LIMIT 5;"
```

#### Trace Request Flow
```bash
# Make request and follow trace
curl -s -X POST http://localhost:80/iti/47 \
-H "Content-Type: application/json" \
-H "X-Correlation-ID: demo-trace-001" \
-d '{"nhs_number": "9690937278"}' > /dev/null && \
sleep 1 && \
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, request_type, message::jsonb->>'message' as message 
FROM application_logs 
WHERE correlation_id = 'demo-trace-001' 
ORDER BY timestamp ASC;"
```

### 3. Monitoring Dashboards

#### Access UIs
```bash
# Grafana Dashboard
open http://localhost:3000
# Default credentials: admin/admin

# Prometheus UI
open http://localhost:9090

# Tempo UI (for traces)
open http://localhost:3000/explore
```

### 4. Error Scenarios

#### Trigger and View Errors
```bash
# Restart service and check startup logs
docker-compose restart gpcon && \
sleep 5 && \
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, message::jsonb->>'message' as message, level 
FROM application_logs 
ORDER BY timestamp DESC LIMIT 1;"

# Make invalid request
curl -s -X POST http://localhost:80/iti/47 \
-H "Content-Type: application/json" \
-d '{"invalid": "data"}' > /dev/null && \
sleep 1 && \
docker-compose exec postgres psql -U postgres -d xhuma -c "
SELECT timestamp, level, message::jsonb->>'message' as message 
FROM application_logs 
WHERE level = 'ERROR' 
ORDER BY timestamp DESC LIMIT 1;"
```

### Key Metrics to Watch
- Request rates by endpoint
- Response times (p95, p99)
- Error rates
- CCDA conversion times
- Token creation/validation rates
- Cache hit/miss rates

## Grafana Dashboards

### Xhuma Overview Dashboard
Located at `/var/lib/grafana/dashboards/xhuma.json`, this dashboard provides:

1. Request Rate Panel
   - Shows request rates by endpoint and method
   - Uses `rate(http_requests_total{job="xhuma"}[5m])`
   - Helps identify traffic patterns and potential issues

2. Average Request Duration Panel
   - Displays average request duration by endpoint
   - Uses `rate(http_request_duration_seconds_sum{job="xhuma"}[5m]) / rate(http_request_duration_seconds_count{job="xhuma"}[5m])`
   - Helps monitor application performance

3. ITI Transaction Rate Panel
   - Shows rate of ITI transactions by type
   - Uses `rate(iti_transaction_total{job="xhuma"}[5m])`
   - Monitors healthcare interoperability operations

4. CCDA Conversion Duration Panel
   - Displays average CCDA conversion times
   - Uses `rate(ccda_conversion_duration_seconds_sum{job="xhuma"}[5m]) / rate(ccda_conversion_duration_seconds_count{job="xhuma"}[5m])`
   - Helps track document conversion performance

## Monitoring Setup

1. Start monitoring stack:
```bash
docker-compose up -d
```

2. Access interfaces:
- Grafana: http://localhost:3000 (default credentials: admin/admin)
- Prometheus: http://localhost:9090
- Application metrics: http://localhost:80/metrics

3. Metrics Configuration:
- Prometheus scrapes metrics every 15s (configured in prometheus.yml)
- Metrics are exposed via FastAPI middleware
- Custom metrics are defined in main.py

4. Dashboard Provisioning:
- Dashboards are automatically provisioned from /var/lib/grafana/dashboards
- Data sources are configured in grafana/provisioning/datasources/datasource.yml
- Dashboard configuration in grafana/provisioning/dashboards/dashboards.yml

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

3. Metric Naming Conventions:
- Use snake_case for metric names
- Include unit suffixes (e.g., _seconds, _bytes)
- Add relevant labels for filtering
- Follow Prometheus naming best practices

4. Dashboard Organization:
- Group related metrics together
- Use consistent units and scales
- Add helpful descriptions
- Configure appropriate refresh intervals
