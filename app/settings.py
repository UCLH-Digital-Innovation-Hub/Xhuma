import os

USE_RELAY = os.getenv("USE_RELAY", "false").lower() in ("1", "true", "yes", "on")
RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", 75))
