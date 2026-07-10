"""
Tool Registry for MCP Server.
Handles tool registration, discovery, and execution.
"""
import logging
import time
import uuid
from typing import Callable, Any, Optional
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Definition of an MCP tool."""
    name: str
    description: str
    credits: int
    handler: Callable
    parameters: dict = field(default_factory=dict)
    category: str = "general"
    enabled: bool = True


class ToolRegistry:
    """Registry for all available MCP tools."""
    
    _tools: dict[str, ToolDefinition] = {}
    
    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        credits: int,
        parameters: dict = None,
        category: str = "general",
        enabled: bool = True
    ) -> Callable:
        """Decorator to register a tool."""
        def decorator(func: Callable) -> Callable:
            cls._tools[name] = ToolDefinition(
                name=name,
                description=description,
                credits=credits,
                handler=func,
                parameters=parameters or {},
                category=category,
                enabled=enabled
            )
            status = "enabled" if enabled else "DISABLED"
            logger.info(f"Registered tool: {name} ({credits} credits) [{status}]")
            return func
        return decorator
    
    @classmethod
    def get(cls, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name."""
        return cls._tools.get(name)
    
    @classmethod
    def get_all(cls) -> dict[str, ToolDefinition]:
        """Get all registered tools."""
        return cls._tools.copy()
    
    @classmethod
    def get_enabled(cls) -> dict[str, ToolDefinition]:
        """Get all enabled tools."""
        return {k: v for k, v in cls._tools.items() if v.enabled}
    
    @classmethod
    def to_mcp_format(cls) -> list[dict]:
        """Convert all tools to MCP protocol format."""
        tools = []
        for name, tool in cls.get_enabled().items():
            # Build properties dict without 'required' key (invalid in JSON Schema properties)
            clean_properties = {}
            for param_name, param_def in tool.parameters.items():
                clean_properties[param_name] = {
                    k: v for k, v in param_def.items() if k != "required"
                }

            tools.append({
                "name": name,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": clean_properties,
                    "required": [
                        k for k, v in tool.parameters.items() 
                        if v.get("required", False)
                    ]
                }
            })
        return tools


# Convenience decorator
def tool(
    name: str,
    description: str,
    credits: int,
    parameters: dict = None,
    category: str = "general",
    enabled: bool = True
) -> Callable:
    """
    Decorator to register a function as an MCP tool.
    
    Usage:
        @tool(
            name="verify_pan",
            description="Verify a PAN number",
            credits=1,
            parameters={
                "pan": {"type": "string", "description": "PAN number", "required": True}
            }
        )
        async def verify_pan(pan: str) -> dict:
            ...
    """
    return ToolRegistry.register(name, description, credits, parameters, category, enabled)


def get_all_tools() -> dict[str, ToolDefinition]:
    """Get all registered tools."""
    return ToolRegistry.get_all()


def get_tool(name: str) -> Optional[ToolDefinition]:
    """Get a tool by name."""
    return ToolRegistry.get(name)


async def execute_tool(
    name: str,
    arguments: dict,
    account_id: int = None,
    credit_request_id: str = None,
    user_jwt: str = None,
) -> tuple[dict, float]:
    """
    Execute a tool and return result with execution time.

    Returns:
        Tuple of (result_dict, execution_time_ms)
    """
    tool_def = ToolRegistry.get(name)
    if tool_def is None:
        raise ValueError(f"Unknown tool: {name}")

    if not tool_def.enabled:
        raise ValueError(f"Tool is disabled: {name}")

    start_time = time.time()

    from ..core.context import current_account, current_credit_request_id, current_user_jwt
    token = None
    cr_token = None
    jwt_token = None
    try:
        # Set context if account_id provided
        if account_id:
            from ..core.auth import get_account_by_id

            account = await get_account_by_id(account_id)
            if account:
                token = current_account.set(account)

        # Thread the credit-transaction id so an async job can refund on the
        # SAME request id later (Phase 3 async jobs).
        if credit_request_id:
            cr_token = current_credit_request_id.set(credit_request_id)

        # Thread the acting user's portal JWT so call_backend can bill via the
        # per-user portal proxy (Phase 2 "shadow"/"sso" billing modes).
        if user_jwt:
            jwt_token = current_user_jwt.set(user_jwt)

        result = await tool_def.handler(**arguments)
        execution_time = (time.time() - start_time) * 1000
        return result, execution_time

    except Exception as e:
        execution_time = (time.time() - start_time) * 1000
        logger.error(f"Tool {name} failed: {e}")
        raise

    finally:
        # Reset context
        if token:
            current_account.reset(token)
        if cr_token:
            current_credit_request_id.reset(cr_token)
        if jwt_token:
            current_user_jwt.reset(jwt_token)
