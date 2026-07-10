"""
FastAPI Streamable HTTP + SSE Transport for Outris MCP Server

Implements both:
1. Streamable HTTP (stateless, new MCP spec standard)
2. SSE (backward compatibility)
3. Tool list endpoint (public discovery)
"""
import logging
import json
import uuid
import asyncio
from typing import Any, Callable, AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from mcp.server.sse import SseServerTransport
from mcp.server import Server
from mcp.types import Tool, TextContent

from .core.config import get_settings
from .core.database import Database
from .core.auth import validate_api_key, AuthError, MCPAccount
from .routes.user_routes import get_current_user, get_mcp_account_by_email
from .mcp_server import OutrisMCPServer
from .tools.registry import ToolRegistry, execute_tool, get_tool
from .tools.helpers import classify_tool_error
from .tools.capability_catalog import client_catalog
from .core.credits import (
    deduct_credits,
    record_tool_result,
    InsufficientCreditsError
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from datetime import date, datetime

class CustomJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

settings = get_settings()

# Import tools to register them — curated Tier-1 intent surface (Phase 1).
from .tools import intent_tools  # noqa: F401  (registration side-effect)
from .routes import public_router, user_router, admin_router, chat_router, demo_router, oauth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Outris MCP Server (Streamable HTTP + SSE)...")
    await Database.connect()
    logger.info(f"Registered tools: {list(ToolRegistry.get_all().keys())}")

    yield

    logger.info("Shutting down...")
    await Database.disconnect()
    from .tools.helpers import close_http_client
    await close_http_client()


# Create FastAPI app
app = FastAPI(
    title="Outris MCP Server",
    description="Model Context Protocol server for identity verification (Streamable HTTP + SSE)",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(public_router)
app.include_router(user_router)
app.include_router(admin_router)
app.include_router(chat_router)
app.include_router(demo_router)
app.include_router(oauth_router)


# ============================================================================
# Public Endpoints (No Auth)
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint - server info."""
    return {
        "server": "outris-mcp-server",
        "version": "2.0.0",
        "mcp_version": "2024-11-05",
        "status": "ready",
        "transports": {
            "streamable_http": "/http",
            "sse": "/sse",
            "stdio": "python -m mcp_server"
        },
        "endpoints": {
            "health": "/health",
            "tools": "/tools"
        },
        "docs": "https://portal.outris.com/mcp"
    }


@app.get("/health")
async def health():
    """Health check - no auth required."""
    return {
        "status": "healthy",
        "server": "outris-mcp-server",
        "version": "2.0.0",
        "transport": "streamable-http+sse",
        "tools_count": len(ToolRegistry.get_enabled())
    }


@app.get("/tools")
async def list_tools():
    """List available tools - no auth required (public discovery)."""
    tools = {}
    for name, tool_def in ToolRegistry.get_all().items():
        tools[name] = {
            "description": tool_def.description,
            "credits": tool_def.credits,
            "category": tool_def.category,
            "requires_auth": name not in ["platform_check", "check_whatsapp"]
        }
    return {
        "total": len(tools),
        "tools": tools,
        "public_tools": ["platform_check", "check_whatsapp"],
        "note": "Use /http or /sse for tool execution"
    }


# ============================================================================
# Streamable HTTP Transport (NEW - Primary Transport)
# ============================================================================

@app.get("/http")
async def streamable_http_discovery():
    """
    Discovery/Probe endpoint for Streamable HTTP.
    
    Some clients (like mcp-remote) may probe the endpoint with GET 
    to verify it exists and is accessible.
    """
    return {
        "status": "active",
        "transport": "streamable-http",
        "message": "Use POST requests for JSON-RPC tool execution."
    }


@app.post("/")
@app.post("/http")
async def streamable_http_transport(
    request: Request,
    authorization: str | None = Header(None)
):
    """
    Streamable HTTP transport endpoint (stateless).
    
    This is the new MCP spec standard transport for cloud deployments.
    Each request is independent (no persistent connection needed).
    
    Request format (JSON-RPC 2.0):
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list" | "tools/call",
        "params": {...}
    }
    
    Response format (JSON-RPC 2.0):
    {"jsonrpc":"2.0","id":1,"result":{...}}
    """
    
    # Parse request body
    try:
        body_bytes = await request.body()
    except Exception as e:
        logger.error(f"[HTTP] Failed to read request body: {e}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "Parse error",
                "data": f"Failed to read body: {e}"
            }
        })
    
    if not body_bytes:
        logger.warning("[HTTP] Empty request body received")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "Parse error",
                "data": "Empty request body"
            }
        })
    
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as e:
        raw_preview = body_bytes.decode('utf-8', errors='replace')[:500]
        logger.error(f"[HTTP] Invalid JSON: {e} | Raw Body: {raw_preview}")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "Parse error",
                "data": str(e)
            }
        })

    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    logger.info(f"[HTTP] Method: {method}, ID: {request_id}")

    try:
        # ====================================================================
        # Method: initialize (Handshake)
        # ====================================================================
        if method == "initialize":
            logger.info(f"[HTTP] Initialized")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {
                            "listChanged": True
                        },
                        "resources": {
                            "listChanged": False
                        },
                        "prompts": {
                            "listChanged": False
                        },
                        "logging": {}
                    },
                    "serverInfo": {
                        "name": "outris-mcp-server",
                        "version": "2.0.0"
                    }
                }
            })

        # ====================================================================
        # Method: notifications/initialized
        # ====================================================================
        elif method == "notifications/initialized":
            logger.info(f"[HTTP] Client initialized notification")
            # Notifications don't require a response, but we return empty result if ID present
            if request_id is not None:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {}
                })
            # For true notifications (no id), return minimal ack
            return JSONResponse({"jsonrpc": "2.0", "result": {}})

        # ====================================================================
        # Method: notifications/cancelled
        # ====================================================================
        elif method == "notifications/cancelled":
            logger.info(f"[HTTP] Request cancelled by client: {params.get('requestId')}")
            # Notifications don't require a response
            return JSONResponse({"jsonrpc": "2.0", "result": {}})

        # ====================================================================
        # Method: ping
        # ====================================================================
        elif method == "ping":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {}
            })

        # ====================================================================
        # Method: tools/list (Public - no auth)
        # ====================================================================
        elif method == "tools/list":
            tools = ToolRegistry.to_mcp_format()
            logger.info(f"[HTTP] tools/list: {len(tools)} tools returned")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": tools}
            })

        # ====================================================================
        # Method: resources/list + resources/read (public discovery)
        # The `outris://capabilities` resource lets the client model see the
        # full long-tail catalog on demand WITHOUT loading 100 tool schemas.
        # ====================================================================
        elif method == "resources/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"resources": [{
                    "uri": "outris://capabilities",
                    "name": "Outris capabilities catalog",
                    "description": "Every identity/KYC lookup available and the identifier each needs. "
                                   "Read this to discover what smart_lookup can answer.",
                    "mimeType": "application/json",
                }]}
            })

        elif method == "resources/read":
            uri = params.get("uri")
            if uri != "outris://capabilities":
                return JSONResponse({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": "Invalid params", "data": f"Unknown resource: {uri}"}
                })
            catalog_json = json.dumps(client_catalog(), cls=CustomJSONEncoder)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"contents": [{
                    "uri": "outris://capabilities",
                    "mimeType": "application/json",
                    "text": catalog_json,
                }]}
            })

        # ====================================================================
        # Method: tools/call (Requires auth)
        # ====================================================================
        elif method == "tools/call":
            # Extract tool name and arguments
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if not tool_name:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params",
                        "data": "Tool name required in params.name"
                    }
                })


            # Validate API key from header — return HTTP 401 to trigger OAuth flow
            if not authorization:
                logger.warning(f"[HTTP] Unauthorized tools/call attempt (no auth)")
                return JSONResponse(
                    status_code=401,
                    content={"error": "Authorization header required"},
                    headers={"WWW-Authenticate": "Bearer"}
                )

            if not authorization.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid Authorization header format"},
                    headers={"WWW-Authenticate": "Bearer"}
                )

            auth_token = authorization.replace("Bearer ", "")
            account = None

            # Try to validate as JWT (OAuth) first, then as API Key
            try:
                if auth_token.startswith("ey"):
                    # Validate as JWT
                    user_info = await get_current_user(authorization)  # Pass full header
                    account_data = await get_mcp_account_by_email(user_info["email"])
                    
                    if not account_data:
                        raise AuthError("No MCP account found for this user", "no_account")
                        
                    # Convert dict to MCPAccount object
                    # Ensure we handle all fields correctly, using defaults for optionals
                    account = MCPAccount(
                        id=account_data["id"],
                        user_email=account_data["user_email"],
                        display_name=account_data.get("display_name"),
                        credits_balance=account_data["credits_balance"],
                        credits_tier=account_data["credits_tier"],
                        is_active=account_data["is_active"],
                        stripe_customer_id=account_data.get("stripe_customer_id"),
                        last_connected_at=account_data.get("last_connected_at")
                    )
                else:
                    # Validate as MCP API Key
                    account = await validate_api_key(auth_token)

            except Exception as e:
                # Catch both AuthError and HTTPException from get_current_user
                error_msg = str(e)
                if hasattr(e, "detail"):
                    error_msg = str(e.detail)
                    
                logger.warning(f"[HTTP] Auth error: {error_msg}")
                return JSONResponse(
                    status_code=401,
                    content={"error": error_msg},
                    headers={"WWW-Authenticate": "Bearer"}
                )
            
            logger.info(f"[HTTP] Authenticated as: {account.user_email}")

            # Look up tool definition
            tool_def = get_tool(tool_name)
            if not tool_def:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params",
                        "data": f"Unknown tool: {tool_name}"
                    }
                })

            # Generate a unique request ID for credit tracking
            credit_request_id = str(uuid.uuid4())

            logger.info(f"[HTTP] Executing tool: {tool_name} (credit_req={credit_request_id})")

            try:
                # Deduct credits before execution
                balance_before, balance_after = await deduct_credits(
                    account=account,
                    tool_name=tool_name,
                    credits_cost=tool_def.credits,
                    request_id=credit_request_id,
                    input_summary={"args": list(arguments.keys())}
                )

                # Execute tool
                result, execution_time = await execute_tool(
                    name=tool_name,
                    arguments=arguments,
                    account_id=account.id
                )

                # Record success
                await record_tool_result(
                    request_id=credit_request_id,
                    success=True,
                    output_summary={"keys": list(result.keys())} if isinstance(result, dict) else None,
                    latency_ms=execution_time,
                    backend_endpoint=tool_name
                )

                # Format result with credit metadata
                result_text = json.dumps(result, cls=CustomJSONEncoder)
                metadata = f"\n\n[Credits: -{tool_def.credits} | Remaining: {balance_after} | Time: {execution_time:.0f}ms]"

                logger.info(f"[HTTP] {tool_name} executed successfully in {execution_time:.0f}ms")
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": result_text + metadata}
                        ],
                        "isError": False
                    }
                })

            except InsufficientCreditsError as e:
                logger.warning(f"[HTTP] Insufficient credits: {e}")
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": f"Insufficient credits: need {e.required}, have {e.available}. Visit portal.outris.com to top up."
                        }],
                        "isError": True
                    }
                })

            except Exception as e:
                # Typed classification — refund on our-fault (5xx/timeout) and on
                # preflight policy rejections; never surface str(e) (may carry
                # PII / supplier names).
                should_refund, error_code, client_message = classify_tool_error(e)

                await record_tool_result(
                    request_id=credit_request_id,
                    success=False,
                    error_code=error_code,
                    error_message=str(e)[:500],
                    is_backend_error=should_refund,
                )

                credits_status = "refunded" if should_refund else "charged"
                logger.error(f"[HTTP] Tool execution failed ({error_code}, {credits_status}): {e}")

                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": f"{client_message}\n\n[Credits: {credits_status}]"
                        }],
                        "isError": True
                    }
                })

        # ====================================================================
        # Unknown method
        # ====================================================================
        else:
            logger.warning(f"[HTTP] Unknown method: {method}")
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Method not found"
                }
            })

    except Exception as e:
        logger.error(f"[HTTP] Unexpected error: {e}", exc_info=True)
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": str(e)
            }
        })


# ============================================================================
# SSE Transport (OPTIONAL - Backward Compatibility)
# ============================================================================

@app.get("/sse")
async def sse_transport(request: Request, authorization: str | None = Header(None)):
    """
    SSE transport endpoint (legacy - backward compatibility).
    
    This maintains a persistent connection using Server-Sent Events.
    Kept for backward compatibility with existing clients.
    New clients should use /http (Streamable HTTP).
    """
    try:
        # Validate API key if provided
        account = None
        if authorization:
            if not authorization.startswith("Bearer "):
                raise AuthError("Invalid Authorization header format")
            api_key = authorization.replace("Bearer ", "")
            account = await validate_api_key(api_key)

        logger.info(f"SSE connection established")

        # Create MCP server instance
        mcp_server = OutrisMCPServer()
        mcp_server.current_account = account

        # Create SSE transport
        sse = SseServerTransport(request.url.path)

        async def handle_sse() -> AsyncIterator[bytes]:
            """Handle SSE connection (async generator)."""
            try:
                async with sse.lifespan() as streams:
                    logger.info("SSE transport lifespan started")
                    await mcp_server.server.run(
                        streams[0],
                        streams[1],
                        mcp_server.server.create_initialization_options()
                    )
            except Exception as e:
                logger.error(f"SSE error: {e}", exc_info=True)
                yield b"event: error\n"
                yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

        return StreamingResponse(
            handle_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except AuthError as e:
        logger.warning(f"SSE auth error: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"SSE setup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to establish SSE connection")


# ============================================================================
# Startup/Shutdown
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
