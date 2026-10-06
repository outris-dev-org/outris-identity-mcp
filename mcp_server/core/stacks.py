"""
Modular MCP Tool Stacks for Outris TraceFlow.

Allows launching or querying a focused subset of tools tailored for specific
enterprise integration contexts (e.g. KYB onboarding, UBO screening, Skip-tracing/Collections,
or Fraud analysis).
"""
from typing import Dict, List, Optional, Any


STACK_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "kyb": {
        "title": "KYB & Corporate Verification Stack",
        "description": "Company resolution, official MCA VPD filings (AOC-4 / MGT-7), beneficial ownership (UBO), GSTIN registration, business PAN, bank verification, and regulatory enforcement screening.",
        "tools": [
            "resolve_company",
            "fetch_company_filings",
            "lookup_beneficial_ownership",
            "lookup_gst",
            "verify_pan",
            "verify_bank_account",
            "search_unified_enforcement",
        ],
    },
    "ubo": {
        "title": "Ultimate Beneficial Ownership (UBO) Stack",
        "description": "Multi-tier corporate ownership unravelling, natural-person UBOs (>=10%), MCA/SEBI regulatory actions, and executive due-diligence screening.",
        "tools": [
            "lookup_beneficial_ownership",
            "search_unified_enforcement",
            "resolve_company",
            "due_diligence_person_start",
            "check_job",
        ],
    },
    "collections": {
        "title": "Collections & Skip-Tracing Stack",
        "description": "Phone intelligence, consent-gated alternate contactability & geocoded addresses, vehicle RC ownership, caller ID, and bank account validation for debt recovery.",
        "tools": [
            "run_collections_intelligence",
            "find_contacts",
            "lookup_vehicle",
            "verify_bank_account",
            "investigate_phone",
            "check_caller_id",
        ],
    },
    "fraud": {
        "title": "Fraud & Risk Intelligence Stack",
        "description": "Carrier SIM swap/port telemetry, phone ownership risk, email footprint & data breaches, caller-ID aggregation, and digital footprint analysis.",
        "tools": [
            "assess_fraud_risk",
            "investigate_phone",
            "investigate_email",
            "check_caller_id",
            "run_digital_footprint",
        ],
    },
    "compliance": {
        "title": "AML & Regulatory Compliance Stack",
        "description": "Screening across 58 regulatory bodies, PEP, sanctions, UBO unravelling, and privileged law enforcement investigation dossiers.",
        "tools": [
            "search_unified_enforcement",
            "lookup_beneficial_ownership",
            "due_diligence_person_start",
            "check_job",
            "run_law_enforcement_intel",
            "verify_pan",
        ],
    },
}


def get_stack(name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return stack definition by name (case-insensitive)."""
    if not name:
        return None
    clean = name.strip().lower()
    return STACK_DEFINITIONS.get(clean)


def get_stack_tools(name: Optional[str]) -> Optional[List[str]]:
    """Return list of tool names in a stack, or None if stack not found/unrestricted."""
    if not name or name.strip().lower() in ("all", "full", "*"):
        return None
    stack = get_stack(name)
    return stack["tools"] if stack else None


def is_tool_in_stack(tool_name: str, stack_name: Optional[str]) -> bool:
    """Check if a tool belongs to the specified stack."""
    tools = get_stack_tools(stack_name)
    if tools is None:
        return True
    return tool_name in tools


def list_stacks() -> Dict[str, Dict[str, Any]]:
    """List all available stacks with metadata."""
    return STACK_DEFINITIONS.copy()
