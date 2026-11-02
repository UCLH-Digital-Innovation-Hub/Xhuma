"""Explicit FastAPI composition; no resource startup occurs here."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

from .observability import configure_application_observability, configure_import_observability

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> FastAPI:
    # Azure must precede imports of application modules that create telemetry instruments.
    # Keeping imports here also preserves this ordering for direct factory callers.
    configure_import_observability()

    from fastapi import FastAPI

    from app.api.errors import register_exception_handlers
    from app.version import VERSION

    from . import lifespan as lifecycle

    # Retain the shared binding that existing lifespans see after a main reload.
    lifecycle.REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))
    app = FastAPI(
        title="Xhuma",
        description="A stateless middleware service for GP Connect to CCDA conversion",
        version=VERSION,
        lifespan=lifecycle.lifespan,
    )
    configure_application_observability(app)
    register_exception_handlers(app)
    register_middleware(app)
    register_routes(app)
    return app


def register_middleware(app: FastAPI):
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    from app.middleware.mtls import MTLSMiddleware

    allowed_hosts_str = os.getenv("ALLOWED_HOSTS", "*")
    allowed_hosts = [h.strip() for h in allowed_hosts_str.split(",")]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    cors_origins_str = os.getenv("CORS_ORIGINS", "*")
    allow_origins = [o.strip() for o in cors_origins_str.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MTLSMiddleware)


def register_routes(app: FastAPI):
    from app.api import dev, system
    from app.relay import routes
    from app.relay.hub import WebSocketHub
    from app.settings import USE_RELAY
    from app.soap import soap

    app.include_router(soap.router)

    # USE_RELAY is still the cached settings binding, not a fresh environment read.
    if USE_RELAY:
        app.state.relay_hub = WebSocketHub()
        app.include_router(routes.router)
        print("Using HSCN Relay")
    else:
        print(f"{USE_RELAY} is not a valid setting for USE_RELAY, defaulting to no relay")

    app.include_router(system.router)
    if os.getenv("ENV", "prod").lower() in ("dev", "local"):
        app.include_router(dev.router)
