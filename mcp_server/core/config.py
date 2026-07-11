"""
MCP Server Configuration
"""
import os
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Database
    database_url: str
    
    # Backend API
    backend_url: str = Field(default="https://api.outris.com", description="Main Backend URL")
    backend_api_key: str = ""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    
    # Environment
    environment: str = "production"
    
    # URLs
    dashboard_url: str = "https://portal.outris.com"
    api_base_url: str = Field(default="https://rail.outris.com", validation_alias="BACKEND_API_URL")
    mcp_base_url: str = "https://mcp-server.outris.com"
    
    # JWT Authentication (shared with main backend)
    jwt_secret_key: str = ""
    
    # Stripe (optional)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    
    # Razorpay (optional - for India payments)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    
    # MCP Settings
    mcp_version: str = "1.0"
    server_name: str = "outris-mcp-server"
    
    # Feature Flags
    enable_kyc_tools: bool = False  # Disabled by default in public repo

    # Phase 3 consent migration: while True, a model-supplied consent="Y" is still
    # accepted for consent-required lookups (backwards compatible). Flip to False
    # once the portal consent-token issuer is live so ONLY a server-issued,
    # human-gated consent_token is honoured (the model can't fabricate one).
    allow_legacy_consent_y: bool = True

    # Phase 2 auth/billing consolidation onto portal SSO + public.api_keys.
    #   "ledger" (default) — today's behavior: mcp_ keys + mcp credits ledger;
    #            data calls use the single shared backend_api_key.
    #   "shadow" — accept portal JWT + mcp_ keys; route data calls through the
    #            per-user portal proxy (/api/portal/execute) so the BFF meters +
    #            bills natively; FREEZE the mcp credit ledger (observability only).
    #   "sso"    — JWT-only; mcp_ key path retired; ledger frozen.
    # The proxy-routing and the ledger-freeze are tied to this ONE flag so they
    # can never diverge (no double-billing). Default "ledger" ships inert.
    mcp_billing_mode: str = "ledger"

    # Per-email canary override: comma-separated emails that route through the
    # portal proxy (shadow) EVEN WHEN mcp_billing_mode is "ledger" — but only if
    # they authenticated with a portal JWT (a JWT is required to proxy). Lets us
    # validate SSO billing for one/few testers without a full cutover. Empty =
    # nobody overridden.
    mcp_shadow_emails: str = ""
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def _shadow_email_set() -> set:
    raw = get_settings().mcp_shadow_emails or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def effective_billing_mode_for(email: str, has_jwt: bool) -> str:
    """Resolve the billing mode for THIS request (per-email canary aware).

    - If the global mode is not "ledger" (shadow/sso), it applies to everyone.
    - Otherwise a JWT-authed user whose email is in MCP_SHADOW_EMAILS is routed
      through the portal proxy ("shadow"); everyone else stays "ledger".
    A JWT is required for the proxy, so mcp_-key users never get shadowed.
    """
    mode = get_settings().mcp_billing_mode
    if mode != "ledger":
        return mode
    if has_jwt and email and email.strip().lower() in _shadow_email_set():
        return "shadow"
    return "ledger"


def get_effective_billing_mode() -> str:
    """The mode for the CURRENT tool call: the per-request contextvar if set
    (populated by the transport via execute_tool), else the global setting.
    Read this in call_backend / the credit ledger so both agree."""
    from .context import current_billing_mode
    return current_billing_mode.get() or get_settings().mcp_billing_mode
