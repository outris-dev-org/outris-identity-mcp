"""
MCP User Routes

Endpoints for user self-service MCP management:
- View MCP account
- Enable MCP
- Regenerate MCP key
- View usage history
"""
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field

from ..core.database import Database
from ..core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["MCP User"])

# ============================================================================
# JWT Authentication
# ============================================================================

async def get_current_user(authorization: str = None) -> dict:
    """
    Validate JWT token and return user info.
    Reuses JWT validation from main backend.
    """
    import jwt
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    token = authorization[7:]  # Remove "Bearer " prefix
    
    settings = get_settings()
    jwt_secret = settings.jwt_secret_key
    
    if not jwt_secret:
        logger.error("JWT_SECRET_KEY not configured")
        raise HTTPException(status_code=500, detail="Authentication not configured")
    
    try:
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token: missing email")
        
        return {
            "email": email.lower(),
            "role": payload.get("role", "user"),
            "display_name": payload.get("displayName") or payload.get("display_name"),
            "user_id": payload.get("user_id")
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
        logger.warning(f"Invalid JWT token: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


def create_jwt_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a new JWT token.
    Uses the same secret as the auth validation.
    """
    import jwt
    
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
        
    to_encode.update({"exp": expire})
    
    settings = get_settings()
    if not settings.jwt_secret_key:
        logger.error("JWT_SECRET_KEY not configured")
        raise RuntimeError("JWT secret not configured")
        
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm="HS256")
    return encoded_jwt


def get_auth_header(request: Request) -> str:
    """Extract Authorization header from request."""
    return request.headers.get("Authorization", "")


# ============================================================================
# Models
# ============================================================================

class MCPAccountInfo(BaseModel):
    """MCP account information (no full key)."""
    id: int
    user_email: str
    display_name: Optional[str]
    mcp_key_prefix: str
    credits_balance: int
    credits_tier: str
    credits_monthly_allocation: int
    is_active: bool
    last_connected_at: Optional[datetime]
    total_tool_calls: int
    total_credits_used: int
    stripe_subscription_status: Optional[str]
    created_at: datetime


class MCPEnableResponse(BaseModel):
    """Response when enabling MCP (includes full key once)."""
    success: bool
    message: str
    mcp_api_key: str  # Full key - shown only once!
    account: MCPAccountInfo


class MCPRegenerateResponse(BaseModel):
    """Response when regenerating MCP key."""
    success: bool
    message: str
    mcp_api_key: str  # New full key - shown only once!
    mcp_key_prefix: str


class ToolCallRecord(BaseModel):
    """Single tool call record."""
    request_id: str
    tool_name: str
    credits_cost: int
    credits_charged: int
    success: bool
    is_backend_error: bool
    error_code: Optional[str]
    latency_ms: Optional[float]
    created_at: datetime


class UsageHistoryResponse(BaseModel):
    """Tool call history response."""
    total_calls: int
    period_days: int
    tool_calls: list[ToolCallRecord]


class UsageSummaryResponse(BaseModel):
    """Usage summary statistics."""
    credits_balance: int
    credits_tier: str
    credits_monthly_allocation: int
    credits_used_today: int
    credits_used_this_month: int
    total_calls_today: int
    total_calls_this_month: int
    success_rate: float
    avg_latency_ms: float
    tool_breakdown: list[dict]
    days_until_refill: int


# ============================================================================
# Helper Functions
# ============================================================================

def generate_mcp_key() -> tuple[str, str, str]:
    """
    Generate a new MCP API key.
    
    Returns:
        Tuple of (full_key, key_hash, key_prefix)
    """
    # Generate 32-byte random key with mcp_ prefix
    random_part = secrets.token_urlsafe(32)[:32]
    full_key = f"mcp_{random_part}"
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    key_prefix = full_key[:12]  # "mcp_xxxxxxxx"
    
    return full_key, key_hash, key_prefix


async def get_mcp_account_by_email(email: str) -> Optional[dict]:
    """Get MCP account by user email."""
    row = await Database.fetchrow(
        """
        SELECT 
            id, user_email, display_name, mcp_key_prefix,
            credits_balance, credits_tier, credits_monthly_allocation,
            is_active, last_connected_at, total_tool_calls, total_credits_used,
            stripe_subscription_status, stripe_current_period_end,
            created_at, updated_at
        FROM mcp.user_accounts
        WHERE user_email = $1
        """,
        email.lower()
    )
    return dict(row) if row else None


async def get_mcp_account_by_id(account_id: int) -> Optional[dict]:
    """Get MCP account by ID."""
    row = await Database.fetchrow(
        """
        SELECT 
            id, user_email, display_name, mcp_key_prefix,
            credits_balance, credits_tier, credits_monthly_allocation,
            is_active, last_connected_at, total_tool_calls, total_credits_used,
            stripe_subscription_status, stripe_current_period_end,
            created_at, updated_at
        FROM mcp.user_accounts
        WHERE id = $1
        """,
        account_id
    )
    return dict(row) if row else None


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/account", response_model=MCPAccountInfo)
async def get_mcp_account(request: Request):
    """
    Get current user's MCP account info.
    
    Returns 404 if MCP is not enabled for this user.
    """
    auth_header = request.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    account = await get_mcp_account_by_email(user["email"])
    
    if not account:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "mcp_not_enabled",
                "message": "MCP is not enabled for your account. Click 'Enable MCP' to get started."
            }
        )
    
    return MCPAccountInfo(**account)


# ============================================================================
# Consent token issuer (Phase 3) — the human-gated side of the consent handshake
# ============================================================================

class ConsentAuthorizeRequest(BaseModel):
    capability_path: str = Field(..., description="The consent-required path being authorised, e.g. /api/collections/phone")
    subject: Optional[str] = Field(None, description="Raw identifier the consent is for; hashed server-side, never stored raw")
    consent_text: Optional[str] = Field(None, description="Free-text consent statement shown to the user")


@router.post("/consent/authorize")
async def authorize_consent(req: ConsentAuthorizeRequest, request: Request):
    """Issue a single-use, short-lived consent token AFTER the human has confirmed
    consent in the portal. The dashboard calls this with the user's JWT; the token
    is then relayed into chat as the tool `consent_token`. The connecting model can
    never forge it (issuance needs this JWT + a human click)."""
    user = await get_current_user(request.headers.get("Authorization", ""))
    account = await get_mcp_account_by_email(user["email"])
    if not account:
        raise HTTPException(status_code=404, detail="MCP is not enabled for your account.")

    from ..core.consent import issue_consent_token, subject_ref
    from ..tools.capability_catalog import requires_consent

    if not requires_consent(req.capability_path):
        raise HTTPException(status_code=400, detail="That action does not require consent.")

    token = await issue_consent_token(
        account_id=account["id"],
        user_email=account["user_email"],
        capability_path=req.capability_path,
        subject_ref=subject_ref(req.subject),
        consent_text=req.consent_text,
    )
    return {"consent_token": token, "expires_in_minutes": 15}


@router.post("/enable", response_model=MCPEnableResponse)
async def enable_mcp(request: Request):
    """
    Enable MCP for current user.
    
    Creates a new MCP account with free tier (50 credits/month).
    Returns the full MCP API key - this is shown only once!
    """
    auth_header = request.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    email = user["email"]
    
    # Check if already enabled
    existing = await get_mcp_account_by_email(email)
    if existing:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "already_enabled",
                "message": "MCP is already enabled for your account."
            }
        )
    
    # Generate MCP API key
    full_key, key_hash, key_prefix = generate_mcp_key()
    
    # Also generate a Direct API Key for public.api_keys (Shadow Key)
    # This ensures the user appears in the Dashboard and can have permissions toggled
    direct_key = f"sk_{secrets.token_urlsafe(24)}"
    direct_key_hash = hashlib.sha256(direct_key.encode()).hexdigest()
    direct_key_prefix = direct_key[:7]
    
    # Create account
    try:
        # 1. Create MCP Account
        row = await Database.fetchrow(
            """
            INSERT INTO mcp.user_accounts (
                user_email,
                user_id,
                display_name,
                mcp_key_hash,
                mcp_key_prefix,
                credits_balance,
                credits_tier,
                credits_monthly_allocation,
                credits_last_refill,
                is_active,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), TRUE, NOW())
            RETURNING id, user_email, display_name, mcp_key_prefix,
                      credits_balance, credits_tier, credits_monthly_allocation,
                      is_active, last_connected_at, total_tool_calls, total_credits_used,
                      stripe_subscription_status, created_at
            """,
            email,
            user.get("user_id"),
            user.get("display_name"),
            key_hash,
            key_prefix,
            50,  # Free tier credits
            "free",
            50
        )
        
        # 2. Check/Create Public API Key (Shadow Key)
        # Check if one already exists
        has_api_key = await Database.fetchval(
            "SELECT 1 FROM public.api_keys WHERE client_email = $1 LIMIT 1",
            email
        )
        
        if not has_api_key:
            await Database.execute(
                """
                INSERT INTO public.api_keys (
                    key_hash,
                    key_prefix,
                    client_name,
                    client_email,
                    created_at,
                    is_active,
                    allow_raw_records
                ) VALUES ($1, $2, $3, $4, NOW(), TRUE, FALSE)
                """,
                direct_key_hash,
                direct_key_prefix,
                user.get("display_name") or email.split("@")[0],
                email
            )
            
    except Exception as e:
        logger.error(f"Failed to create MCP account: {e}")
        raise HTTPException(status_code=500, detail="Failed to create MCP account")
    
    # Log the credit transaction
    await Database.execute(
        """
        INSERT INTO mcp.user_credit_transactions (
            user_account_id,
            transaction_type,
            amount,
            balance_before,
            balance_after,
            reference_type,
            description,
            created_by,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        """,
        row["id"],
        "monthly_refill",
        50,
        0,
        50,
        "system",
        "Initial free tier allocation",
        "system"
    )
    
    logger.info(f"MCP enabled for {email} (account_id={row['id']})")

    return MCPEnableResponse(
        success=True,
        message="MCP enabled successfully! Store your API key securely - it won't be shown again.",
        mcp_api_key=full_key,
        account=MCPAccountInfo(**dict(row))
    )


@router.post("/regenerate-key", response_model=MCPRegenerateResponse)
async def regenerate_mcp_key(request: Request):
    """
    Regenerate MCP API key.
    
    Invalidates the old key and creates a new one.
    Returns the new full key - shown only once!
    """
    auth_header = request.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    account = await get_mcp_account_by_email(user["email"])
    
    if not account:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "mcp_not_enabled",
                "message": "MCP is not enabled for your account."
            }
        )
    
    # Generate new key
    full_key, key_hash, key_prefix = generate_mcp_key()
    
    # Update account with new key
    await Database.execute(
        """
        UPDATE mcp.user_accounts
        SET mcp_key_hash = $1, mcp_key_prefix = $2, updated_at = NOW()
        WHERE id = $3
        """,
        key_hash,
        key_prefix,
        account["id"]
    )
    
    logger.info(f"MCP key regenerated for {user['email']} (account_id={account['id']})")
    
    return MCPRegenerateResponse(
        success=True,
        message="MCP API key regenerated successfully! Store it securely - it won't be shown again.",
        mcp_api_key=full_key,
        mcp_key_prefix=key_prefix
    )


@router.get("/usage", response_model=UsageHistoryResponse)
async def get_usage_history(
    request: Request,
    days: int = Query(default=7, ge=1, le=90, description="Number of days to look back"),
    tool_name: Optional[str] = Query(default=None, description="Filter by tool name"),
    success_only: bool = Query(default=False, description="Only show successful calls"),
    limit: int = Query(default=100, ge=1, le=500, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination")
):
    """
    Get tool call history for current user.
    """
    auth_header = request.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    account = await get_mcp_account_by_email(user["email"])
    
    if not account:
        raise HTTPException(status_code=404, detail={"code": "mcp_not_enabled"})
    
    # Build query with filters
    conditions = ["user_account_id = $1", "created_at > NOW() - INTERVAL '%s days'" % days]
    params = [account["id"]]
    
    if tool_name:
        conditions.append(f"tool_name = ${len(params) + 1}")
        params.append(tool_name)
    
    if success_only:
        conditions.append("success = TRUE")
    
    where_clause = " AND ".join(conditions)
    
    # Get total count
    total = await Database.fetchval(
        f"SELECT COUNT(*) FROM mcp.user_tool_calls WHERE {where_clause}",
        *params
    )
    
    # Get paginated results
    rows = await Database.fetch(
        f"""
        SELECT 
            request_id, tool_name, credits_cost, credits_charged,
            success, is_backend_error, error_code, latency_ms, created_at
        FROM mcp.user_tool_calls
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT {limit} OFFSET {offset}
        """,
        *params
    )
    
    return UsageHistoryResponse(
        total_calls=total or 0,
        period_days=days,
        tool_calls=[
            ToolCallRecord(
                request_id=str(r["request_id"]),
                tool_name=r["tool_name"],
                credits_cost=r["credits_cost"],
                credits_charged=r["credits_charged"],
                success=r["success"],
                is_backend_error=r["is_backend_error"] or False,
                error_code=r["error_code"],
                latency_ms=r["latency_ms"],
                created_at=r["created_at"]
            )
            for r in rows
        ]
    )


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(request: Request):
    """
    Get usage summary statistics for current user.
    """
    auth_header = request.headers.get("Authorization", "")
    user = await get_current_user(auth_header)
    
    account = await get_mcp_account_by_email(user["email"])
    
    if not account:
        raise HTTPException(status_code=404, detail={"code": "mcp_not_enabled"})
    
    account_id = account["id"]
    
    # Get today's usage
    today_stats = await Database.fetchrow(
        """
        SELECT 
            COUNT(*) as calls,
            COALESCE(SUM(credits_charged), 0) as credits_used
        FROM mcp.user_tool_calls
        WHERE user_account_id = $1
          AND DATE(created_at) = CURRENT_DATE
        """,
        account_id
    )
    
    # Get this month's usage
    month_stats = await Database.fetchrow(
        """
        SELECT 
            COUNT(*) as calls,
            COALESCE(SUM(credits_charged), 0) as credits_used,
            COUNT(*) FILTER (WHERE success = TRUE) as successful,
            AVG(latency_ms) as avg_latency
        FROM mcp.user_tool_calls
        WHERE user_account_id = $1
          AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
        """,
        account_id
    )
    
    # Get tool breakdown
    tool_breakdown = await Database.fetch(
        """
        SELECT 
            tool_name,
            COUNT(*) as calls,
            COALESCE(SUM(credits_charged), 0) as credits_used
        FROM mcp.user_tool_calls
        WHERE user_account_id = $1
          AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY tool_name
        ORDER BY calls DESC
        """,
        account_id
    )
    
    # Calculate days until refill (first of next month)
    from datetime import date
    today = date.today()
    if today.month == 12:
        next_refill = date(today.year + 1, 1, 1)
    else:
        next_refill = date(today.year, today.month + 1, 1)
    days_until_refill = (next_refill - today).days
    
    # Calculate success rate
    total_month = month_stats["calls"] or 0
    successful_month = month_stats["successful"] or 0
    success_rate = (successful_month / total_month * 100) if total_month > 0 else 100.0
    
    return UsageSummaryResponse(
        credits_balance=account["credits_balance"],
        credits_tier=account["credits_tier"],
        credits_monthly_allocation=account["credits_monthly_allocation"],
        credits_used_today=today_stats["credits_used"] or 0,
        credits_used_this_month=month_stats["credits_used"] or 0,
        total_calls_today=today_stats["calls"] or 0,
        total_calls_this_month=total_month,
        success_rate=round(success_rate, 1),
        avg_latency_ms=round(month_stats["avg_latency"] or 0, 1),
        tool_breakdown=[
            {"tool": r["tool_name"], "calls": r["calls"], "credits": r["credits_used"]}
            for r in tool_breakdown
        ],
        days_until_refill=days_until_refill
    )
