"""
Helper utilities for tool implementations.

Phase-0 safety substrate (2026-07):
  * ``BackendError`` — a typed backend failure carrying ``status_code`` so the
    credit layer can decide refund-vs-charge on the status, never on a substring
    of the message (which the supplier-scrub would corrupt anyway).
  * ``scrub_supplier_names`` — UNCONDITIONAL removal of internal supplier names
    from anything returned to a client (success payloads AND error text). See
    number-lookup CLAUDE.md "Never Leak Supplier Names to Clients".
  * ``mask_pii`` — DEFAULT-DENY PII masking for accounts without
    ``allow_raw_records``; primary signal is the value SHAPE (so it works on
    unknown response shapes), key-name is only a secondary signal.
"""
import re
import httpx
import logging
from typing import Optional, Any

from ..core.config import get_settings

logger = logging.getLogger(__name__)

# Shared HTTP client
_client: Optional[httpx.AsyncClient] = None


# ---------------------------------------------------------------------------
# Typed backend error
# ---------------------------------------------------------------------------
class BackendError(Exception):
    """A failure talking to the number-lookup backend.

    ``client_message`` is already supplier-scrubbed and safe to surface.
    Refund policy keys off ``status_code``/``is_timeout``, NEVER the message.
    """

    def __init__(self, status_code: int, client_message: str, is_timeout: bool = False):
        self.status_code = status_code
        self.client_message = client_message
        self.is_timeout = is_timeout
        super().__init__(client_message)


class PreflightRejection(Exception):
    """A request rejected BEFORE any backend work (policy gate).

    No lookup happened, so the credit layer must REFUND — the user is not
    charged for a consent/plan/policy rejection. ``code`` is a stable machine
    tag; ``client_message`` is safe to surface.
    """

    code = "preflight_rejected"

    def __init__(self, client_message: str, code: Optional[str] = None):
        self.client_message = client_message
        if code:
            self.code = code
        super().__init__(client_message)


class ConsentRequiredError(PreflightRejection):
    code = "consent_required"


class MoneyPathBlockedError(PreflightRejection):
    code = "money_movement_blocked"


class BetaNotEnabledError(PreflightRejection):
    code = "not_enabled_for_plan"


# ---------------------------------------------------------------------------
# Supplier-name scrub (unconditional)
# ---------------------------------------------------------------------------
# Internal vendor names that must NEVER reach a client-facing payload, log we
# return, or error string. Keep in sync with number-lookup CLAUDE.md banned list
# (plus vendors added since). Matched case-insensitively with a trailing-word
# allowance so "EnrichData's" / "gridlines_v2" are caught too.
_SUPPLIER_NAMES = [
    "sign3", "hibp", "haveibeenpwned", "leakosint", "leak-osint", "leak_osint",
    "gridlines", "aitan", "crimescan", "enrichdata", "totalekyc", "kyczen",
    "monnai", "trustfull", "brightdata", "bulkpe", "digitap", "paysprint",
    "surepass", "smartauth", "zumigo", "finaxle", "microvista", "jamku",
    "cleartax", "azapi", "datasutram", "paasoo", "onemoney", "harvestapi",
    "befisc", "tenacio", "ironbeaver",
]
_SUPPLIER_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(n) for n in _SUPPLIER_NAMES) + r")\w*"
)
# Supplier-code aliases (s1..s14, v1/v2) — only scrubbed when they appear as the
# VALUE of a supplier-identifying key, to avoid mangling unrelated strings.
_SUPPLIER_CODE_RE = re.compile(r"(?i)^\s*(?:s\d{1,2}|v\d)\s*$")
_SUPPLIER_KEY_HINTS = ("supplier", "provider", "vendor", "gateway", "upstream", "data_source")


def _is_supplier_key(key: Optional[str]) -> bool:
    if not key:
        return False
    k = key.lower()
    return any(h in k for h in _SUPPLIER_KEY_HINTS) or k == "source"


def scrub_text(value: str) -> str:
    """Strip supplier names from a free-text string."""
    return _SUPPLIER_RE.sub("supplier", value)


def scrub_supplier_names(obj: Any, key: Optional[str] = None) -> Any:
    """Recursively remove internal supplier names from a payload/error object."""
    if isinstance(obj, dict):
        return {k: scrub_supplier_names(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_supplier_names(v, key) for v in obj]
    if isinstance(obj, str):
        if _is_supplier_key(key) and _SUPPLIER_CODE_RE.match(obj):
            return "outris"
        return scrub_text(obj)
    return obj


# ---------------------------------------------------------------------------
# Default-deny PII masking (value-shape first, key-name second)
# ---------------------------------------------------------------------------
_PAN_RE = re.compile(r"^[A-Za-z]{5}[0-9]{4}[A-Za-z]$")
_AADHAAR_RE = re.compile(r"^\d{4}\s?\d{4}\s?\d{4}$")
_PHONE_RE = re.compile(r"^(?:\+?91[\-\s]?)?[6-9]\d{9}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOB_RE = re.compile(r"^(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Za-z]{5}\d{4}[A-Za-z]\d[A-Za-z\d]{3}$")
_LONG_DIGITS_RE = re.compile(r"^\d{9,}$")  # bank account / long IDs
_VPA_RE = re.compile(r"^[\w.\-]{2,}@[a-zA-Z]{2,}$")

# Keys whose values are personal PII even when the value shape is generic text
# (names, addresses). Company/entity names are NOT personal PII — excluded so
# KYB tools (resolve_company, lookup_gst) stay useful for masked users.
_PII_KEY_EXACT = {
    "name", "full_name", "first_name", "middle_name", "last_name", "fathers_name",
    "father_name", "mother_name", "mothers_name", "spouse_name", "husband_name",
    "wife_name", "care_of", "address", "full_address", "permanent_address",
    "present_address", "correspondence_address", "email", "phone", "mobile",
    "dob", "date_of_birth", "aadhaar", "aadhaar_number", "account_number", "vpa",
}
_PII_KEY_DENY = ("company", "entity", "firm", "organisation", "organization",
                 "business", "trade", "bank", "branch", "file")


def _looks_like_pii(value: str) -> bool:
    v = value.strip()
    if not v or len(v) < 4:
        return False
    return bool(
        _PAN_RE.match(v) or _AADHAAR_RE.match(v) or _PHONE_RE.match(v)
        or _EMAIL_RE.match(v) or _DOB_RE.match(v) or _GSTIN_RE.match(v)
        or _LONG_DIGITS_RE.match(v) or _VPA_RE.match(v)
    )


def _is_pii_key(key: Optional[str]) -> bool:
    if not key:
        return False
    k = key.lower()
    if any(d in k for d in _PII_KEY_DENY):
        return False
    return k in _PII_KEY_EXACT or k.endswith("_name") or k.endswith("_address")


def mask_pii(obj: Any, allow_raw: bool = False, key: Optional[str] = None) -> Any:
    """Default-deny PII mask. When ``allow_raw`` is True, returns unchanged.

    Otherwise every string leaf is masked if its VALUE looks like PII (works on
    unknown shapes) or its KEY is a known personal field. Leaf masking reuses the
    length-preserving :func:`mask_sensitive`.
    """
    if allow_raw:
        return obj
    if isinstance(obj, dict):
        return {k: mask_pii(v, allow_raw, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_pii(v, allow_raw, key) for v in obj]
    if isinstance(obj, str):
        if _looks_like_pii(obj) or _is_pii_key(key):
            return mask_sensitive(obj)
        return obj
    return obj


async def get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            follow_redirects=True
        )
    return _client


async def close_http_client() -> None:
    """Close shared HTTP client."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call_backend(
    endpoint: str,
    method: str = "GET",
    params: dict = None,
    json_data: dict = None,
    api_key: str = None
) -> dict:
    """
    Call the backend API (number-lookup).
    
    Args:
        endpoint: API endpoint (e.g., "/api/kyc/pan/details")
        method: HTTP method
        params: Query parameters
        json_data: JSON body for POST requests
        api_key: API key (uses configured backend key if not provided)
    
    Returns:
        Response JSON as dict
    """
    settings = get_settings()

    # Phase 2: in "shadow"/"sso" billing mode, route USER traffic through the
    # per-user portal proxy so the BFF meters + bills the user's own key natively.
    # An explicit api_key (internal callers) or "ledger" mode keeps the direct
    # shared-key path unchanged. The mode is per-request (per-email canary aware).
    from ..core.config import get_effective_billing_mode
    mode = get_effective_billing_mode()
    if mode != "ledger" and api_key is None:
        return await _call_backend_via_proxy(endpoint, method, params, json_data)

    client = await get_http_client()
    url = f"{settings.backend_url}{endpoint}"
    headers = {
        "X-API-Key": api_key or settings.backend_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    logger.debug(f"Calling backend: {method} {url}")

    try:
        if method.upper() == "GET":
            response = await client.get(url, params=params, headers=headers)
        elif method.upper() == "POST":
            response = await client.post(url, params=params, json=json_data, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()
        # UNCONDITIONAL supplier-name scrub on the way out — the client must
        # never see an internal vendor name, even in nested/unknown fields.
        return scrub_supplier_names(response.json())

    except httpx.HTTPStatusError as e:
        # Log the raw upstream body INTERNALLY only; NEVER surface it (it can
        # contain supplier names / PII). Raise a typed, scrubbed error instead.
        logger.error(f"Backend HTTP error: {e.response.status_code} - {e.response.text[:500]}")
        raise BackendError(
            status_code=e.response.status_code,
            client_message=f"Upstream service returned HTTP {e.response.status_code}.",
        )
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        logger.error(f"Backend timeout: {e}")
        raise BackendError(status_code=504, client_message="Upstream service timed out.", is_timeout=True)
    except BackendError:
        raise
    except Exception as e:
        logger.error(f"Backend call failed: {e}")
        raise BackendError(status_code=502, client_message="Failed to reach the upstream service.")


async def _call_backend_via_proxy(endpoint: str, method: str, params: dict, json_data: dict) -> dict:
    """Call the BFF as the acting user via POST /api/portal/execute.

    The BFF decrypts the user's own portal key, loopbacks the real request, and
    attributes usage/billing to the user's public.api_keys family — so the MCP
    needs NO credit ledger of its own. The proxy returns a wrapper
    ``{status_code, ok, body}``; a wrapped status >= 400 becomes a typed
    BackendError so classify_tool_error / async-refund logic is unchanged.
    """
    from ..core.context import current_user_jwt, current_credit_request_id

    settings = get_settings()
    jwt = current_user_jwt.get()
    if not jwt:
        # No acting user session -> cannot bill anyone; fail closed (refundable).
        raise BackendError(status_code=401, client_message="Missing user session for this request.")

    client = await get_http_client()
    proxy_url = f"{settings.backend_url}/api/portal/execute"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    cr_id = current_credit_request_id.get()
    if cr_id:
        headers["X-Transaction-Id"] = str(cr_id)  # correlate mcp.* tool lens <-> api_usage_log
    payload = {
        "method": method.upper(),
        "path": endpoint,
        "body": json_data,
        "query": params,
    }

    logger.debug(f"Calling backend via portal proxy: {method} {endpoint}")
    try:
        response = await client.post(proxy_url, json=payload, headers=headers)
        response.raise_for_status()      # proxy-level failure (503 no-encryption, 500, 502)
        wrapped = response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Portal proxy HTTP error: {e.response.status_code} - {e.response.text[:300]}")
        raise BackendError(
            status_code=e.response.status_code,
            client_message=f"Upstream service returned HTTP {e.response.status_code}.",
        )
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        logger.error(f"Portal proxy timeout: {e}")
        raise BackendError(status_code=504, client_message="Upstream service timed out.", is_timeout=True)
    except BackendError:
        raise
    except Exception as e:
        logger.error(f"Portal proxy call failed: {e}")
        raise BackendError(status_code=502, client_message="Failed to reach the upstream service.")

    # Unwrap {status_code, ok, body}. A wrapped 4xx/5xx maps to BackendError so
    # the existing refund / classification logic keeps working.
    status = wrapped.get("status_code", 200)
    body = wrapped.get("body")
    if status >= 400:
        logger.error(f"Proxied backend returned {status} for {endpoint}")
        raise BackendError(
            status_code=status,
            client_message=f"Upstream service returned HTTP {status}.",
        )
    return scrub_supplier_names(body)


def normalize_phone(phone: str) -> str:
    """Normalize a phone number to standard format."""
    # Remove common prefixes and formatting
    phone = phone.strip()
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # Remove + prefix
    if phone.startswith("+"):
        phone = phone[1:]
    
    # Add 91 prefix for 10-digit Indian numbers
    if len(phone) == 10 and phone[0] in "6789":
        phone = "91" + phone
    
    return phone


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """
    Smart masking for sensitive data that PRESERVES LENGTH.
    
    Strategies:
    - Email (>7 chars local): sa***ab***i@gmail.com (First 2, Middle 2, Last 1 kept)
    - Phone (>=10 digits): 91***5***7890 (First 2, Middle 1, Last 4 kept)
    - General (>8 chars): Sau...thi (First 3, Last 3 kept)
    - General (short): J**n (First 1, Last 1 kept)
    """
    if not value:
        return value
        
    value = str(value).strip()
    length = len(value)
    
    # Helper to mask string while keeping specific indices
    def apply_mask(text: str, keep_indices: set) -> str:
        return "".join([c if i in keep_indices else "*" for i, c in enumerate(text)])

    # Email Masking
    if "@" in value and "." in value:
        try:
            local, domain = value.split("@", 1)
            local_len = len(local)
            
            if local_len > 7:
                # Keep First 2, Middle 2, Last 1
                mid = local_len // 2
                indices = {0, 1, mid-1, mid, local_len-1}
                masked_local = apply_mask(local, indices)
            elif local_len > 2:
                # Keep First 1, Last 1
                indices = {0, local_len-1}
                masked_local = apply_mask(local, indices)
            else:
                # Keep First 1
                masked_local = local[0] + "*" * (local_len - 1)
                
            return f"{masked_local}@{domain}"
        except:
            pass # Fallback
            
    # Phone Masking (numeric check)
    if value.replace("+", "").isdigit() and length >= 10:
        # Keep First 2, Middle 1, Last 4
        mid = length // 2
        indices = {0, 1, mid}
        # Add last 4 indices
        for i in range(length - 4, length):
            indices.add(i)
            
        return apply_mask(value, indices)
        
    # General String (Names/Addresses)
    if length > 8:
        # Keep First 3, Last 3
        indices = {0, 1, 2, length-3, length-2, length-1}
        return apply_mask(value, indices)
    elif length > 2:
        # Keep First 1, Last 1
        indices = {0, length-1}
        return apply_mask(value, indices)
    else:
        # Keep First 1
        return value[0] + "*" * (length - 1)


def classify_tool_error(e: Exception) -> tuple[bool, str, str]:
    """Classify a tool-execution exception for the credit layer.

    Returns ``(should_refund, error_code, client_message)``:
      * ``PreflightRejection`` — refund (no backend work happened).
      * ``BackendError`` — refund iff 5xx/timeout (our fault); a 4xx is the
        caller's bad input, so it's charged.
      * anything else — charged as an internal execution error; the message is
        NOT surfaced (it may contain PII/supplier names — never ``str(e)``).

    The client message is always supplier-scrubbed.
    """
    if isinstance(e, PreflightRejection):
        return True, e.code, scrub_text(e.client_message)
    if isinstance(e, BackendError):
        should_refund = e.status_code >= 500 or e.is_timeout
        code = "backend_error" if should_refund else "upstream_rejected"
        return should_refund, code, scrub_text(e.client_message)
    # Unknown/internal error — do not leak details to the client.
    return False, "execution_error", "An unexpected error occurred processing this request."


def summarize_response(response: dict, max_items: int = 5) -> dict:
    """Create a summary of a response for logging (truncate lists)."""
    summary = {}
    for key, value in response.items():
        if isinstance(value, list):
            summary[key] = f"[{len(value)} items]" if len(value) > max_items else value
        elif isinstance(value, dict):
            summary[key] = summarize_response(value, max_items)
        else:
            summary[key] = value
    return summary
