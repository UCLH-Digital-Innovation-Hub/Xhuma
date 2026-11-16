"""ASGI entrypoint and intentional compatibility exports."""

from app.bootstrap.application import create_app

app = create_app()

# Import after construction so Azure bootstrap still precedes application imports.
from app.api.system import get_jwk as get_jwk  # noqa: E402
from app.bootstrap.lifespan import lifespan as lifespan  # noqa: E402
