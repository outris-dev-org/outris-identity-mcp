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
import asyncio
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
        "consent: ask the user to open the consent link in portal.outris.com/mcp "
        "and pass the consent_token they receive. Best for: debt collection, "
        "locating a person. Never fabricate consent.\n\nCost: 3 credits"
    ),
    credits=C_HEAVY,
    parameters={
        "phone": _phone_param(),
        "consent_token": {
            "type": "string",
            "description": "Server-issued consent token from portal.outris.com/mcp (preferred).",
            "required": False,
        },
        "consent": {
            "type": "string",
            "description": "Deprecated legacy consent flag ('Y'); accepted only during migration.",
            "required": False,
        },
    },
    category="phone",
)
async def find_contacts(phone: str, consent_token: str = None, consent: str = None) -> dict:
    return await execute_endpoint(
        "POST", "/api/collections/phone", body={"phone": normalize_phone(phone)},
        consent_token=consent_token, consent=consent,
    )


async def _run_dd_job(job_id, subject, consent_token, consent, cr_id):
    """Background worker for the async due-diligence panel. Runs the (40-70s)
    backend call, persists the result, and refunds the ORIGINAL credit charge on
    a backend failure via the same credit_request_id."""
    from ..core import jobs
    from ..core.credits import record_tool_result
    from .helpers import BackendError
    try:
        result = await execute_endpoint(
            "POST", "/api/screening/person", body={"subject": subject},
            consent_token=consent_token, consent=consent, consume_consent=True,
        )
        await jobs.mark_complete(job_id, result)
    except BackendError as e:
        should_refund = e.status_code >= 500 or e.is_timeout
        await jobs.mark_failed(
            job_id, "backend_error" if should_refund else "upstream_rejected", e.client_message)
        if should_refund and cr_id:
            try:
                await record_tool_result(
                    request_id=cr_id, success=False, error_code="backend_error",
                    error_message=e.client_message, is_backend_error=True)
            except Exception as re:
                logger.error(f"async refund failed for job {job_id}: {re}")
    except Exception as e:
        await jobs.mark_failed(job_id, "execution_error", str(e)[:200])


@tool(
    name="due_diligence_person_start",
    description=(
        "Start a full due-diligence / background check on a PERSON, anchored on "
        "their mobile number (optionally add name, PAN, DOB, email, city). Covers "
        "PEP, sanctions, enforcement, cybercrime, breaches, directorships, and "
        "adverse media. PREMIUM and ASYNC (~40-70s): it returns a job_id — poll "
        "check_job until status is 'complete'. Requires consent: ask the user to "
        "open the consent link in portal.outris.com/mcp and pass the "
        "consent_token.\n\nCost: 5 credits"
    ),
    credits=C_SCREEN,
    parameters={
        "phone": _phone_param(),
        "consent_token": {"type": "string", "description": "Server-issued consent token (preferred).", "required": False},
        "consent": {"type": "string", "description": "Deprecated legacy consent flag ('Y'); migration only.", "required": False},
        "name": {"type": "string", "description": "Optional subject full name.", "required": False},
        "pan": {"type": "string", "description": "Optional PAN — strongest key for PEP/enforcement.", "required": False},
        "dob": {"type": "string", "description": "Optional date of birth (disambiguates common names).", "required": False},
        "email": {"type": "string", "description": "Optional subject email.", "required": False},
        "city": {"type": "string", "description": "Optional city (adverse-media disambiguation).", "required": False},
    },
    category="screening",
)
async def due_diligence_person_start(
    phone: str, consent_token: str = None, consent: str = None, name: str = None,
    pan: str = None, dob: str = None, email: str = None, city: str = None,
) -> dict:
    from ..core.consent import validate_and_consume_consent_token
    from ..core.context import current_account, current_credit_request_id
    from ..core.config import get_settings
    from ..core import jobs
    from .helpers import ConsentRequiredError

    acct = current_account.get()
    # Fail-fast consent check (do NOT consume — the background call consumes).
    grant = await validate_and_consume_consent_token(
        consent_token, "/api/screening/person", acct.id if acct else None, consume=False)
    legacy_ok = (get_settings().allow_legacy_consent_y
                 and isinstance(consent, str) and consent.strip().upper() == "Y")
    if grant is None and not legacy_ok:
        raise ConsentRequiredError(
            "This screening needs the end user's real consent. Ask the user to open the "
            "consent link in portal.outris.com/mcp and pass the consent_token they receive.")

    subject = {"phone": normalize_phone(phone)}
    for k, v in (("name", name), ("pan", pan), ("dob", dob), ("email", email), ("city", city)):
        if v:
            subject[k] = v

    cr_id = current_credit_request_id.get()
    try:
        job_id = await jobs.create_job(
            account_id=acct.id if acct else None, tool_name="due_diligence_person",
            capability_path="/api/screening/person",
            input_summary={"keys": list(subject.keys())}, credits_request_id=cr_id)
    except Exception:
        # Sync fallback (pre-migration / job store unavailable): run inline.
        result = await execute_endpoint(
            "POST", "/api/screening/person", body={"subject": subject},
            consent_token=consent_token, consent=consent, consume_consent=True)
        return {"status": "complete", "result": result}

    asyncio.create_task(_run_dd_job(job_id, subject, consent_token, consent, cr_id))
    return {"status": "running", "job_id": str(job_id), "poll_with": "check_job", "eta_seconds": "40-70"}


@tool(
    name="check_job",
    description=(
        "Check the status/result of an async job by job_id (e.g. from "
        "due_diligence_person_start). Free to poll — wait ~10s between polls.\n\n"
        "Cost: 0 credits"
    ),
    credits=0,
    parameters={
        "job_id": {"type": "string", "description": "The job_id returned by an async tool.", "required": True},
    },
    category="jobs",
)
async def check_job(job_id: str) -> dict:
    from ..core.context import current_account
    from ..core import jobs
    acct = current_account.get()
    row = await jobs.get_job(acct.id if acct else None, job_id)
    if row is None:
        return {"status": "not_found", "job_id": job_id}
    status = row.get("status")
    if status == "running":
        return {"status": "running", "job_id": job_id, "hint": "poll again in ~10s"}
    if status == "failed":
        return {"status": "failed", "job_id": job_id, "error": row.get("error_code")}
    result = row.get("result")
    if isinstance(result, str):
        import json as _json
        try:
            result = _json.loads(result)
        except Exception:
            pass
    return {"status": "complete", "job_id": job_id, "result": result}


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


# Registering the Tier-2 router (smart_lookup) alongside the Tier-1 tools — the
# 3 server entrypoints import intent_tools, so this brings it with them.
from . import smart_lookup as _smart_lookup  # noqa: E402,F401

# OTP-based Aadhaar OKYC tools are PARKED for now (owner: keep the surface simple,
# no OTP APIs yet). The module (tools/aadhaar.py) + its test stay on disk; just
# uncomment this import to re-register aadhaar_okyc_init / aadhaar_okyc_verify.
# from . import aadhaar as _aadhaar          # noqa: E402,F401
