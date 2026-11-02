import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from ..security import get_jwk_keyset

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """
    Root endpoint that serves the welcome page with service information.

    Returns:
        HTMLResponse: A welcome page containing service description and endpoint examples.
    """
    return """
    <html>
        <head>
            <title>Welcome to Xhuma</title>
        </head>
        <body>
            <h3>Xhuma</h3>
            <p>This is the internet facing demo for Xhuma</p>
            <p>Interactive API documentation is available <a href="/docs#/">here</a>
            <h4>Endpoints</h4>
            <p>/pds/lookuppatient/nhsno will perform a pds lookup and return the fhir response.
               <a href="pds/lookup_patient/9449306680">Example</a></p>
        </body>
    </html>
    """


@router.get("/health")
async def health_check():
    """
    Public health check endpoint.
    """
    return {"status": "ok"}


@router.get("/jwk")
async def get_jwk(request: Request):
    """
    Public endpoint that provides the service's JSON Web Key Set.

    This endpoint is used by clients to verify JWT signatures and establish
    trust with the service.

    Returns:
        dict: JSON Web Key Set in dictionary format.
    """
    try:
        keys = await asyncio.to_thread(get_jwk_keyset)
        if keys:
            if hasattr(request, "app") and hasattr(request.app, "state"):
                request.app.state.jwk_keys = keys
                request.app.state.jwk_json = keys[0]
            return {"keys": keys}
    except Exception as e:
        logging.warning(f"Failed to load JWT signing key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWKS unavailable",
        )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="JWKS unavailable",
    )
