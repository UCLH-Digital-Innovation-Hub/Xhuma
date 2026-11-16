"""Startup validation and infrastructure ownership for the application."""

import asyncio
import datetime
import logging
import math
import os
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI

from app.db import make_engine, make_sessionmaker
from app.redis_connect import redis_client, snomed_client
from app.security import fix_pem_formatting, get_jwk_keyset

from .observability import lifespan_observability

# Application construction refreshes this shared binding, including for callers
# retaining an earlier app across a main-module reload.
REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context for FastAPI. Runs startup logic before app starts serving.
    """
    # --- Startup logic ---
    # Validate required configuration
    for var in ["API_KEY", "NHS_API_KEY", "ORG_ASID", "ORG_CODE"]:
        val = os.getenv(var)
        if not val or not val.strip():
            raise RuntimeError(f"Missing required configuration: {var}")
        if val.strip().startswith("@Microsoft.KeyVault("):
            raise RuntimeError(f"Unresolved KeyVault reference for required configuration: {var}")

    # Check configuration separation
    api_key = os.getenv("API_KEY")
    nhs_api_key = os.getenv("NHS_API_KEY")
    is_prod = os.getenv("ENV", "prod").lower() == "prod"

    if api_key and nhs_api_key and api_key == nhs_api_key:
        if is_prod:
            raise RuntimeError("CRITICAL: API_KEY and NHS_API_KEY must not be identical in production. Failing closed.")
        else:
            print("Warning: API_KEY and NHS_API_KEY are identical. This is allowed in dev/int but forbidden in prod.")

    ccda_expiry_str = os.getenv("CCDA_EXPIRY_HOURS", "4")
    try:
        expiry_hours = float(ccda_expiry_str)
        if not math.isfinite(expiry_hours) or expiry_hours <= 0:
            raise ValueError("Must be positive and finite")
        ttl = datetime.timedelta(hours=expiry_hours)
        if ttl.total_seconds() < 1:
            raise ValueError("Duration too small for Redis expiry")
        # Prevent integer overflow in Redis / Timedelta by capping at a reasonable upper bound (e.g. 1 year)
        if ttl.total_seconds() > 31536000:
            raise ValueError("Duration exceeds maximum allowed cache expiry (1 year)")

        app.state.ccda_expiry_hours = expiry_hours
    except (ValueError, OverflowError) as e:
        raise RuntimeError(f"Invalid CCDA_EXPIRY_HOURS configuration: {e}")

    async with AsyncExitStack() as resources:
        # Initialize Postgres connection pool
        engine = make_engine()
        resources.push_async_callback(engine.dispose)
        resources.push_async_callback(redis_client.close)
        resources.push_async_callback(snomed_client.close)
        SessionLocal = make_sessionmaker(engine)

        app.state.engine = engine
        app.state.SessionLocal = SessionLocal

        # Store registry ID in Redis with 24 hour expiry
        try:
            await redis_client.setex("registry", 86400, str(REGISTRY_ID).encode())
        except Exception as e:
            print(f"Warning: Failed to connect to Redis during startup: {e}")

        # Handle NHS Certificates securely
        cert_dir_obj = tempfile.TemporaryDirectory()
        resources.callback(cert_dir_obj.cleanup)
        app.state.nhs_cert_dir_obj = cert_dir_obj
        cert_dir = cert_dir_obj.name
        os.chmod(cert_dir, 0o700)
        os.environ["NHS_CERT_DIR_EPHEMERAL"] = cert_dir

        if os.getenv("NHS_CLIENT_CERT") and os.getenv("NHS_CLIENT_KEY") and os.getenv("NHS_BUNDLE"):
            try:
                cert_path = os.path.join(cert_dir, "client_cert.pem")
                key_path = os.path.join(cert_dir, "client_key.pem")
                bundle_path = os.path.join(cert_dir, "nhs_bundle.pem")

                with open(cert_path, "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_CLIENT_CERT")))
                with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_CLIENT_KEY")))
                with open(bundle_path, "w") as f:
                    f.write(fix_pem_formatting(os.getenv("NHS_BUNDLE")))
                print("Successfully hydrated NHS certificates to ephemeral filesystem storage.")
            except Exception:
                print(
                    "Warning: Failed to hydrate NHS certificates to ephemeral storage (malformed material). GP Connect will fail closed."
                )
        else:
            if os.getenv("ENV", "prod").lower() == "prod" and os.getenv("USE_RELAY") not in ("1", "true", "True"):
                print(
                    "Warning: Missing NHS_CLIENT_CERT, NHS_CLIENT_KEY or NHS_BUNDLE in production. Direct GP Connect will fail closed."
                )

        # Handle JWK generation/verification securely entirely in-memory
        app.state.jwk_json = {}
        app.state.jwk_keys = []

        try:
            keys = await asyncio.to_thread(get_jwk_keyset)
            if keys:
                app.state.jwk_keys = keys
                app.state.jwk_json = keys[0]
            else:
                logging.warning("No JWT signing key configured; /jwk will return an error")
        except Exception as e:
            logging.warning(f"Failed to load JWT signing key during startup: {e}")

        async with lifespan_observability():
            yield
