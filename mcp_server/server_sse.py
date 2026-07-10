"""
FastAPI + SSE Transport for Outris MCP Server

FIXED VERSION - No double-wrapping of SSE responses
"""
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mcp.server.sse import SseServerTransport

from .core.config import get_settings
from .core.database import Database
from .core.auth import validate_api_key, AuthError
from .mcp_server import OutrisMCPServer
from .tools.registry import ToolRegistry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()

# Import tools to register them — curated Tier-1 intent surface (Phase 1).
from .tools import intent_tools  # noqa: F401  (registration side-effect)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Outris MCP Server (SSE)...")
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
    description="Model Context Protocol server for identity verification",
    version="1.0.0",
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


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "server": "outris-mcp-server",
        "version": "1.0.0",
        "mcp_version": "2024-11-05",
        "status": "ready",
        "endpoints": {
            "sse": "/sse",
            "health": "/health",
            "tools": "/tools"
        },
        "docs": "https://portal.outris.com/mcp"
    }


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "server": "outris-mcp-server",
        "version": "1.0.0",
        "tools_count": len(ToolRegistry.get_enabled())
    }


@app.get("/tools")
async def list_tools():
    """List available tools (public discovery)."""
    tools = []
    for name, tool_def in ToolRegistry.get_enabled().items():
        tools.append({
            "name": name,
            "description": tool_def.description.split("\n")[0],
            "credits": tool_def.credits,
            "category": tool_def.category
        })
    return {"tools": tools}


@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    MCP Server-Sent Events endpoint.

    IMPORTANT: The MCP SDK handles SSE internally. Do NOT wrap in EventSourceResponse!

    Authentication:
    - Header: Authorization: Bearer <mcp-key>
    - OR Query: ?api_key=<mcp-key>

    Guest mode: Connect without auth (limited to 2 demo tools)
    """
    # Check for API key
    auth_header = request.headers.get("Authorization", "")
    api_key = request.query_params.get("api_key")

    if not auth_header and api_key:
        auth_header = f"Bearer {api_key}"

    # Validate auth (allow guest mode)
    account = None
    if auth_header:
        try:
            account = await validate_api_key(auth_header)
            logger.info(f"Authenticated SSE connection: {account.user_email}")
        except AuthError as e:
            logger.warning(f"Auth failed (guest mode): {e.message}")
    else:
        logger.info("Guest SSE connection")

    # Create MCP server instance for this connection
    mcp_instance = OutrisMCPServer()
    await mcp_instance.set_account(account)

    # Create SSE transport
    sse_transport = SseServerTransport("/messages")

    # Let the SDK handle the SSE response directly
    # The SDK's connect_sse() already sends proper SSE headers and manages the stream
    try:
        async with sse_transport.connect_sse(
            request.scope,
            request.receive,
            request._send  # Pass send directly - SDK controls response
        ) as (read_stream, write_stream):
            await mcp_instance.get_server().run(
                read_stream,
                write_stream,
                mcp_instance.get_server().create_initialization_options()
            )
    except Exception as e:
        logger.error(f"SSE connection error: {e}", exc_info=True)
        raise


@app.post("/sse")
async def sse_post_endpoint(request: Request):
    """
    POST handler for /sse endpoint.

    Some MCP clients try POST first for transport discovery.
    Redirect them to use GET for SSE stream + POST to /messages for messages.
    """
    return JSONResponse(
        status_code=200,
        content={
            "error": "use_sse_transport",
            "message": "Use GET /sse for SSE stream, POST /messages?session_id=<id> for messages",
            "sse_endpoint": "/sse",
            "messages_endpoint": "/messages"
        }
    )


@app.post("/messages")
async def messages_endpoint(request: Request):
    """
    MCP message endpoint for bidirectional communication.

    This endpoint receives messages from the client during an SSE session.
    The MCP SDK's transport handles routing these to the server.
    """
    # The actual message handling is done by the SSE transport
    # This endpoint just needs to exist and return 200
    session_id = request.query_params.get("session_id")

    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"error": "session_id required"}
        )

    # Message is handled by the SSE transport's internal routing
    return {"status": "ok"}


@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    """
    OAuth discovery endpoint.

    Some MCP clients check for OAuth. Return empty to indicate
    we use Bearer token auth directly (no OAuth flow).
    """
    return {
        "issuer": "https://mcp-server.outris.com",
        "authorization_endpoint": None,
        "token_endpoint": None,
        "grant_types_supported": [],
        "response_types_supported": []
    }


@app.get("/.well-known/mcp")
async def mcp_discovery(request: Request):
    """
    MCP discovery endpoint.
    Helps clients auto-configure the connection.
    """
    return {
        "version": "2024-11-05",
        "name": "outris-mcp-server",
        "description": "Identity verification and investigation tools",
        "endpoints": {
            "sse": f"{request.base_url}sse"
        },
        "capabilities": {
            "tools": True,
            "resources": False,
            "prompts": False
        }
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "mcp_server.server_sse:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development"
    )
