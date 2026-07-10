"""
Server-issued consent tokens (Phase 3a).

Why: consent-required (DPDPA) lookups must be authorised by a real HUMAN, not by
a value the connecting model can synthesise. Real MCP elicitation is impossible
on our stateless Streamable-HTTP transport (and unsupported by the Claude.ai
client), so we use a transport-agnostic handshake instead:

    1. The human opens a consent screen in portal.outris.com/mcp and clicks
       "I confirm consent" — the dashboard POSTs /api/mcp/consent/authorize with
       the user's JWT and receives a single-use, short-lived ``consent_token``.
    2. The user relays that token into chat; the model passes it as the
       ``consent_token`` tool argument.
    3. ``generic.execute_endpoint`` validates + consumes the token server-side
       and injects consent="Y" into the backend call from the stored grant —
       NEVER from a model-supplied value.

The model can never forge a token: issuance needs the human's JWT + click, and
the token is a 32-byte random nonce validated against ``mcp.consent_tokens``.

Requires table ``mcp.consent_tokens`` (migrations/003_phase3_consent_and_jobs.sql).
Timestamps are naive (``datetime.now()``) to match the existing ``mcp.oauth_codes``
convention and avoid the offset-aware pitfall noted in CLAUDE.md.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .database import Database

logger = logging.getLogger(__name__)

CONSENT_TTL_MINUTES = 15
_DEFAULT_CONSENT_TEXT = "The end user has authorised this identity/KYC lookup (DPDPA)."

# Aadhaar OKYC is a 2-step flow (init -> verify) on two different paths but ONE
# consent decision. A token issued for either OKYC path is valid for both.
_OKYC_PATHS = {"/api/kyc/aadhaar/okyc/init", "/api/kyc/aadhaar/okyc/verify"}


@dataclass
class ConsentGrant:
    capability_path: str
    consent_text: str
    subject_ref: Optional[str]


def subject_ref(identifier: Optional[str]) -> Optional[str]:
    """A privacy-preserving handle for the consented subject (never raw PII)."""
    if not identifier:
        return None
    return hashlib.sha256(str(identifier).strip().lower().encode()).hexdigest()[:16]


def _clean(path: str) -> str:
    return path.split("?", 1)[0].rstrip("/")


def _paths_match(stored: str, want: str) -> bool:
    s, w = _clean(stored), _clean(want)
    if s == w:
        return True
    # OKYC init and verify share one consent scope.
    return s in _OKYC_PATHS and w in _OKYC_PATHS


async def issue_consent_token(
    *, account_id: int, user_email: str, capability_path: str,
    subject_ref: Optional[str] = None, consent_text: Optional[str] = None,
) -> str:
    token = secrets.token_urlsafe(32)
    await Database.execute(
        """INSERT INTO mcp.consent_tokens
             (token, user_account_id, user_email, capability_path, subject_ref,
              consent_text, expires_at, used, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, NOW())""",
        token, account_id, user_email, _clean(capability_path), subject_ref,
        consent_text or _DEFAULT_CONSENT_TEXT,
        datetime.now() + timedelta(minutes=CONSENT_TTL_MINUTES),
    )
    logger.info(f"Issued consent token for account={account_id} path={_clean(capability_path)}")
    return token


async def validate_and_consume_consent_token(
    token: Optional[str], capability_path: str, account_id: Optional[int],
    *, consume: bool = True,
) -> Optional[ConsentGrant]:
    """Return a ConsentGrant iff the token is valid for this path + account, else
    None (caller raises ConsentRequiredError -> refunded). Single-use: consumed
    atomically under FOR UPDATE unless ``consume=False`` (OKYC init keeps it live
    until verify burns it).

    Fail-closed: any DB error (e.g. table not yet migrated) returns None so the
    caller falls back to the legacy/consent-required path — never grants access.
    """
    if not token:
        return None
    try:
        async with Database.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mcp.consent_tokens WHERE token = $1 FOR UPDATE", token
            )
            if row is None or row["used"]:
                return None
            if account_id is not None and row["user_account_id"] != account_id:
                return None
            if not _paths_match(row["capability_path"], capability_path):
                return None
            if datetime.now() > row["expires_at"]:
                return None
            if consume:
                await conn.execute(
                    "UPDATE mcp.consent_tokens SET used = TRUE, used_at = NOW() WHERE token = $1",
                    token,
                )
            return ConsentGrant(
                capability_path=row["capability_path"],
                consent_text=row["consent_text"] or _DEFAULT_CONSENT_TEXT,
                subject_ref=row["subject_ref"],
            )
    except Exception as e:  # table missing / DB down -> fail closed
        logger.warning(f"Consent token validation failed (fail-closed): {e}")
        return None
