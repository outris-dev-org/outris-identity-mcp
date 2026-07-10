"""
Generic catalog-driven executor + the safety guard chain.

Every Tier-1 intent tool (and, in Phase 2, ``smart_lookup``) funnels through
:func:`execute_endpoint` so the money / consent / supplier-scrub / PII-mask
guards are enforced in ONE place and cannot be forgotten per-tool.

Order of operations:
  1. Money gate      — money-movement paths are NEVER invocable here.
  2. Consent gate    — DPDPA consent-required paths need a truthy consent.
  3. Masking posture — read allow_raw_records off the current account.
  4. Backend call    — ``call_backend`` supplier-scrubs the payload + errors.
  5. Default-deny PII mask on the (possibly unknown-shaped) response.
"""
import logging
from typing import Any, Optional

from .helpers import (
    call_backend,
    mask_pii,
    ConsentRequiredError,
    MoneyPathBlockedError,
)
from ..core.context import current_account

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Money-movement paths — real ₹ transfers. NEVER reachable through the generic
# executor or any LLM-driven path. Exact paths only, so the safe read-only
# siblings (pennyless / reverse-penny QR + status polls) stay reachable.
#
# PHASE-2 TODO: promote this to a first-class ``money_movement`` flag on
# ``endpoint_catalog.py`` (the single source of truth) and derive the block from
# there. A hardcoded set in this repo WILL drift the moment a new penny-drop
# provider/path is added on the backend (see the "more penny-drop providers
# incoming" note). Until then, keep this list in lockstep with the backend.
# ---------------------------------------------------------------------------
MONEY_MOVEMENT_PATHS = {
    "/api/kyc/bank/penny-drop",
    "/api/kyc/bank/reverse-penny",
}

# ---------------------------------------------------------------------------
# DPDPA consent-required paths. A truthy consent ("Y") must be forwarded.
# PHASE-2 TODO: derive from ``endpoint_catalog.consent_required`` and enforce via
# MCP elicitation so consent can't be synthesised by the model in the same turn.
# ---------------------------------------------------------------------------
CONSENT_REQUIRED_PATHS = {
    "/api/screening/person",
    "/api/collections/phone",
    "/api/kyc/mobile-to-pan",
    "/api/kyc/pan-to-mobile",
    "/api/kyc/uan-history",
    "/api/vehicle/rc-to-mobile",
    "/api/kyc/aadhaar/okyc/init",
    "/api/kyc/aadhaar/okyc/verify",
}


def _clean_path(path: str) -> str:
    """Strip query string + trailing slash for exact-path matching."""
    return path.split("?", 1)[0].rstrip("/")


def is_money_movement_path(path: str) -> bool:
    return _clean_path(path) in MONEY_MOVEMENT_PATHS


def requires_consent(path: str) -> bool:
    return _clean_path(path) in CONSENT_REQUIRED_PATHS


async def execute_endpoint(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    consent: Optional[str] = None,
) -> Any:
    """Call a backend endpoint through the full guard chain.

    Raises:
        MoneyPathBlockedError: path moves real money (refunded, never fired).
        ConsentRequiredError: consent-required path with no truthy consent.
        BackendError: upstream failure (already supplier-scrubbed).
    """
    # 1. Money gate (defense in depth — money paths are never wired as tools).
    if is_money_movement_path(path):
        logger.warning(f"[generic] BLOCKED money-movement path via executor: {path}")
        raise MoneyPathBlockedError(
            "This action would move real money and cannot be performed "
            "automatically. It must be initiated explicitly by a human."
        )

    # 2. Consent gate.
    if requires_consent(path):
        if not (isinstance(consent, str) and consent.strip().upper() == "Y"):
            raise ConsentRequiredError(
                "This lookup requires the end user's explicit consent. Ask the "
                "user to confirm they consent to the lookup, then set "
                "consent='Y'. Never assume or fabricate consent."
            )

    # 3. Masking posture from the authenticated account.
    account = current_account.get()
    allow_raw = bool(account and getattr(account, "allow_raw_records", False))

    # 4. Backend call — call_backend supplier-scrubs the payload AND raises a
    #    typed, scrubbed BackendError on failure (never leaks upstream text).
    response = await call_backend(path, method=method, params=params, json_data=body)

    # 5. Default-deny PII mask (value-shape first, robust to unknown shapes).
    masked = mask_pii(response, allow_raw=allow_raw)
    if isinstance(masked, dict):
        masked.setdefault("_masked", not allow_raw)
    return masked
