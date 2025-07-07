import logging

import httpx

logger = logging.getLogger("httpx_logger")
logger.setLevel(logging.INFO)
# Create file handler to write logs to a file
file_handler = logging.FileHandler("nhs_logs.log")
file_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(file_handler)


async def log_request(request: httpx.Request):
    logger.info("Outgoing Request:")
    logger.info(f"{request.method} {request.url}")
    logger.info(f"Headers: {dict(request.headers)}")
    if request.content:
        try:
            logger.info(f"Body: {request.content.decode('utf-8')}")
        except UnicodeDecodeError:
            logger.info("Body: [Binary Content]")
    logger.info("-----")


async def log_response(response: httpx.Response):
    content_bytes = await response.aread()
    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_str = "[Binary Content]"
    logger.info("Incoming Response:")
    logger.info(f"Status Code: {response.status_code}")
    logger.info(f"Headers: {dict(response.headers)}")
    logger.info(f"Body: {content_str}")
    logger.info("=====")
