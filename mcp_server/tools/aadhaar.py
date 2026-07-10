"""
Aadhaar OKYC — a 2-step, consent-gated tool (Phase 3b).

OKYC is a stateful OTP flow:
  1. aadhaar_okyc_init(aadhaar, consent_token) -> sends an OTP to the Aadhaar-
     linked mobile and returns a transaction reference.
  2. aadhaar_okyc_verify(transaction_id, otp, consent_token) -> verifies the OTP
     and returns the (masked) KYC profile.

Two anti-fabrication guarantees:
  * consent_token — a real, human-issued token (the model can't forge it); the
    OKYC scope covers BOTH init and verify (see core/consent._OKYC_PATHS).
  * OTP — a possession factor the user reads off their phone; it is a normal
    tool ARGUMENT the user pastes, NEVER elicited (the MCP spec forbids eliciting
    sensitive data).

The token is issued single-use but kept live across init (consume_consent=False)
and burned on verify (consume_consent=True). The opaque ``transaction_id`` is
exempted from PII masking via ``control_fields`` (it can be all-digits, which the
mask would otherwise mangle and break the handshake).
"""
import logging

from .registry import tool
from .generic import execute_endpoint

logger = logging.getLogger(__name__)

_OKYC_INIT = "/api/kyc/aadhaar/okyc/init"
_OKYC_VERIFY = "/api/kyc/aadhaar/okyc/verify"
_CONTROL_FIELDS = ("transaction_id", "ref_id", "reference_id", "client_id", "txn_id")


@tool(
    name="aadhaar_okyc_init",
    description=(
        "Start Aadhaar OKYC (offline KYC), step 1 of 2: sends an OTP to the "
        "Aadhaar-linked mobile and returns a transaction reference. Requires a "
        "real human consent_token (ask the user to open the consent link in "
        "portal.outris.com/mcp). Then call aadhaar_okyc_verify with the OTP the "
        "user receives.\n\nCost: 3 credits"
    ),
    credits=3,
    parameters={
        "aadhaar": {"type": "string", "description": "12-digit Aadhaar number.", "required": True},
        "consent_token": {
            "type": "string",
            "description": "Server-issued human consent token for Aadhaar OKYC (not a typed 'Y').",
            "required": True,
        },
    },
    category="kyc",
)
async def aadhaar_okyc_init(aadhaar: str, consent_token: str) -> dict:
    return await execute_endpoint(
        "POST", _OKYC_INIT,
        body={"aadhaar_number": str(aadhaar).strip()},
        consent_token=consent_token,
        consume_consent=False,          # keep the token live until verify
        control_fields=_CONTROL_FIELDS,
    )


@tool(
    name="aadhaar_okyc_verify",
    description=(
        "Complete Aadhaar OKYC, step 2 of 2: verify the OTP the user received and "
        "return the KYC profile (name, DOB, address, photo). Pass the transaction "
        "reference from step 1, the OTP the user reads off their phone, and the "
        "same consent_token.\n\nCost: 0 credits"
    ),
    credits=0,                          # the billable unit was charged on init
    parameters={
        "transaction_id": {"type": "string", "description": "Reference returned by aadhaar_okyc_init.", "required": True},
        "otp": {"type": "string", "description": "OTP the user received on the Aadhaar-linked mobile.", "required": True},
        "consent_token": {"type": "string", "description": "Same human consent token used in init.", "required": True},
    },
    category="kyc",
)
async def aadhaar_okyc_verify(transaction_id: str, otp: str, consent_token: str) -> dict:
    return await execute_endpoint(
        "POST", _OKYC_VERIFY,
        body={"transaction_id": str(transaction_id).strip(), "otp": str(otp).strip()},
        consent_token=consent_token,
        consume_consent=True,           # single-use: burn the token now
        control_fields=_CONTROL_FIELDS,
    )
