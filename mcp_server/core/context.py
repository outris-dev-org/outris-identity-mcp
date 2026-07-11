from contextvars import ContextVar
from typing import Optional
from .auth import MCPAccount

# Context variable to hold the current MCP account during request processing
current_account: ContextVar[Optional[MCPAccount]] = ContextVar("current_account", default=None)

# The credit-transaction id the transport used to deduct for THIS tool call.
# Threaded through execute_tool so an async job can later refund on the SAME
# request id (Phase 3 async jobs). None outside a metered tool call.
current_credit_request_id: ContextVar[Optional[str]] = ContextVar(
    "current_credit_request_id", default=None
)

# The portal SSO JWT of the acting user (Phase 2). In "shadow"/"sso" billing
# mode, call_backend forwards this to the BFF portal proxy so the BFF bills the
# user's own key natively. None when auth'd via a legacy mcp_ key or unset.
current_user_jwt: ContextVar[Optional[str]] = ContextVar("current_user_jwt", default=None)

# The effective billing mode for THIS request (ledger|shadow|sso), resolved by
# the transport (per-email canary aware) and read by call_backend + the credit
# ledger so proxy-routing and ledger-freeze can never diverge. None = fall back
# to the global setting.
current_billing_mode: ContextVar[Optional[str]] = ContextVar("current_billing_mode", default=None)
