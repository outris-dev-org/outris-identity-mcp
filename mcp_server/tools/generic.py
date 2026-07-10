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
# Money / consent classification is derived from the capability catalog — the
# SINGLE source of truth in this repo (Phase 2, review must-fix #1). generic.py
# no longer keeps its own duplicate sets that would drift from the backend.
# (PHASE-3 follow-up: generate that catalog from number-lookup's
# endpoint_catalog.py so the ultimate source of truth is the backend itself.)
from .capability_catalog import is_money_movement_path, requires_consent
from ..core.context import current_account

logger = logging.getLogger(__name__)


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
