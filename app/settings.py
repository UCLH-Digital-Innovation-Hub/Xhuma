import os

USE_RELAY = os.getenv("USE_RELAY", "false").lower() == "true"
RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", 75))
