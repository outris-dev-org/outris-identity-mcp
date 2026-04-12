"""
OAuh 2.0 Identity Provider Routes
=================================
Implements OAuth 2.0 endpoints for third-party integration (Claude.ai).

Endpoints:
- GET /.well-known/oauth-authorization-server: Discovery metadata
- POST /api/oauth/authorize: Generate authorization code (Internal use by Dashboard)
- POST /api/oauth/token: Exchange code for access token (Public use by Claude)

Flow:
1. Claude sends user to Dashboard /oauth/authorize -> Authentication & Consent
2. Dashboard calls Backend POST /api/oauth/authorize -> Returns code
3. Dashboard redirects user back to Claude with code
4. Claude calls Backend POST /api/oauth/token with code -> Returns JWT Access Token
"""
import json
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Request, Form
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from ..core.database import Database
from ..core.config import get_settings
from .user_routes import get_current_user, generate_mcp_key, get_mcp_account_by_email, create_jwt_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["OAuth 2.0"])

# ============================================================================
# Models
# ============================================================================

class OAuthAuthorizeRequest(BaseModel):
    """Request to generate authorization code (from Dashboard)."""
    client_id: str
    response_type: str
    redirect_uri: str
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None
    state: Optional[str] = None
    scope: Optional[str] = None

class OAuthAuthorizeResponse(BaseModel):
    """Response with authorization code."""
    code: str
    state: Optional[str]
    redirect_uri: str

class OAuthTokenResponse(BaseModel):
    """OAuth 2.0 Token Response."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: Optional[str] = None

# ============================================================================
# Discovery Endpoint
# ============================================================================

@router.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    """
    OAuth 2.0 Discovery Endpoint for Claude.ai.
    """
    settings = get_settings()
    dashboard_url = settings.dashboard_url
    # Token endpoint MUST be on the same origin as the MCP server
    mcp_base_url = settings.mcp_base_url

    return {
        "issuer": mcp_base_url,
        "authorization_endpoint": f"{dashboard_url}/oauth/authorize",
        "token_endpoint": f"{mcp_base_url}/api/oauth/token",
        "registration_endpoint": f"{mcp_base_url}/api/oauth/register",
        "token_endpoint_auth_methods_supported": ["none"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mcp"],
    }


@router.get("/.well-known/oauth-protected-resource/http")
@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata():
    """
    OAuth Protected Resource Metadata (RFC 9728).
    Serves both root and path-specific (/http) variants.
    """
    settings = get_settings()
    mcp_base_url = settings.mcp_base_url

    return {
        "resource": f"{mcp_base_url}/http",
        "authorization_servers": [mcp_base_url],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://portal.outris.com/mcp"
    }


# ============================================================================
# Dynamic Client Registration (RFC 7591 - called by Claude.ai)
# ============================================================================

@router.post("/api/oauth/register")
async def register_client(request: Request):
    """
    Dynamic Client Registration endpoint.

    Claude.ai calls this to register itself as an OAuth client
    before starting the authorization flow.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Generate a client_id for this registration
    client_id = f"claude-{secrets.token_urlsafe(16)}"

    redirect_uris = body.get("redirect_uris", [])
    client_name = body.get("client_name", "unknown")
    client_uri = body.get("client_uri", "")

    logger.info(f"OAuth client registered: {client_name} (id={client_id}, redirects={redirect_uris})")

    # Store client registration (optional: persist to DB for validation later)
    # For now, we accept all registrations dynamically
    try:
        await Database.execute(
            """
            INSERT INTO mcp.oauth_clients (
                client_id, client_name, client_uri, redirect_uris, created_at
            ) VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (client_id) DO NOTHING
            """,
            client_id,
            client_name,
            client_uri,
            json.dumps(redirect_uris)
        )
    except Exception as e:
        # If table doesn't exist or DB error, still return success
        # The registration is stateless for now
        logger.warning(f"Could not persist client registration: {e}")

    return JSONResponse(
        status_code=201,
        content={
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )


# ============================================================================
# Authorize Endpoint (Internal - called by Dashboard)
# ============================================================================

@router.post("/api/oauth/authorize", response_model=OAuthAuthorizeResponse)
async def authorize_user(
    request: OAuthAuthorizeRequest,
    req: Request
):
    """
    Generate authorization code.
    Authenticated endpoint - requires User JWT.
    """
    # 1. Verify User
    auth_header = req.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    # 2. Validate Client & Redirect URI (Strict Security Check)
    # Fetch client details to verify existence and authorized redirect URIs
    client_row = await Database.fetchrow(
        "SELECT redirect_uris FROM mcp.oauth_clients WHERE client_id = $1",
        request.client_id
    )
    
    if not client_row:
        logger.warning(f"OAuth authorization attempt with invalid client_id: {request.client_id}")
        raise HTTPException(status_code=400, detail="Invalid client_id")
        
    # Verify redirect_uri is in the whitelist
    try:
        allowed_redirects = json.loads(client_row["redirect_uris"])
    except Exception:
        allowed_redirects = []
        
    if request.redirect_uri not in allowed_redirects:
        logger.warning(f"OAuth Open Redirect attempt: client={request.client_id}, requested={request.redirect_uri}")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri for this client")

    # 3. Validate Request (Response Type)    if request.response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")
    
    # 3. Generate Code
    code = secrets.token_urlsafe(32)
    # Expires in 10 minutes
    expires_at = datetime.now() + timedelta(minutes=10)
    
    # 4. Store Code
    # Table: mcp.oauth_codes
    try:
        await Database.execute(
            """
            INSERT INTO mcp.oauth_codes (
                code, user_email, user_id, client_id, redirect_uri, 
                code_challenge, code_challenge_method, 
                expires_at, used, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, FALSE, NOW())
            """,
            code,
            user["email"],
            user.get("user_id"),
            request.client_id,
            request.redirect_uri,
            request.code_challenge,
            request.code_challenge_method,
            expires_at
        )
    except Exception as e:
        logger.error(f"Failed to store oauth code: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return OAuthAuthorizeResponse(
        code=code,
        state=request.state,
        redirect_uri=request.redirect_uri
    )

# ============================================================================
# Token Endpoint (Public - called by Claude)
# ============================================================================

@router.post("/api/oauth/token", response_model=OAuthTokenResponse)
async def exchange_token(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None)
):
    """
    Exchange authorization code for access token.
    """
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="Unsupported grant_type")
    
    # 1. Find Code
    row = await Database.fetchrow(
        """
        SELECT code, user_email, user_id, redirect_uri, code_challenge, code_challenge_method, expires_at, used
        FROM mcp.oauth_codes
        WHERE code = $1
        """,
        code
    )
    
    if not row:
        raise HTTPException(status_code=400, detail="Invalid code")
    
    # 2. Check Validity
    if row["used"]:
        raise HTTPException(status_code=400, detail="Code already used")
        
    if datetime.now() > row["expires_at"]:
        raise HTTPException(status_code=400, detail="Code expired")
        
    if row["redirect_uri"] != redirect_uri:
        # In relaxed mode, we might just log this warning, but strictly it should fail.
        # However, some clients might have mismatching trailing slashes.
        # For now, simplistic equality check.
        raise HTTPException(status_code=400, detail="Redirect URI mismatch")
        
    # 3. Verify PKCE (if challenge exists)
    if row["code_challenge"]:
        if not code_verifier:
             raise HTTPException(status_code=400, detail="Missing code_verifier")
        
        # S256
        if row["code_challenge_method"] == "S256":
            import base64
            # SHA256(code_verifier) -> Base64URL
            digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
            calculated = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
            
            if calculated != row["code_challenge"]:
                 raise HTTPException(status_code=400, detail="PKCE verification failed")
        else:
            # plain
             if code_verifier != row["code_challenge"]:
                 raise HTTPException(status_code=400, detail="PKCE verification failed")

    # 4. Mark Used
    await Database.execute("UPDATE mcp.oauth_codes SET used = TRUE WHERE code = $1", code)
    
    # 5. Issue Token
    display_name = row["user_email"].split("@")[0]
    
    payload = {
        "sub": row["user_email"],
        "email": row["user_email"],
        "role": "user",
        "displayName": display_name
    }
    
    access_token = create_jwt_token(payload, expires_delta=timedelta(days=30))
    
    return OAuthTokenResponse(
        access_token=access_token,
        expires_in=30 * 24 * 60 * 60, # 30 days
        scope="mcp"
    )

