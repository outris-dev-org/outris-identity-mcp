"""
MCP Server Public Routes

Public endpoints for anonymous playground access.
Rate-limited to 3 tries per IP per day.
Includes PII Sanitization for demo safety.
"""
import os
import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel

from ..core.database import Database
from ..tools.registry import get_tool, execute_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["Public"])

# Rate limiting configuration
DAILY_LIMIT = 3
ALLOWED_TOOLS = ["phone_to_name", "check_online_platforms", "digital_commerce"]
DEMO_FULL_ACCESS_KEY = os.getenv("DEMO_FULL_ACCESS_KEY") # If set, this key bypasses sanitization

# In-memory rate limit store
_rate_limit_store: Dict[str, Dict[str, Any]] = {}


class TryToolRequest(BaseModel):
    tool: str
    inputs: Dict[str, Any]


class TryToolResponse(BaseModel):
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    remaining_tries: int
    demo_mode: bool = True
    sanitized: bool = False


# ============================================================================
# PII Sanitization
# ============================================================================

def mask_email(email: str) -> str:
    """Mask email: s***@gmail.com"""
    if not email or "@" not in email: return email
    user, domain = email.split("@", 1)
    if len(user) <= 1:
        return f"{user}***@{domain}"
    return f"{user[0]}***@{domain}"

def mask_phone(phone: str) -> str:
    """Mask phone: +9195***1234"""
    if not phone or len(phone) < 6: return phone
    # Keep first 5 (country+prefix) and last 2? Or first 5, last 3?
    # +919592366712 -> +9195***712
    return f"{phone[:5]}***{phone[-3:]}"

def mask_name(name: str) -> str:
    """Mask name: J*** D***"""
    if not name: return name
    parts = name.split()
    masked_parts = []
    for part in parts:
        if len(part) > 1:
            masked_parts.append(f"{part[0]}***")
        else:
            masked_parts.append(part)
    return " ".join(masked_parts)

def sanitize_data(data: Any) -> Any:
    """Recursively sanitize PII from JSON-compatiable data."""
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            key_lower = k.lower()
            
            # recursive trigger
            if isinstance(v, (dict, list)):
                new_dict[k] = sanitize_data(v)
            
            # PII Fields
            elif "email" in key_lower and isinstance(v, str):
                new_dict[k] = mask_email(v)
            elif ("phone" in key_lower or "mobile" in key_lower) and isinstance(v, str):
                new_dict[k] = mask_phone(v)
            elif "name" in key_lower and "user" not in key_lower and "tool" not in key_lower and isinstance(v, str):
                # Mask names but avoid masking usernames if they look non-PII? 
                # Strict approach: mask if key contains 'name'
                new_dict[k] = mask_name(v)
            elif "address" in key_lower and isinstance(v, str):
                new_dict[k] = "Address Hidden (Demo)"
            elif "pan" in key_lower or "tax" in key_lower or "id" in key_lower:
                if isinstance(v, str) and len(v) > 4:
                     new_dict[k] = f"***{v[-4:]}" # Mask IDs
                else:
                     new_dict[k] = v
            else:
                new_dict[k] = v
        return new_dict
    
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    
    return data


# ============================================================================
# Helpers
# ============================================================================

def _get_ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _get_today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _check_rate_limit(ip_hash: str) -> tuple[bool, int]:
    today = _get_today_key()
    keys_to_delete = [k for k in _rate_limit_store if not k.endswith(today)]
    for k in keys_to_delete:
        del _rate_limit_store[k]
    
    key = f"{ip_hash}:{today}"
    usage = _rate_limit_store.get(key, {"count": 0})
    remaining = DAILY_LIMIT - usage["count"]
    return remaining > 0, max(0, remaining)


def _increment_usage(ip_hash: str) -> int:
    today = _get_today_key()
    key = f"{ip_hash}:{today}"
    if key not in _rate_limit_store:
        _rate_limit_store[key] = {"count": 0}
    _rate_limit_store[key]["count"] += 1
    return max(0, DAILY_LIMIT - _rate_limit_store[key]["count"])


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/tools")
async def get_available_tools():
    tools = []
    for tool_name in ALLOWED_TOOLS:
        tool_def = get_tool(tool_name)
        if tool_def:
            tools.append({
                "name": tool_name,
                "description": tool_def.description.split("\\n")[0],
                "credits": tool_def.credits,
                "category": tool_def.category,
                "parameters": tool_def.parameters
            })
    return {"tools": tools, "daily_limit": DAILY_LIMIT, "demo_mode": True}


@router.post("/try-tool", response_model=TryToolResponse)
async def try_tool_anonymous(
    request: Request, 
    body: TryToolRequest,
    x_full_access_key: Optional[str] = Header(None, alias="X-Full-Access-Key")
):
    """Anonymous playground endpoint with Sanitization."""
    client_ip = request.client.host if request.client else "unknown"
    if request.headers.get("X-Forwarded-For"):
        client_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
    
    ip_hash = _get_ip_hash(client_ip)
    is_allowed, remaining = _check_rate_limit(ip_hash)
    
    if not is_allowed:
        raise HTTPException(429, detail={"error": "Daily limit reached.", "remaining_tries": 0})
    
    if body.tool not in ALLOWED_TOOLS:
        return TryToolResponse(success=False, error="Tool not available", remaining_tries=remaining)
    
    try:
        logger.info(f"Demo tool call: {body.tool} from IP hash {ip_hash}")
        result, _ = await execute_tool(body.tool, body.inputs)
        remaining = _increment_usage(ip_hash)
        
        # Check if full access is authorized
        is_full_access = False
        if DEMO_FULL_ACCESS_KEY and x_full_access_key and x_full_access_key == DEMO_FULL_ACCESS_KEY:
            is_full_access = True
            
        final_result = result if is_full_access else sanitize_data(result)
        
        return TryToolResponse(
            success=True, 
            result=final_result, 
            remaining_tries=remaining,
            sanitized=not is_full_access
        )
    except Exception as e:
        logger.error(f"Demo tool error: {e}")
        return TryToolResponse(success=False, error=str(e), remaining_tries=remaining)


@router.get("/remaining-tries")
async def get_remaining_tries(request: Request):
    """Anonymous tries-left counter for the playground (read-only, no increment)."""
    client_ip = request.client.host if request.client else "unknown"
    if request.headers.get("X-Forwarded-For"):
        client_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
    _, remaining = _check_rate_limit(_get_ip_hash(client_ip))
    return {"remaining_tries": remaining, "daily_limit": DAILY_LIMIT}


# ============================================================================
# Demo Platform Check
# ============================================================================

demo_router = APIRouter(prefix="/api/demo", tags=["Demo"])

@demo_router.get("/platform-check")
async def demo_platform_check(
    phone: str, 
    request: Request,
    x_full_access_key: Optional[str] = Header(None, alias="X-Full-Access-Key")
):
    """Platform check demo with PII Sanitization."""
    client_ip = request.client.host if request.client else "unknown"
    if request.headers.get("X-Forwarded-For"):
        client_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
    
    ip_hash = _get_ip_hash(client_ip)
    is_allowed, remaining = _check_rate_limit(ip_hash)
    
    if not is_allowed:
        raise HTTPException(429, detail="Limit reached")
        
    try:
        result, exec_time = await execute_tool("check_online_platforms", {"phone": phone})
        logger.info(f"DEBUG RAW PLATFORM RESULT for {phone}: {result}") # Debug log
        remaining = _increment_usage(ip_hash)
        
        # Format response (logic from previous step)
        registered_platforms = result.get("registered_platforms", [])
        not_registered = result.get("not_registered", [])
        
        platforms_dict = {}
        for p in registered_platforms:
             p_name = p.get("platform") or p.get("name") if isinstance(p, dict) else p
             if p_name: 
                 platforms_dict[p_name.lower()] = {"domain": p_name.lower(), "exists": True, "status": "registered"}
        
        for p in not_registered:
             p_name = p.get("platform") or p.get("name") if isinstance(p, dict) else p
             if p_name: 
                 platforms_dict[p_name.lower()] = {"domain": p_name.lower(), "exists": False, "status": "not_registered"}
        
        if not platforms_dict:
            # Default placeholder logic
             pass
             
        # Apply Sanitization to the raw result if needed, but here we construct a custom response.
        # The custom response doesn't expose PII usually, except phone number.
        
        # Check full access
        is_full_access = False
        if DEMO_FULL_ACCESS_KEY and x_full_access_key and x_full_access_key == DEMO_FULL_ACCESS_KEY:
            is_full_access = True
            
        response_data = {
            "phone": phone if is_full_access else mask_phone(phone),
            "platforms_checked": result.get("platforms_checked", 0),
            "platforms": platforms_dict,
            "registered_platforms": registered_platforms, # This might contain raw details!
            "remaining_tries": remaining,
            "execution_time": exec_time,
            "sanitized": not is_full_access
        }
        
        if not is_full_access:
            # Sanitize the registered_platforms list itself as it might contain profile URLs or names
            response_data["registered_platforms"] = sanitize_data(registered_platforms)
            
        return response_data
        
    except Exception as e:
        logger.error(f"Demo error: {e}")
        raise HTTPException(500, detail=str(e))
