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
