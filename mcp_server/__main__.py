"""
Outris MCP Server - Entry Point for STDIO Transport

This enables running the MCP server locally via STDIO (standard input/output).
Perfect for:
- Local CLI usage
- Integration with tools that support STDIO transport
- Development and testing

Usage:
    python -m mcp_server

Or for deployment:
    python -m mcp_server --host 0.0.0.0 --port 8000  # For HTTP/SSE
"""
import asyncio
import sys
import logging
from .core.config import get_settings
from .core.database import Database

# Configure logging (to stderr since stdout is used for MCP protocol)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


async def main_stdio():
    """Run MCP server with STDIO transport (CLI mode)."""
    # Lazy import — OutrisMCPServer must not load during --http startup
    # (module-level init uses MCP SDK APIs that differ by package version).
    from .mcp_server import OutrisMCPServer

    logger.info("Starting Outris MCP Server (STDIO transport)...")

    # Initialize database
    await Database.connect()
    logger.info("Database connected")

    try:
        # Create and run MCP server
        server = OutrisMCPServer()
        logger.info("MCP Server initialized")

        # Import stdio_server
        from mcp.server.stdio import stdio_server

        logger.info("Running STDIO transport...")
        async with stdio_server() as (read_stream, write_stream):
            logger.info("STDIO streams established")
            await server.server.run(
                read_stream,
                write_stream,
                server.server.create_initialization_options()
            )
    except Exception as e:
        logger.error(f"Error running STDIO server: {e}", exc_info=True)
        raise
    finally:
        await Database.disconnect()
        logger.info("Shutdown complete")


async def main_http():
    """Run MCP server with HTTP transport (web server mode)."""
    import uvicorn
    from .server_streamable import app

    logger.info("Starting Outris MCP Server (HTTP/SSE transports)...")

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """Determine transport mode and run."""
    settings = get_settings()

    # Check command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--http":
            logger.info("Running HTTP transport mode")
            asyncio.run(main_http())
        elif sys.argv[1] == "--stdio":
            logger.info("Running STDIO transport mode")
            asyncio.run(main_stdio())
        elif sys.argv[1] == "--help":
            print("""
Outris MCP Server

Usage:
    python -m mcp_server                    # Auto-detect (default: STDIO)
    python -m mcp_server --stdio            # STDIO transport (CLI)
    python -m mcp_server --http             # HTTP + SSE transport (web server)
    python -m mcp_server --help             # Show this help

Environment Variables:
    OUTRIS_API_KEY                  # Default API key for clients
    DATABASE_URL                    # PostgreSQL connection string
    ENABLE_KYC_TOOLS               # Enable KYC tools (true/false)
    LOG_LEVEL                       # Logging level (INFO, DEBUG, etc.)

Endpoints (HTTP mode):
    GET  /                          # Server info
    GET  /health                    # Health check
    GET  /tools                     # List available tools
    POST /http                      # Streamable HTTP transport
    GET  /sse                       # SSE transport (legacy)
""")
        else:
            logger.error(f"Unknown argument: {sys.argv[1]}")
            sys.exit(1)
    else:
        # Default: auto-detect based on stdin
        # If stdin is a TTY, run HTTP; otherwise run STDIO
        if sys.stdin.isatty():
            logger.info("TTY detected - running HTTP transport mode")
            asyncio.run(main_http())
        else:
            logger.info("Non-TTY detected - running STDIO transport mode")
            asyncio.run(main_stdio())


if __name__ == "__main__":
    main()
