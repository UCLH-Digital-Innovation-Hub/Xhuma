# Health Probes Configuration for Azure Container Apps

This document outlines the health probes configuration for Xhuma's Azure Container Apps deployment.

## Overview

Health probes are critical for ensuring application reliability and availability in containerized environments. They help the platform determine if a container is healthy and ready to serve traffic.

## Configuration Details

The Xhuma container app uses two types of probes:

1. **Liveness Probe**: Determines if the application is running properly
2. **Readiness Probe**: Determines if the application is ready to receive traffic

### Current Configuration

```terraform
liveness_probe {
  transport               = "HTTP"
  path                    = "/health"
  port                    = 8080
  initial_delay           = 5
  interval_seconds        = 10
  timeout                 = 2
  failure_count_threshold = 3
}

readiness_probe {
  transport               = "HTTP"
  path                    = "/ready"
  port                    = 8080
  initial_delay           = 5
  interval_seconds        = 10
  timeout                 = 2
  failure_count_threshold = 3
  success_count_threshold = 1
}
```

## Required Parameters

The following parameters are required for each probe:

- **transport**: Protocol used (HTTP, TCP, etc.)
- **port**: Port on which the probe endpoint is available 
- **path**: For HTTP probes, the URL path to check
- **interval_seconds**: Time between probe attempts
- **timeout**: How long the probe waits for a response
- **failure_count_threshold**: Number of consecutive failures before considering unhealthy
- **success_count_threshold**: Number of consecutive successes before considering healthy (readiness only)

## Notes on Azure Provider Versions

Recent Azure Terraform provider versions (3.75.0+) require these specific parameters. Changes include:

- The `transport` parameter is now required
- `failure_threshold` is replaced with `failure_count_threshold`
- `success_threshold` is replaced with `success_count_threshold`

## Endpoints Implementation

Ensure your application implements:

1. A `/health` endpoint that returns HTTP 200 when the application is running correctly
2. A `/ready` endpoint that returns HTTP 200 when the application can accept traffic

## Troubleshooting

If container apps are failing health checks:

1. Verify endpoints are implemented and responding on port 8080
2. Check logs for errors when accessing these endpoints
3. Ensure the Docker container exposes port 8080
4. Verify network connectivity between the Container App Environment and your application
5. Confirm that `transport` parameter matches your application configuration

## Port Configuration

The Xhuma application is configured to run on port 8080, which is:

- Exposed in the Dockerfile (`EXPOSE 8080`)
- Set as the default port for the application (`ENV PORT=8080`)
- Configured as the port for health probes
- Used as the target port in the ingress configuration
