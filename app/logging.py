import logging
import sys

import httpx

logger = logging.getLogger("httpx_logger")
logger.setLevel(logging.INFO)

# Use StreamHandler instead of FileHandler to pipe logs directly to Azure Log Stream
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
stream_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(stream_handler)


def _scrub_headers(headers: httpx.Headers) -> dict:
    """Removes sensitive headers like tokens and certs before logging."""
    safe_headers = dict(headers)
    for sensitive_key in [
        "authorization",
        "x-arr-clientcert",
        "x-relay-clientcert",
        "cookie",
    ]:
        if sensitive_key in safe_headers:
            safe_headers[sensitive_key] = "***REDACTED***"
    return safe_headers


def record_application_failure(exception: Exception) -> None:
    """Records a handled exception to Application Insights to show up in the Failures blade."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            span.record_exception(exception)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
    except ImportError:
        pass


async def log_request(request: httpx.Request):
    correlation_id = request.headers.get("x-correlation-id", "N/A")
    traceparent = request.headers.get("traceparent", "N/A")

    logger.info(f"[req_trace:{correlation_id}] Outgoing Request:")
    logger.info(f"[req_trace:{correlation_id}] {request.method} {request.url}")
    logger.info(
        f"[req_trace:{correlation_id}] Headers: {_scrub_headers(request.headers)}"
    )
    logger.info(f"[req_trace:{correlation_id}] traceparent: {traceparent}")
    logger.info(f"[req_trace:{correlation_id}] Body: [REDACTED FOR PHI SECURITY]")
    logger.info("-----")


async def log_response(response: httpx.Response):
    correlation_id = response.request.headers.get("x-correlation-id", "N/A")

    logger.info(f"[res_trace:{correlation_id}] Incoming Response:")
    logger.info(f"[res_trace:{correlation_id}] Status Code: {response.status_code}")
    logger.info(
        f"[res_trace:{correlation_id}] Headers: {_scrub_headers(response.headers)}"
    )
    logger.info(f"[res_trace:{correlation_id}] Body: [REDACTED FOR PHI SECURITY]")
    logger.info("=====")
