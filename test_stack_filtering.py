"""
Tests for Modular MCP Stacks and Tool Filtering.

Verifies:
1. Registration of fetch_company_filings and lookup_beneficial_ownership.
2. ToolRegistry stack and persona filtering.
3. OutrisMCPServer runtime isolation per stack.
4. OpenAPI stack export integrity (no securitySchemes for Vertex AI).
"""
import os
import json
import asyncio
import pytest

os.environ["DATABASE_URL"] = "postgresql://dummy:dummy@localhost/dummy"
os.environ["ANTHROPIC_API_KEY"] = "dummy"
os.environ["JWT_SECRET_KEY"] = "dummy"

from mcp_server.tools.registry import ToolRegistry, get_tool
from mcp_server.core.stacks import (
    STACK_DEFINITIONS,
    get_stack,
    get_stack_tools,
    is_tool_in_stack,
)
from mcp_server.core.auth import MCPAccount
from mcp_server.mcp_server import OutrisMCPServer
import mcp_server.tools.intent_tools


def test_tool_registration():
    """Verify new tools are registered in ToolRegistry."""
    vpd_tool = get_tool("fetch_company_filings")
    assert vpd_tool is not None
    assert vpd_tool.category == "business"
    assert "cin" in vpd_tool.parameters
    assert vpd_tool.parameters["cin"]["required"] is True
    assert "underwriter" in vpd_tool.allowed_personas
    assert "compliance" in vpd_tool.allowed_personas

    ubo_tool = get_tool("lookup_beneficial_ownership")
    assert ubo_tool is not None
    assert ubo_tool.category == "compliance"
    assert "identifier" in ubo_tool.parameters
    assert ubo_tool.parameters["identifier"]["required"] is True
    assert "compliance" in ubo_tool.allowed_personas

    enf_tool = get_tool("search_unified_enforcement")
    assert enf_tool is not None
    assert "underwriter" in enf_tool.allowed_personas


def test_stack_definitions():
    """Verify stack definitions and helper functions."""
    assert "kyb" in STACK_DEFINITIONS
    assert "ubo" in STACK_DEFINITIONS
    assert "collections" in STACK_DEFINITIONS
    assert "fraud" in STACK_DEFINITIONS
    assert "compliance" in STACK_DEFINITIONS

    kyb_tools = get_stack_tools("kyb")
    assert "fetch_company_filings" in kyb_tools
    assert "lookup_beneficial_ownership" in kyb_tools
    assert "resolve_company" in kyb_tools
    assert "lookup_gst" in kyb_tools
    assert "verify_pan" in kyb_tools
    assert "verify_bank_account" in kyb_tools
    assert "search_unified_enforcement" in kyb_tools
    assert len(kyb_tools) == 7

    ubo_tools = get_stack_tools("ubo")
    assert "lookup_beneficial_ownership" in ubo_tools
    assert "search_unified_enforcement" in ubo_tools
    assert "resolve_company" in ubo_tools
    assert "due_diligence_person_start" in ubo_tools
    assert "check_job" in ubo_tools
    assert len(ubo_tools) == 5

    assert is_tool_in_stack("fetch_company_filings", "kyb") is True
    assert is_tool_in_stack("fetch_company_filings", "ubo") is False
    assert is_tool_in_stack("lookup_beneficial_ownership", "ubo") is True
    assert is_tool_in_stack("assess_fraud_risk", "kyb") is False


def test_registry_stack_filtering():
    """Verify ToolRegistry.get_for_stack_and_persona filtering."""
    kyb_filtered = ToolRegistry.get_for_stack_and_persona(stack="kyb")
    assert set(kyb_filtered.keys()) == set(get_stack_tools("kyb"))

    ubo_filtered = ToolRegistry.get_for_stack_and_persona(stack="ubo")
    assert set(ubo_filtered.keys()) == set(get_stack_tools("ubo"))

    # Persona intersection test:
    # 'underwriter' in KYB stack
    underwriter_kyb = ToolRegistry.get_for_stack_and_persona(stack="kyb", persona="underwriter")
    assert "fetch_company_filings" in underwriter_kyb
    assert "resolve_company" in underwriter_kyb
    assert "lookup_gst" in underwriter_kyb


@pytest.mark.asyncio
async def test_mcp_server_stack_isolation():
    """Verify OutrisMCPServer isolates tools when launched with a stack."""
    # Launch KYB server
    kyb_server = OutrisMCPServer(stack="kyb")
    kyb_server.current_account = MCPAccount(
        id=1,
        user_email="test@outris.com",
        display_name="Tester",
        credits_balance=100,
        credits_tier="enterprise",
        is_active=True,
        persona="underwriter",
    )

    tools = await kyb_server.list_available_tools()
    names = [t.name for t in tools]
    assert "fetch_company_filings" in names
    assert "lookup_beneficial_ownership" in names
    assert "resolve_company" in names
    assert "assess_fraud_risk" not in names
    assert "run_collections_intelligence" not in names


def test_openapi_specs():
    """Verify generated OpenAPI specs exist and comply with Vertex AI rules."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. KYB spec
    kyb_path = os.path.join(base_dir, "vertex_openapi_kyb.json")
    assert os.path.exists(kyb_path)
    with open(kyb_path, "r") as f:
        kyb_spec = json.load(f)
    assert len(kyb_spec["paths"]) == 7
    assert "/api/vertex-agent/fetch_company_filings" in kyb_spec["paths"]
    assert "/api/vertex-agent/lookup_beneficial_ownership" in kyb_spec["paths"]
    # Vertex AI Agent Builder rule: No securitySchemes allowed
    assert "securitySchemes" not in kyb_spec.get("components", {})

    # 2. UBO spec
    ubo_path = os.path.join(base_dir, "vertex_openapi_ubo.json")
    assert os.path.exists(ubo_path)
    with open(ubo_path, "r") as f:
        ubo_spec = json.load(f)
    assert len(ubo_spec["paths"]) == 5
    assert "/api/vertex-agent/lookup_beneficial_ownership" in ubo_spec["paths"]
    assert "/api/vertex-agent/fetch_company_filings" not in ubo_spec["paths"]
    assert "securitySchemes" not in ubo_spec.get("components", {})


if __name__ == "__main__":
    test_tool_registration()
    test_stack_definitions()
    test_registry_stack_filtering()
    test_openapi_specs()
    print("ALL TESTS PASSED")
