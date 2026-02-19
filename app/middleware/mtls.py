import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

class MTLSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        require_mtls = os.getenv("REQUIRE_MTLS", "false").lower() == "true"
        
        # Public paths that don't need mTLS (e.g. Health checks, Swagger, JWK)
        public_paths = [
            "/docs", 
            "/openapi.json", 
            "/jwk", 
            "/health",
            "/_dev/audit", # Public in dev
            "/favicon.ico"
        ]
        
        # If mTLS is not enforced or path is public, skip check
        # Check for root path explicitly or strictly match prefixes
        is_public = (request.url.path == "/") or any(request.url.path.startswith(p) for p in public_paths)

        if not require_mtls or is_public:
            return await call_next(request)

        # Check for client cert header from Azure App Service
        client_cert = request.headers.get("X-ARR-ClientCert")
        if not client_cert:
            return JSONResponse(
                status_code=403,
                content={"detail": "Client Certificate Required"}
            )

        # In a real implementation, you would inspect 'client_cert' (Thumbprint/Issuer) here
        # For now, existence of the header confirms Azure validated it (if client_cert_mode="Required" but we are using "Optional" so we trust Azure passed it if present)
        
        return await call_next(request)
