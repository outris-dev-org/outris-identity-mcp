import logging
import uuid
import time
from typing import Any, Dict

from fastapi import APIRouter, Request, HTTPException

from ..core.auth import get_account_by_id
from ..core.credits import InsufficientCreditsError, deduct_credits, record_tool_result
from ..tools.registry import ToolRegistry, execute_tool
from ..tools.helpers import classify_tool_error
from ..routes.chat_routes import get_current_user, _load_account

logger = logging.getLogger(__name__)

# Vertex Agent Builder requires standard REST endpoints.
# This router dynamically exposes all registered MCP tools as POST endpoints.
router = APIRouter(prefix="/api/vertex-agent", tags=["Vertex AI Agent Builder Extensions"])

@router.post("/{tool_name}")
async def execute_vertex_tool(tool_name: str, request: Request):
    """Execute an MCP tool via a standard REST POST request for Vertex Agent Builder."""
    # 1. Authenticate user
    auth_header = request.headers.get("Authorization", "")
    try:
        user = await get_current_user(auth_header)
        account = await _load_account(user["email"])
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")

    # 2. Validate tool
    tool_def = ToolRegistry.get(tool_name)
    if not tool_def:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found.")

    # 3. Parse JSON body (tool arguments)
    try:
        body = await request.json() if await request.body() else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    # 4. Billing and Execution
    request_id = str(uuid.uuid4())
    started = time.perf_counter()

    try:
        await deduct_credits(
            account=account,
            tool_name=tool_name,
            credits_cost=tool_def.credits,
            request_id=request_id,
            input_summary={"args": list(body.keys())},
        )
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=f"Insufficient credits: need {e.required}, have {e.available}.")

    try:
        result, exec_ms = await execute_tool(tool_name, body, account_id=account.id)
        
        await record_tool_result(
            request_id=request_id,
            success=True,
            latency_ms=exec_ms,
            backend_endpoint=tool_name,
        )
        return result

    except Exception as e:
        should_refund, error_code, client_message = classify_tool_error(e)
        
        await record_tool_result(
            request_id=request_id,
            success=False,
            error_code=error_code,
            error_message=str(e)[:500],
            is_backend_error=should_refund,
        )
        
        logger.error(f"Vertex Agent tool execution error ({error_code}): {e}")
        # Agent Builder needs clear HTTP error codes to know the tool failed
        status_code = 502 if should_refund else 400
        raise HTTPException(status_code=status_code, detail=client_message)

@router.get("/openapi.json")
async def get_vertex_openapi():
    """Return the generated OpenAPI spec for Agent Builder."""
    import json
    import os
    
    spec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "vertex_openapi.json")
    try:
        with open(spec_path, "r") as f:
            return json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="OpenAPI spec not generated yet.")
