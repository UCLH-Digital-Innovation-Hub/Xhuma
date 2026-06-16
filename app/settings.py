import os

USE_RELAY = os.getenv("USE_RELAY", "false").lower() in ("1", "true", "yes", "on")
RELAY_TIMEOUT = int(os.getenv("RELAY_TIMEOUT", 75))
EXTERNAL_RELAY_URL = os.getenv("EXTERNAL_RELAY_URL")
EXTERNAL_RELAY_CLIENT_ID = os.getenv("EXTERNAL_RELAY_CLIENT_ID", "client1")
