# Lead Architect: PipeForge
# Shared Utility: API Key Authentication for the Command Center

import os, secrets
from fastapi import Request, HTTPException, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Read from env -- set UI_API_KEY in your .env file
# If not set, a random key is generated and printed at startup (dev mode)
_configured_key = os.getenv("UI_API_KEY", "")
if not _configured_key:
    _configured_key = secrets.token_urlsafe(32)
    print(f"\n[Auth] [WARN]  UI_API_KEY not set. Using generated key for this session:")
    print(f"[Auth] UI_API_KEY={_configured_key}\n")

UI_API_KEY = _configured_key

async def verify_api_key(request: Request) -> bool:
    """
    Checks the X-API-Key header OR a query param ?api_key=...
    Raises 403 if missing or wrong.
    """
    # Check header first
    key = request.headers.get("X-API-Key")
    # Fall back to query param (useful for browser access)
    if not key:
        key = request.query_params.get("api_key")
    # Fall back to cookie (set after first auth)
    if not key:
        key = request.cookies.get("pf_api_key")

    if not key or not secrets.compare_digest(key, UI_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key. Pass X-API-Key header or ?api_key= param."
        )
    return True