import json
import logging
import os
import traceback
from contextlib import asynccontextmanager


@asynccontextmanager
async def capture_test_logs(test_id: str, nhsno: str):
    base_dir = f"scal_logs/{test_id}/{nhsno}"
    os.makedirs(base_dir, exist_ok=True)

    # Setup HTTPX logger
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.DEBUG)

    log_file = os.path.join(base_dir, "http.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    file_handler.setFormatter(formatter)
    httpx_logger.addHandler(file_handler)

    try:
        yield base_dir
    except Exception:
        with open(os.path.join(base_dir, "error.log"), "w") as f:
            f.write(traceback.format_exc())
        raise
    finally:
        # Clean up to avoid duplicate log handlers across tests
        httpx_logger.removeHandler(file_handler)
        file_handler.close()
