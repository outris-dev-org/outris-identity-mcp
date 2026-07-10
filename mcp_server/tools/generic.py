"""
Generic catalog-driven executor + the safety guard chain.

Every Tier-1 intent tool (and, in Phase 2, ``smart_lookup``) funnels through
:func:`execute_endpoint` so the money / consent / supplier-scrub / PII-mask
guards are enforced in ONE place and cannot be forgotten per-tool.

Order of operations:
  1. Money gate      — money-movement paths are NEVER invocable here.
  2. Consent gate    — DPDPA consent-required paths need a valid, server-issued
                       consent_token (Phase 3). A model-supplied consent="Y" is
                       only honoured while ``allow_legacy_consent_y`` is True.
  3. Masking posture — read allow_raw_records off the current account.
  4. Backend call    — ``call_backend`` supplier-scrubs the payload + errors.
  5. Default-deny PII mask on the (possibly unknown-shaped) response, with a
     ``control_fields`` exemption for opaque handshake refs (e.g. OKYC
     transaction_id) that would otherwise be mangled by the mask.
"""
import logging
from typing import Any, Optional, Tuple

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
# Module-level bind so tests can monkeypatch generic.validate_and_consume_consent_token
# (mirrors how tests monkeypatch generic.call_backend).
from ..core.consent import validate_and_consume_consent_token
from ..core.config import get_settings

logger = logging.getLogger(__name__)


async def execute_endpoint(
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    consent: Optional[str] = None,
    consent_token: Optional[str] = None,
    account_id: Optional[int] = None,
    consume_consent: bool = True,
    control_fields: Tuple[str, ...] = (),
) -> Any:
    """Call a backend endpoint through the full guard chain.

    Args:
        consent_token: a server-issued, human-gated consent token (preferred).
        consent: legacy model-supplied consent flag ("Y") — only honoured while
            settings.allow_legacy_consent_y is True.
        account_id: explicit account id (used where the contextvar is unset).
        consume_consent: burn the token single-use (False = keep live, e.g.
            OKYC init before verify).
        control_fields: response keys exempt from PII masking (opaque handshake
            refs like OKYC transaction_id).

    Raises:
        MoneyPathBlockedError: path moves real money (refunded, never fired).
        ConsentRequiredError: consent-required path without a valid token.
        BackendError: upstream failure (already supplier-scrubbed).
    """
    # 1. Money gate (defense in depth — money paths are never wired as tools).
    if is_money_movement_path(path):
        logger.warning(f"[generic] BLOCKED money-movement path via executor: {path}")
        raise MoneyPathBlockedError(
            "This action would move real money and cannot be performed "
            "automatically. It must be initiated explicitly by a human."
        )

    account = current_account.get()
    acct_id = account_id if account_id is not None else (account.id if account else None)

    # 2. Consent gate — token-first. The SERVER injects consent from the validated
    #    grant; a model-supplied "Y" is never forwarded as the consent of record.
    if requires_consent(path):
        grant = await validate_and_consume_consent_token(
            consent_token, path, acct_id, consume=consume_consent
        )
        legacy_ok = (
            get_settings().allow_legacy_consent_y
            and isinstance(consent, str) and consent.strip().upper() == "Y"
        )
        if grant is None and not legacy_ok:
            raise ConsentRequiredError(
                "This lookup needs the end user's real consent. Ask the user to "
                "open the consent link in portal.outris.com/mcp and re-run with "
                "the consent_token they receive. A typed 'Y' is not accepted."
            )
        # Server-inject the consent of record (from the grant, not the model).
        body = {**(body or {}), "consent": "Y"}
        if grant and grant.consent_text:
            body["consent_text"] = grant.consent_text

    # 3. Masking posture from the authenticated account.
    allow_raw = bool(account and getattr(account, "allow_raw_records", False))

    # 4. Backend call — call_backend supplier-scrubs the payload AND raises a
    #    typed, scrubbed BackendError on failure (never leaks upstream text).
    response = await call_backend(path, method=method, params=params, json_data=body)

    # 5. Default-deny PII mask (value-shape first, robust to unknown shapes).
    masked = mask_pii(response, allow_raw=allow_raw)
    if isinstance(masked, dict):
        # Restore opaque handshake refs the mask would have mangled (control,
        # not PII) — e.g. a numeric OKYC transaction_id caught by the digit rule.
        if control_fields and isinstance(response, dict):
            for f in control_fields:
                if f in response:
                    masked[f] = response[f]
        masked.setdefault("_masked", not allow_raw)
    return masked
