"""
Credit management for MCP Server.
Handles credit deduction, logging, and balance queries.
"""
import json
import uuid
import logging
from typing import Optional
from datetime import datetime

from .database import Database
from .auth import MCPAccount

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Raised when account doesn't have enough credits."""
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(f"Insufficient credits: need {required}, have {available}")


async def deduct_credits(
    account: MCPAccount,
    tool_name: str,
    credits_cost: int,
    request_id: str,
    input_summary: dict = None,
) -> tuple[int, int]:
    """
    Deduct credits from account atomically.
    
    Returns:
        Tuple of (balance_before, balance_after)

    Raises:
        InsufficientCreditsError: If not enough credits
    """
    from .config import get_settings
    if get_settings().mcp_billing_mode != "ledger":
        # Phase 2: the BFF portal proxy is the meter. Record the tool call for
        # observability ONLY — no balance mutation, no InsufficientCredits.
        try:
            await Database.execute(
                """
                INSERT INTO mcp.user_tool_calls (
                    request_id, user_account_id, tool_name, credits_cost,
                    credits_charged, credits_before, credits_after,
                    input_params, success, created_at
                ) VALUES ($1, $2, $3, $4, 0, 0, 0, $5, FALSE, NOW())
                """,
                uuid.UUID(request_id), account.id, tool_name, credits_cost,
                json.dumps(input_summary) if input_summary else None,
            )
        except Exception as e:  # observability insert must never block the tool call
            logger.warning(f"user_tool_calls observability insert failed: {e}")
        return 0, 0

    async with Database.transaction() as conn:
        # Lock the row and get current balance
        row = await conn.fetchrow(
            """
            SELECT credits_balance, total_tool_calls, total_credits_used
            FROM mcp.user_accounts 
            WHERE id = $1 
            FOR UPDATE
            """,
            account.id
        )
        
        if row is None:
            raise ValueError(f"MCP account {account.id} not found")
        
        balance_before = row["credits_balance"]
        
        if balance_before < credits_cost:
            raise InsufficientCreditsError(credits_cost, balance_before)
        
        balance_after = balance_before - credits_cost
        
        # Update account
        await conn.execute(
            """
            UPDATE mcp.user_accounts
            SET 
                credits_balance = $1,
                total_tool_calls = total_tool_calls + 1,
                total_credits_used = total_credits_used + $2,
                updated_at = NOW()
            WHERE id = $3
            """,
            balance_after,
            credits_cost,
            account.id
        )
        
        # Log credit transaction
        await conn.execute(
            """
            INSERT INTO mcp.user_credit_transactions (
                user_account_id,
                transaction_type,
                amount,
                balance_before,
                balance_after,
                reference_type,
                reference_id,
                description,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            """,
            account.id,
            "usage",
            -credits_cost,  # Negative for deduction
            balance_before,
            balance_after,
            "tool_call",
            request_id,
            f"Tool: {tool_name}"
        )
        
        # Create tool call record (will be updated with result later)
        # credits_charged starts at credits_cost, will be set to 0 if backend error
        await conn.execute(
            """
            INSERT INTO mcp.user_tool_calls (
                request_id,
                user_account_id,
                tool_name,
                credits_cost,
                credits_charged,
                credits_before,
                credits_after,
                input_params,
                success,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, FALSE, NOW())
            """,
            uuid.UUID(request_id),
            account.id,
            tool_name,
            credits_cost,
            credits_cost,  # Charged initially, refunded on backend error
            balance_before,
            balance_after,
            json.dumps(input_summary) if input_summary else None
        )
    
    logger.info(
        f"Deducted {credits_cost} credits for {tool_name} "
        f"(account={account.id}, {balance_before} -> {balance_after})"
    )
    
    return balance_before, balance_after


async def record_tool_result(
    request_id: str,
    success: bool,
    output_summary: dict = None,
    error_code: str = None,
    error_message: str = None,
    latency_ms: float = None,
    backend_endpoint: str = None,
    is_backend_error: bool = False
) -> None:
    """
    Update tool call record with result.
    
    If is_backend_error=True, the user is not charged (credits refunded).
    Backend errors are our fault, not the user's.
    """
    # Determine credits_charged: 0 if backend error, otherwise keep original
    if is_backend_error:
        # Refund the credits by setting credits_charged to 0
        # We need to also refund to the account balance
        await _refund_credits_for_backend_error(request_id)
        credits_charged = 0
    else:
        # Keep original credits_charged (set during deduct_credits)
        credits_charged = None  # Don't update, keep original
    
    if credits_charged is not None:
        await Database.execute(
            """
            UPDATE mcp.user_tool_calls
            SET 
                success = $1,
                output_summary = $2,
                error_code = $3,
                error_message = $4,
                latency_ms = $5,
                backend_endpoint = $6,
                is_backend_error = $7,
                credits_charged = $8
            WHERE request_id = $9
            """,
            success,
            json.dumps(output_summary) if output_summary else None,
            error_code,
            error_message,
            latency_ms,
            backend_endpoint,
            is_backend_error,
            credits_charged,
            uuid.UUID(request_id)
        )
    else:
        await Database.execute(
            """
            UPDATE mcp.user_tool_calls
            SET 
                success = $1,
                output_summary = $2,
                error_code = $3,
                error_message = $4,
                latency_ms = $5,
                backend_endpoint = $6,
                is_backend_error = $7
            WHERE request_id = $8
            """,
            success,
            json.dumps(output_summary) if output_summary else None,
            error_code,
            error_message,
            latency_ms,
            backend_endpoint,
            is_backend_error,
            uuid.UUID(request_id)
        )


async def _refund_credits_for_backend_error(request_id: str) -> None:
    """
    Refund credits when a backend error occurs.
    User shouldn't pay for our failures.
    """
    from .config import get_settings
    if get_settings().mcp_billing_mode != "ledger":
        # BFF is the meter and only bills on HTTP 2xx — a backend 5xx is never
        # billed, so there is nothing to refund. The mcp.user_tool_calls row is
        # still marked is_backend_error by record_tool_result (observability).
        return

    # Get the tool call details
    row = await Database.fetchrow(
        """
        SELECT user_account_id, credits_cost, credits_charged
        FROM mcp.user_tool_calls
        WHERE request_id = $1
        """,
        uuid.UUID(request_id)
    )
    
    if not row or row["credits_charged"] == 0:
        # Already refunded or no credits to refund
        return
    
    account_id = row["user_account_id"]
    refund_amount = row["credits_cost"]
    
    if account_id and refund_amount > 0:
        # Refund credits to account
        async with Database.transaction() as conn:
            await conn.execute(
                """
                UPDATE mcp.user_accounts
                SET 
                    credits_balance = credits_balance + $1,
                    total_credits_used = total_credits_used - $1,
                    updated_at = NOW()
                WHERE id = $2
                """,
                refund_amount,
                account_id
            )
            
            # Log the refund transaction
            await conn.execute(
                """
                INSERT INTO mcp.user_credit_transactions (
                    user_account_id,
                    transaction_type,
                    amount,
                    balance_before,
                    balance_after,
                    reference_type,
                    reference_id,
                    description,
                    created_at
                ) VALUES ($1, $2, $3, 
                    (SELECT credits_balance - $3 FROM mcp.user_accounts WHERE id = $1),
                    (SELECT credits_balance FROM mcp.user_accounts WHERE id = $1),
                    $4, $5, $6, NOW())
                """,
                account_id,
                "refund",
                refund_amount,  # Positive = credit added back
                "tool_call",
                request_id,
                "Backend error - credits refunded"
            )
        
        logger.info(f"Refunded {refund_amount} credits for backend error (request={request_id})")


async def get_balance(account_id: int) -> int:
    """Get current credit balance for an account."""
    return await Database.fetchval(
        "SELECT credits_balance FROM mcp.user_accounts WHERE id = $1",
        account_id
    ) or 0


async def add_credits(
    account_id: int,
    amount: int,
    transaction_type: str = "purchase",
    reference_id: str = None,
    description: str = None,
    stripe_invoice_id: str = None
) -> tuple[int, int]:
    """
    Add credits to an account.
    
    Returns:
        Tuple of (balance_before, balance_after)
    """
    async with Database.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT credits_balance FROM mcp.user_accounts WHERE id = $1 FOR UPDATE",
            account_id
        )
        
        if row is None:
            raise ValueError(f"MCP account {account_id} not found")
        
        balance_before = row["credits_balance"]
        balance_after = balance_before + amount
        
        await conn.execute(
            "UPDATE mcp.user_accounts SET credits_balance = $1, updated_at = NOW() WHERE id = $2",
            balance_after,
            account_id
        )
        
        await conn.execute(
            """
            INSERT INTO mcp.user_credit_transactions (
                user_account_id,
                transaction_type,
                amount,
                balance_before,
                balance_after,
                reference_type,
                reference_id,
                description,
                stripe_invoice_id,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            """,
            account_id,
            transaction_type,
            amount,
            balance_before,
            balance_after,
            "stripe_invoice" if stripe_invoice_id else "manual",
            reference_id or str(uuid.uuid4()),
            description or f"Added {amount} credits",
            stripe_invoice_id
        )
    
    logger.info(f"Added {amount} credits to account {account_id}: {balance_before} -> {balance_after}")
    return balance_before, balance_after


async def get_usage_stats(account_id: int, days: int = 30) -> dict:
    """Get usage statistics for an account."""
    stats = await Database.fetchrow(
        """
        SELECT 
            COUNT(*) as total_calls,
            COUNT(*) FILTER (WHERE success = TRUE) as successful_calls,
            COUNT(*) FILTER (WHERE success = FALSE) as failed_calls,
            SUM(credits_cost) as total_credits_used,
            AVG(latency_ms) as avg_latency_ms
        FROM mcp.user_tool_calls
        WHERE user_account_id = $1
          AND created_at > NOW() - INTERVAL '%s days'
        """ % days,
        account_id
    )
    
    top_tools = await Database.fetch(
        """
        SELECT tool_name, COUNT(*) as call_count, SUM(credits_cost) as credits_used
        FROM mcp.user_tool_calls
        WHERE user_account_id = $1
          AND created_at > NOW() - INTERVAL '%s days'
        GROUP BY tool_name
        ORDER BY call_count DESC
        LIMIT 10
        """ % days,
        account_id
    )
    
    return {
        "period_days": days,
        "total_calls": stats["total_calls"] or 0,
        "successful_calls": stats["successful_calls"] or 0,
        "failed_calls": stats["failed_calls"] or 0,
        "total_credits_used": stats["total_credits_used"] or 0,
        "avg_latency_ms": round(stats["avg_latency_ms"] or 0, 2),
        "top_tools": [
            {"tool": r["tool_name"], "calls": r["call_count"], "credits": r["credits_used"]}
            for r in top_tools
        ]
    }
