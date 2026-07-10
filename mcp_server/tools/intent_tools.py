"""
Tier-1 intent tools — the curated MCP surface (~10 tools).

Design principle: instead of exposing ~100 backend endpoints as flat tools, we
expose a small set of INTENT tools, one per high-value user journey. Each is a
THIN wrapper over an EXISTING number-lookup orchestrator — the multi-source
fan-out already happens server-side, so the MCP never re-sequences at the HTTP
layer. Every call funnels through ``generic.execute_endpoint`` so the
money / consent / supplier-scrub / PII-mask guards apply uniformly.

Backend request shapes are confirmed against number-lookup routes
(traceflow.py, skiptracing.py, fraud_indicator.py, person_screening.py,
collections_intelligence.py, orchestrate.py, bank_intelligence.py, kyb.py) and
the vendor_gateway pan-comprehensive / rc-advance aliases.

Descriptions here are CLIENT-FACING and MUST stay supplier-name free.
"""
import logging

from .registry import tool
from .helpers import normalize_phone
from .generic import execute_endpoint

logger = logging.getLogger(__name__)

# Coarse credit tiers. PHASE-2 / open-question #3: reconcile with the real
# per-category supplier cost; these are a cheap / identity / heavy / screening
# approximation, not final pricing.
C_ID = 2       # single identity lookup
C_HEAVY = 3    # orchestrator fan-out
C_SCREEN = 5   # full due-diligence panel


def _phone_param(desc="Indian mobile number (10-digit, with or without +91)."):
    return {"type": "string", "description": desc, "required": True}


# ===========================================================================
# PHONE
# ===========================================================================
@tool(
    name="investigate_phone",
    description=(
        "Investigate an Indian mobile number and return who is behind it — "
        "name(s), addresses, alternate phone numbers, and social/digital "
        "footprint. Use depth='basic' for a fast identity bundle (default) or "
        "depth='full' for a comprehensive multi-source investigation (slower). "
        "Best for: 'who owns this number', caller ID, KYC, skip-tracing.\n\n"
        "Cost: 3 credits"
    ),
    credits=C_HEAVY,
    parameters={
        "phone": _phone_param(),
        "depth": {
            "type": "string",
            "enum": ["basic", "full"],
            "description": "basic = fast identity bundle (default); full = comprehensive investigation (slower).",
            "required": False,
        },
    },
    category="phone",
)
async def investigate_phone(phone: str, depth: str = "basic") -> dict:
    p = normalize_phone(phone)
    if str(depth).lower() == "full":
        return await execute_endpoint("GET", f"/api/traceflow/{p}")
    return await execute_endpoint("GET", f"/api/skiptracing/phone/{p}")


@tool(
    name="assess_fraud_risk",
    description=(
        "Assess the fraud risk of an Indian mobile number. Returns a composite "
        "risk profile: SIM age / age-on-network, number revocation status, "
        "SIM-swap / port signals, and digital & financial exposure. Set "
        "detailed=true for the full signal breakdown. Best for: onboarding "
        "risk checks, is-this-number-risky.\n\nCost: 3 credits"
    ),
    credits=C_HEAVY,
    parameters={
        "phone": _phone_param(),
        "detailed": {
            "type": "boolean",
            "description": "Include the full raw signal breakdown. Default false.",
            "required": False,
        },
    },
    category="phone",
)
async def assess_fraud_risk(phone: str, detailed: bool = False) -> dict:
    p = normalize_phone(phone)
    return await execute_endpoint(
        "POST", "/api/fraud-indicator", body={"phone": p, "detailed": bool(detailed)}
    )


@tool(
    name="find_contacts",
    description=(
        "Skip-trace an Indian mobile number for the person's ALTERNATE phone "
        "numbers and current, geocoded addresses. Requires the end user's "
        "consent (set consent='Y'). Best for: debt collection, locating a "
        "person. Do not fabricate consent — ask the user first.\n\n"
        "Cost: 3 credits"
    ),
    credits=C_HEAVY,
    parameters={
        "phone": _phone_param(),
        "consent": {
            "type": "string",
            "description": "Must be 'Y'. Confirms the end user consented to this lookup (DPDPA).",
            "required": True,
        },
        "consent_text": {
            "type": "string",
            "description": "Optional free-text consent declaration provided by the user.",
            "required": False,
        },
    },
    category="phone",
)
async def find_contacts(phone: str, consent: str, consent_text: str = None) -> dict:
    p = normalize_phone(phone)
    body = {"phone": p, "consent": consent}
    if consent_text:
        body["consent_text"] = consent_text
    return await execute_endpoint(
        "POST", "/api/collections/phone", body=body, consent=consent
    )


@tool(
    name="due_diligence_person",
    description=(
        "Run a full due-diligence / background check on a PERSON, anchored on "
        "their mobile number (optionally add name, PAN, DOB, email, city for "
        "accuracy). Covers PEP, sanctions, enforcement actions, cybercrime "
        "reports, breaches, company directorships, and adverse media. Requires "
        "consent (set consent='Y'). NOTE: this is a premium panel and can take "
        "40-70 seconds. Best for: KYC/AML, compliance onboarding, vendor "
        "screening.\n\nCost: 5 credits"
    ),
    credits=C_SCREEN,
    parameters={
        "phone": _phone_param(),
        "consent": {
            "type": "string",
            "description": "Must be 'Y'. DPDPA subject-consent attestation. Do not fabricate.",
            "required": True,
        },
        "name": {"type": "string", "description": "Optional subject full name.", "required": False},
        "pan": {"type": "string", "description": "Optional PAN — strongest key for PEP/enforcement.", "required": False},
        "dob": {"type": "string", "description": "Optional date of birth (disambiguates common names).", "required": False},
        "email": {"type": "string", "description": "Optional subject email.", "required": False},
        "city": {"type": "string", "description": "Optional city (adverse-media disambiguation).", "required": False},
    },
    category="screening",
)
async def due_diligence_person(
    phone: str, consent: str, name: str = None, pan: str = None,
    dob: str = None, email: str = None, city: str = None,
) -> dict:
    subject = {"phone": normalize_phone(phone)}
    for k, v in (("name", name), ("pan", pan), ("dob", dob), ("email", email), ("city", city)):
        if v:
            subject[k] = v
    return await execute_endpoint(
        "POST", "/api/screening/person",
        body={"subject": subject, "consent": consent}, consent=consent,
    )


# ===========================================================================
# EMAIL
# ===========================================================================
@tool(
    name="investigate_email",
    description=(
        "Trace the person behind an email address — linked names, phone "
        "numbers, addresses, and known data breaches. Best for: reverse email "
        "lookup, digital footprint, cross-referencing an identity.\n\n"
        "Cost: 2 credits"
    ),
    credits=C_ID,
    parameters={
        "email": {"type": "string", "description": "Email address to investigate.", "required": True},
    },
    category="email",
)
async def investigate_email(email: str) -> dict:
    return await execute_endpoint("GET", f"/api/skiptracing/email/{email.strip()}")


# ===========================================================================
# BUSINESS / KYB
# ===========================================================================
@tool(
    name="resolve_company",
    description=(
        "Resolve an Indian company from its NAME and return its CIN "
        "(Corporate Identification Number) plus any GSTIN / MSME registrations "
        "discovered along the way. Best for: 'is this a real company', KYB "
        "entry point, finding a CIN from a company name.\n\nCost: 3 credits"
    ),
    credits=C_HEAVY,
    parameters={
        "company_name": {"type": "string", "description": "Company name to resolve.", "required": True},
    },
    category="kyb",
)
async def resolve_company(company_name: str) -> dict:
    return await execute_endpoint(
        "POST", "/api/orchestrate/company/resolve", body={"company_name": company_name.strip()}
    )


@tool(
    name="lookup_gst",
    description=(
        "Look up GST registration details for a business by its GSTIN "
        "(15-character GST number). Returns legal/trade name, status, "
        "registration type, and address. Best for: GST verification, vendor "
        "onboarding.\n\nCost: 2 credits"
    ),
    credits=C_ID,
    parameters={
        "gstin": {"type": "string", "description": "15-character GSTIN.", "required": True},
    },
    category="kyb",
)
async def lookup_gst(gstin: str) -> dict:
    return await execute_endpoint("GET", f"/api/kyb/gst/{gstin.strip().upper()}")


# ===========================================================================
# KYC — PAN / VEHICLE / BANK
# ===========================================================================
@tool(
    name="verify_pan",
    description=(
        "Verify an Indian PAN (Permanent Account Number) and return the "
        "holder's name, status, and PAN type (individual / company / etc.). "
        "Best for: KYC, PAN validity checks.\n\nCost: 2 credits"
    ),
    credits=C_ID,
    parameters={
        "pan": {"type": "string", "description": "10-character PAN (e.g. ABCDE1234F).", "required": True},
    },
    category="kyc",
)
async def verify_pan(pan: str) -> dict:
    return await execute_endpoint(
        "POST", "/api/kyc/pan/comprehensive", body={"pan": pan.strip().upper()}
    )


@tool(
    name="lookup_vehicle",
    description=(
        "Look up an Indian vehicle by its RC (registration) number — returns "
        "make/model, registration details, and the registered owner. Best for: "
        "vehicle verification, RC checks.\n\nCost: 2 credits"
    ),
    credits=C_ID,
    parameters={
        "rc_number": {"type": "string", "description": "Vehicle registration number (e.g. MH12AB1234).", "required": True},
    },
    category="kyc",
)
async def lookup_vehicle(rc_number: str) -> dict:
    return await execute_endpoint(
        "POST", "/api/vehicle/rc-advance", body={"rc_number": rc_number.strip().upper()}
    )


@tool(
    name="verify_bank_account",
    description=(
        "Validate an Indian bank account WITHOUT moving any money (no-debit "
        "NPCI validation) and return whether it is valid plus the account "
        "holder's name for matching. Best for: payout/beneficiary "
        "verification. This does NOT transfer funds.\n\nCost: 2 credits"
    ),
    credits=C_ID,
    parameters={
        "account_number": {"type": "string", "description": "Bank account number to validate.", "required": True},
        "ifsc": {"type": "string", "description": "IFSC code of the account's branch.", "required": True},
    },
    category="financial",
)
async def verify_bank_account(account_number: str, ifsc: str) -> dict:
    return await execute_endpoint(
        "POST", "/api/kyc/bank/pennyless",
        body={"account_number": account_number.strip(), "ifsc": ifsc.strip().upper()},
    )
