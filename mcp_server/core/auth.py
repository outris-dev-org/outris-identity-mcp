"""
Authentication and authorization for MCP Server.
Validates MCP API keys and checks account status.

MCP uses separate keys from Direct API (mcp_xxx format).
Keys are stored in mcp.user_accounts, NOT public.api_keys.
"""
import hashlib
import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from .database import Database

logger = logging.getLogger(__name__)


@dataclass
class MCPAccount:
    """Represents an MCP user account."""
    id: int
    user_email: str
    display_name: Optional[str]
    credits_balance: int
    credits_tier: str
    is_active: bool
    stripe_customer_id: Optional[str] = None
    last_connected_at: Optional[datetime] = None
    allow_raw_records: bool = False
    persona: str = "general"  # Role-based tool access (e.g., fraud, collections, underwriting)


class AuthError(Exception):
    """Authentication/authorization error."""
    def __init__(self, message: str, code: str = "auth_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def hash_mcp_key(mcp_key: str) -> str:
    """Hash an MCP API key using SHA256."""
    return hashlib.sha256(mcp_key.encode()).hexdigest()


async def validate_api_key(api_key: str) -> MCPAccount:
    """
    Validate an MCP API key and return the associated account.
    
    MCP keys are stored in mcp.user_accounts (separate from Direct API keys).
    Expected format: mcp_xxxx... or Bearer mcp_xxxx...
    
    Raises:
        AuthError: If API key is invalid or account not active
    """
    if not api_key:
        raise AuthError("API key is required", "missing_key")
    
    # Remove "Bearer " prefix if present
    if api_key.startswith("Bearer "):
        api_key = api_key[7:]
    
    key_hash = hash_mcp_key(api_key)
    
    # Query mcp.user_accounts directly by key hash
    query = """
        SELECT 
            id,
            user_email,
            display_name,
            credits_balance,
            credits_tier,
            is_active,
            stripe_customer_id,
            last_connected_at
        FROM mcp.user_accounts
        WHERE mcp_key_hash = $1
    """
    
    row = await Database.fetchrow(query, key_hash)
    
    if row is None:
        raise AuthError("Invalid MCP API key", "invalid_key")
    
    if not row["is_active"]:
        raise AuthError("MCP account is deactivated", "account_inactive")
    
    # Fetch permissions from public.api_keys
    # Use the most permissive setting if multiple keys exist
    perm_query = """
        SELECT bool_or(allow_raw_records) as allow_raw
        FROM public.api_keys 
        WHERE client_email = $1 AND is_active = TRUE
    """
    perm_row = await Database.fetchrow(perm_query, row["user_email"])
    allow_raw = perm_row["allow_raw"] if perm_row and perm_row["allow_raw"] is not None else False
    
    # Update last_connected_at
    await Database.execute(
        "UPDATE mcp.user_accounts SET last_connected_at = NOW() WHERE id = $1",
        row["id"]
    )
    
    return MCPAccount(
        id=row["id"],
        user_email=row["user_email"],
        display_name=row["display_name"],
        credits_balance=row["credits_balance"],
        credits_tier=row["credits_tier"],
        is_active=row["is_active"],
        stripe_customer_id=row["stripe_customer_id"],
        last_connected_at=row["last_connected_at"],
        allow_raw_records=allow_raw
    )


async def check_credits(account: MCPAccount, required: int) -> bool:
    """Check if account has sufficient credits."""
    # Refresh balance from DB (in case of concurrent usage)
    current_balance = await Database.fetchval(
        "SELECT credits_balance FROM mcp.user_accounts WHERE id = $1",
        account.id
    )
    return current_balance >= required


async def get_account_by_id(mcp_account_id: int) -> Optional[MCPAccount]:
    """Get MCP account by ID."""
    query = """
        SELECT 
            id,
            user_email,
            display_name,
            credits_balance,
            credits_tier,
            is_active,
            stripe_customer_id,
            last_connected_at
        FROM mcp.user_accounts
        WHERE id = $1
    """
    
    row = await Database.fetchrow(query, mcp_account_id)
    if row is None:
        return None
        
    # Fetch permissions from public.api_keys
    perm_query = """
        SELECT bool_or(allow_raw_records) as allow_raw
        FROM public.api_keys 
        WHERE client_email = $1 AND is_active = TRUE
    """
    perm_row = await Database.fetchrow(perm_query, row["user_email"])
    allow_raw = perm_row["allow_raw"] if perm_row and perm_row["allow_raw"] is not None else False
    
    return MCPAccount(
        id=row["id"],
        user_email=row["user_email"],
        display_name=row["display_name"],
        credits_balance=row["credits_balance"],
        credits_tier=row["credits_tier"],
        is_active=row["is_active"],
        stripe_customer_id=row["stripe_customer_id"],
        last_connected_at=row["last_connected_at"],
        allow_raw_records=allow_raw
    )


class SessionManager:
    """Manages SSE connection sessions and authentication state."""
    
    _sessions: dict[str, Optional[MCPAccount]] = {}
    
    @classmethod
    def create_session(cls, session_id: str) -> None:
        """Create a new unauthenticated session."""
        cls._sessions[session_id] = None
        
    @classmethod
    def remove_session(cls, session_id: str) -> None:
        """Remove a session."""
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            
    @classmethod
    def set_account(cls, session_id: str, account: MCPAccount) -> None:
        """Associate an account with a session (login)."""
        cls._sessions[session_id] = account
        
    @classmethod
    def get_account(cls, session_id: str) -> Optional[MCPAccount]:
        """Get the account associated with a session."""
        return cls._sessions.get(session_id)
        
    @classmethod
    def is_authenticated(cls, session_id: str) -> bool:
        """Check if session is authenticated."""
        return cls._sessions.get(session_id) is not None
