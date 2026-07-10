"""
Outris MCP Server - Compliant Implementation

This is the CORRECT MCP server implementation using the official MCP SDK.
It provides identity verification tools via the Model Context Protocol.

Architecture:
- Uses official `mcp` Python SDK for protocol handling
- FastAPI for HTTP/SSE endpoints
- Custom auth, credits, and tool execution logic
"""
import logging
import json
import uuid
from typing import Any, Sequence
from contextlib import asynccontextmanager

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel,
    Resource,
)
from mcp.server.lowlevel.helper_types import ReadResourceContents

from .core.config import get_settings
from .core.database import Database
from .core.auth import validate_api_key, AuthError, MCPAccount
from .core.credits import (
    deduct_credits,
    record_tool_result,
    InsufficientCreditsError
)
from .tools.registry import ToolRegistry, get_tool, execute_tool
from .tools.helpers import classify_tool_error

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Get settings
settings = get_settings()

# Import tools to register them.
# Phase 1 (2026-07): the curated Tier-1 intent surface replaces the old
# per-endpoint wrappers (investigation/platforms/commerce/breach/kyc). Those
# modules remain on disk but are no longer imported, so only the ~10 intent
# tools register — keeping the tool list small and safe for the client model.
from .tools import intent_tools  # noqa: F401  (registration side-effect)
logger.info("Registered Tier-1 intent tools")


class OutrisMCPServer:
    """Outris MCP Server with authentication and credits."""

    def __init__(self):
        self.server = Server("outris-mcp-server")
        self.current_account: MCPAccount | None = None
        self._setup_handlers()

    def _setup_handlers(self):
        """Setup MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return available tools based on authentication."""
            tools = []

            # If not authenticated, return limited demo tools
            if self.current_account is None:
                logger.info("Guest request - returning demo tools only")
                demo_tools = ["platform_check", "check_whatsapp"]

                for name, tool_def in ToolRegistry.get_enabled().items():
                    if name in demo_tools:
                        tools.append(Tool(
                            name=name,
                            description=f"[DEMO] {tool_def.description}",
                            inputSchema={
                                "type": "object",
                                "properties": tool_def.parameters,
                                "required": [
                                    k for k, v in tool_def.parameters.items()
                                    if v.get("required", False)
                                ]
                            }
                        ))

                # Add info tool
                tools.append(Tool(
                    name="get_full_access",
                    description="Learn how to unlock all investigation tools",
                    inputSchema={"type": "object", "properties": {}}
                ))

                return tools

            # Authenticated - return all enabled tools
            for name, tool_def in ToolRegistry.get_enabled().items():
                tools.append(Tool(
                    name=name,
                    description=tool_def.description,
                    inputSchema={
                        "type": "object",
                        "properties": tool_def.parameters,
                        "required": [
                            k for k, v in tool_def.parameters.items()
                            if v.get("required", False)
                        ]
                    }
                ))

            logger.info(f"Returning {len(tools)} tools (auth={self.current_account is not None})")
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
            """Execute a tool call."""
            logger.info(f"Tool call: {name} (auth={self.current_account is not None})")

            # Special: get_full_access
            if name == "get_full_access":
                return [TextContent(
                    type="text",
                    text="To unlock all tools:\n"
                         "1. Visit https://portal.outris.com/mcp\n"
                         "2. Generate your free API key\n"
                         "3. Add to Claude config: Authorization: Bearer <your-key>"
                )]

            # Get tool definition
            tool_def = get_tool(name)
            if tool_def is None:
                return [TextContent(
                    type="text",
                    text=f"Error: Unknown tool '{name}'"
                )]

            # Guest restrictions
            if self.current_account is None:
                allowed_demo = ["platform_check", "check_whatsapp"]
                if name not in allowed_demo:
                    return [TextContent(
                        type="text",
                        text=f"Authentication required. Use 'get_full_access' for instructions."
                    )]

                # Execute demo tool without credits
                try:
                    result, exec_time = await execute_tool(name, arguments)
                    return [TextContent(
                        type="text",
                        text=json.dumps(result, indent=2, default=str)
                    )]
                except Exception as e:
                    logger.error(f"Demo tool {name} failed: {e}")
                    return [TextContent(
                        type="text",
                        text=f"Error: {str(e)}"
                    )]

            # Authenticated user - check and deduct credits
            request_id = str(uuid.uuid4())

            try:
                balance_before, balance_after = await deduct_credits(
                    account=self.current_account,
                    tool_name=name,
                    credits_cost=tool_def.credits,
                    request_id=request_id,
                    input_summary={"args": list(arguments.keys())}
                )
            except InsufficientCreditsError as e:
                return [TextContent(
                    type="text",
                    text=f"Insufficient credits: need {e.required}, have {e.available}. "
                         f"Visit https://portal.outris.com/mcp to add credits."
                )]

            # Execute tool
            try:
                result, exec_time = await execute_tool(
                    name, arguments,
                    account_id=self.current_account.id if self.current_account else None,
                    credit_request_id=request_id,
                )

                # Record success
                await record_tool_result(
                    request_id=request_id,
                    success=True,
                    output_summary={"keys": list(result.keys())} if isinstance(result, dict) else None,
                    latency_ms=exec_time,
                    backend_endpoint=name
                )

                # Format response with metadata
                result_text = json.dumps(result, indent=2, default=str)
                metadata = f"\n\n[Credits: -{tool_def.credits} | Remaining: {balance_after} | Time: {exec_time:.0f}ms]"

                return [TextContent(
                    type="text",
                    text=result_text + metadata
                )]

            except Exception as e:
                logger.error(f"Tool {name} failed: {e}")

                # Typed classification — refund on our-fault (5xx/timeout) and on
                # preflight policy rejections; never leak str(e) to the client.
                should_refund, error_code, client_message = classify_tool_error(e)

                await record_tool_result(
                    request_id=request_id,
                    success=False,
                    error_code=error_code,
                    error_message=str(e)[:500],
                    is_backend_error=should_refund,
                )

                credits_status = "refunded" if should_refund else "charged"
                return [TextContent(
                    type="text",
                    text=f"{client_message}\n\n[Credits: {credits_status}]"
                )]

        # ----------------------------------------------------------------
        # Resources — the outris://capabilities catalog (parity with the
        # streamable-HTTP transport so stdio/SSE clients get discovery too).
        # ----------------------------------------------------------------
        @self.server.list_resources()
        async def list_resources() -> list[Resource]:
            return [Resource(
                uri="outris://capabilities",
                name="Outris capabilities catalog",
                description="Every identity/KYC lookup available and the identifier each needs. "
                            "Read this to discover what smart_lookup can answer.",
                mimeType="application/json",
            )]

        @self.server.read_resource()
        async def read_resource(uri) -> list[ReadResourceContents]:
            if str(uri) != "outris://capabilities":
                raise ValueError(f"Unknown resource: {uri}")
            from .tools.capability_catalog import client_catalog
            return [ReadResourceContents(
                content=json.dumps(client_catalog()),
                mime_type="application/json",
            )]

    async def set_account(self, account: MCPAccount | None):
        """Set the authenticated account for this session."""
        self.current_account = account
        if account:
            logger.info(f"Session authenticated: {account.user_email}")
        else:
            logger.info("Session is guest mode")

    def get_server(self) -> Server:
        """Get the MCP server instance."""
        return self.server


# Create global server instance
mcp_server_instance = OutrisMCPServer()


async def run_mcp_server():
    """Run MCP server via stdio transport."""
    # Initialize database
    await Database.connect()
    logger.info("Database connected")

    # Log registered tools
    logger.info(f"Registered tools: {list(ToolRegistry.get_all().keys())}")

    # Run server
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server_instance.get_server().run(
            read_stream,
            write_stream,
            mcp_server_instance.get_server().create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_mcp_server())
