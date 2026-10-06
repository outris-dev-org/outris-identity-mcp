"""
Outris MCP Server - Entry Point for STDIO and HTTP/SSE Transports

This enables running the MCP server locally via STDIO (standard input/output)
or as a web server with Streamable HTTP + SSE.

Features:
- Full MCP tool surface or targeted modular stacks (--stack kyb, --stack ubo, etc.)
- Role/persona filtering (--persona underwriter, --persona compliance, etc.)
- Auto-detection: runs HTTP if a TTY is attached, otherwise runs STDIO

Usage:
    python -m mcp_server                           # Auto-detect (default: STDIO)
    python -m mcp_server --stdio                   # STDIO transport (CLI)
    python -m mcp_server --stdio --stack kyb       # Launch only the KYB stack
    python -m mcp_server --stdio --stack ubo       # Launch only the UBO stack
    python -m mcp_server --http --port 8000        # HTTP + SSE transport
    python -m mcp_server --http --stack kyb        # HTTP transport restricted to KYB
    python -m mcp_server --list-stacks             # Print available modular stacks
"""
import argparse
import asyncio
import os
import sys
import logging
from .core.config import get_settings
from .core.database import Database
from .core.stacks import list_stacks

# Configure logging (to stderr since stdout is used for MCP protocol)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


async def main_stdio(stack: str = None, persona: str = None):
    """Run MCP server with STDIO transport (CLI mode)."""
    # Lazy import — OutrisMCPServer must not load during --http startup
    from .mcp_server import OutrisMCPServer

    logger.info(
        f"Starting Outris MCP Server (STDIO transport, stack={stack or 'full'}, "
        f"persona={persona or 'default'})..."
    )

    # Initialize database
    await Database.connect()
    logger.info("Database connected")

    try:
        server = OutrisMCPServer(stack=stack, persona=persona)
        logger.info(f"MCP Server initialized (stack={stack or 'full'})")

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


async def main_http(host: str = "0.0.0.0", port: int = 8000, stack: str = None, persona: str = None):
    """Run MCP server with HTTP transport (web server mode)."""
    if stack:
        os.environ["MCP_STACK"] = stack
    if persona:
        os.environ["MCP_PERSONA"] = persona

    import uvicorn
    from .server_streamable import app

    logger.info(
        f"Starting Outris MCP Server (HTTP/SSE transports, host={host}, port={port}, "
        f"stack={stack or 'full'})..."
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """Determine transport mode and run."""
    parser = argparse.ArgumentParser(
        description="Outris Identity MCP Server - Identity Verification & Risk Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modular Stacks:
  kyb          Company filings (VPD/AOC-4), UBO, GST, PAN, Bank verification, Enforcement
  ubo          Beneficial ownership tree, MCA/SEBI sanctions, Company resolve, Person DD
  collections  Collections intelligence, Alternate contacts & addresses, Vehicles, Caller ID
  fraud        Carrier SIM risk/telemetry, Phone/email footprints, Digital footprint
  compliance   Regulatory enforcement (58 bodies), UBO, PEP/Sanctions, Law enforcement

Examples:
  python -m mcp_server --stdio --stack kyb
  python -m mcp_server --stdio --stack ubo
  python -m mcp_server --http --port 8000 --stack kyb
  python -m mcp_server --list-stacks
        """
    )
    parser.add_argument("--stdio", action="store_true", help="Force STDIO transport (CLI)")
    parser.add_argument("--http", action="store_true", help="Force HTTP + SSE web transport")
    parser.add_argument("--stack", type=str, default="", help="Restrict tool surface to a specific stack (kyb, ubo, collections, fraud, compliance)")
    parser.add_argument("--persona", type=str, default="", help="Filter surface for a specific operator persona")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Web server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--list-stacks", action="store_true", help="List all available modular tool stacks and exit")

    args = parser.parse_args()

    if args.list_stacks:
        print("\nAvailable Outris MCP Modular Stacks:\n" + "=" * 45)
        for s_name, s_meta in list_stacks().items():
            print(f"\n  [{s_name.upper()}] - {s_meta['title']}")
            print(f"  Description: {s_meta['description']}")
            print(f"  Tools ({len(s_meta['tools'])}): {', '.join(s_meta['tools'])}")
        print()
        sys.exit(0)

    stack = args.stack.strip().lower() if args.stack else None
    persona = args.persona.strip().lower() if args.persona else None

    if args.http:
        logger.info("Running HTTP transport mode")
        asyncio.run(main_http(host=args.host, port=args.port, stack=stack, persona=persona))
    elif args.stdio:
        logger.info("Running STDIO transport mode")
        asyncio.run(main_stdio(stack=stack, persona=persona))
    else:
        # Default auto-detect: TTY -> HTTP; non-TTY / pipe -> STDIO
        if sys.stdin.isatty():
            logger.info("TTY detected - running HTTP transport mode")
            asyncio.run(main_http(host=args.host, port=args.port, stack=stack, persona=persona))
        else:
            logger.info("Non-TTY detected - running STDIO transport mode")
            asyncio.run(main_stdio(stack=stack, persona=persona))


if __name__ == "__main__":
    main()
