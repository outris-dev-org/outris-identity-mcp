"""
smart_lookup — the Tier-2 router for the long tail.

Given a natural-language question + one or more identifiers, it deterministically
types the identifiers, retrieves the best-matching capability from the catalog,
and dispatches through the SAME guarded executor the intent tools use. There is
NO planner LLM here: identifier-typing + catalog retrieval + a couple of coded
sequences cover the real routing space at one backend round-trip.

Safety (inherited from generic.execute_endpoint):
  * money-movement endpoints are never dispatchable (not in the retrieval set);
  * consent-required endpoints raise ConsentRequiredError (refunded, surfaced)
    unless the caller passed consent='Y' — never fabricated here;
  * supplier names are scrubbed and PII masked by default on every result.
"""
import json
import logging
import re
from urllib.parse import quote

from .registry import tool
from .identifiers import type_identifiers
from . import capability_catalog as cat
from .generic import execute_endpoint
from .helpers import scrub_text

logger = logging.getLogger(__name__)

_SEQ_DIRECTOR_HINT = re.compile(r"\b(director|directors|board|owner|owners|who runs|who owns|promoter)\b", re.I)


def _coerce_identifiers(identifiers):
    """Accept a dict, list, JSON string, or plain string."""
    if isinstance(identifiers, str):
        s = identifiers.strip()
        if s and s[0] in "[{":
            try:
                identifiers = json.loads(s)
            except Exception:
                pass
    return type_identifiers(identifiers)


def _find_cin(obj):
    """Best-effort recursive search for a CIN-shaped value under a 'cin' key."""
    cin_re = re.compile(r"^[LUu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and "cin" in str(k).lower() and cin_re.match(v.strip()):
                return v.strip()
        for v in obj.values():
            found = _find_cin(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_cin(v)
            if found:
                return found
    return None


def _disambiguation(typed_ids, message, options=None):
    if options is None:
        options = [
            {"id": c["id"], "summary": scrub_text(c["summary"]), "inputs": c["inputs"],
             "consent_required": c["consent_required"]}
            for c in cat.client_catalog() if not c["moves_money"]
        ]
    return {
        "status": "needs_disambiguation",
        "message": message,
        "detected_identifiers": {k: "provided" for k in typed_ids},  # don't echo raw PII
        "options": options,
    }


async def _company_and_directors(name: str, question: str) -> dict:
    """Coded sequence: resolve a company name to CIN, then fetch its directors."""
    resolve = await execute_endpoint(
        "POST", "/api/orchestrate/company/resolve", body={"company_name": name.strip()}
    )
    out = {"status": "ok", "company": resolve,
           "routing_trace": {"sequence": "company_resolve -> company_directors",
                             "matched_on": "company_name"}}
    cin = _find_cin(resolve)
    if cin:
        out["directors"] = await execute_endpoint(
            "GET", f"/api/kyb/company/cin/{quote(cin, safe='')}/directors"
        )
    else:
        out["note"] = "Company resolved but no CIN was found to fetch directors."
    return out


@tool(
    name="smart_lookup",
    description=(
        "Answer any other identity / KYC / business question when no specific "
        "tool fits. Provide a natural-language `question` plus the "
        "`identifiers` you already have (phone, email, PAN, GSTIN, CIN, DIN, "
        "UAN, IFSC, vehicle RC, UPI VPA, UDIN, or a company name). It figures "
        "out the right lookup — or a short sequence — and returns the answer. "
        "Only pass identifiers the user actually gave you; never invent one. "
        "For consent-required lookups, ask the user to open the consent link in "
        "portal.outris.com/mcp and pass the consent_token they receive.\n\n"
        "Cost: 3 credits"
    ),
    credits=3,
    parameters={
        "question": {
            "type": "string",
            "description": "The user's question in plain language.",
            "required": True,
        },
        "identifiers": {
            "type": "object",
            "description": "Identifiers you have, as {type: value} — e.g. {\"pan\": \"ABCDE1234F\"} "
                           "or {\"phone\": \"9876543210\"}. Only include values the user provided.",
            "required": True,
        },
        "consent_token": {
            "type": "string",
            "description": "Server-issued consent token from portal.outris.com/mcp "
                           "(preferred for consent-required lookups).",
            "required": False,
        },
        "consent": {
            "type": "string",
            "description": "Deprecated legacy consent flag ('Y'); accepted only during "
                           "migration. Prefer consent_token.",
            "required": False,
        },
    },
    category="router",
)
async def smart_lookup(question: str, identifiers=None, consent_token: str = None,
                       consent: str = None) -> dict:
    typed = _coerce_identifiers(identifiers)

    if not typed:
        return _disambiguation(
            typed,
            "I couldn't recognise a usable identifier. Provide one of: phone, "
            "email, PAN, GSTIN, CIN, DIN, UAN, IFSC, vehicle RC, UPI VPA, UDIN, "
            "or a company name.",
        )

    # Coded sequence: company name + a directors/owners question.
    if "company_name" in typed and _SEQ_DIRECTOR_HINT.search(question or ""):
        return await _company_and_directors(typed["company_name"], question)

    candidates = cat.find_candidates(typed, question)
    if not candidates:
        return _disambiguation(
            typed,
            "I recognised the identifier but don't have a lookup wired for that "
            "combination yet. Try a more specific question or a different identifier.",
        )

    top_score, top = candidates[0]
    # Ambiguous: several capabilities tie at the top score.
    tied = [c for s, c in candidates if s == top_score]
    if len(tied) > 1:
        return _disambiguation(
            typed,
            "Your request matches several lookups. Which do you want?",
            options=[{"id": c.id, "summary": scrub_text(c.summary),
                      "inputs": list(c.identifier_types),
                      "consent_required": c.consent} for c in tied],
        )

    method, path, params, body = top.build(typed)
    result = await execute_endpoint(
        method, path, params=params, body=body,
        consent_token=consent_token, consent=consent,
    )
    payload = {
        "status": "ok",
        "routing_trace": {
            "capability": top.id,
            "summary": scrub_text(top.summary),
            "matched_on": [t for t in top.identifier_types],
            "score": top_score,
        },
        "result": result,
    }
    return payload
