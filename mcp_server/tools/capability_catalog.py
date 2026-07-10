"""
Capability catalog — the MCP's single source of truth for the long-tail surface
AND for the money / consent guard flags.

Why this file exists (Phase 2, addressing the review's must-fix #1): the money
and consent classification USED to live as hardcoded sets in generic.py, a
second source of truth that drifts from the backend. It is now consolidated
here, next to the endpoint definitions, so ``generic.execute_endpoint`` derives
its money/consent gates from the SAME place ``smart_lookup`` routes over.

Each entry is authored to be CLIENT-SAFE (no supplier names in ``summary``).

PHASE-2/3 FOLLOW-UP (documented, not yet done): the ideal source of truth is
number-lookup's ``infrastructure/endpoint_catalog.py`` (which already carries
``consent_required`` and could carry a ``money_movement`` flag). This catalog
should eventually be GENERATED from a client-safe projection of that file
(fetched at runtime) rather than hand-maintained — until then, keep the
money/consent flags here in lockstep with the backend.

Coverage: this seeds the highest-confidence long-tail endpoints (confirmed
request shapes). It is intentionally a plain data table — extend it by adding
``Capability(...)`` rows. Endpoints not yet listed are simply not reachable via
smart_lookup yet (they still have their Tier-1 tool if one exists).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote


@dataclass(frozen=True)
class Capability:
    id: str
    summary: str                              # client-safe, supplier-free
    identifier_types: Tuple[str, ...]         # inputs required (typed)
    method: str
    path: str                                 # template; {type} placeholders filled from ids
    keywords: Tuple[str, ...] = ()
    consent: bool = False
    money: bool = False
    beta: bool = False
    body_map: Tuple[Tuple[str, str], ...] = ()   # (body_field, identifier_type)
    static_body: Optional[dict] = None
    dispatchable: bool = True                  # False = present only for the guard SSOT

    def build(self, ids: Dict[str, str]):
        """Return (method, path, params, body) for execute_endpoint.

        Consent is NOT injected here (Phase 3): execute_endpoint server-injects
        it from a validated consent token, never from a model-supplied value.
        """
        path = self.path
        for t, v in ids.items():
            path = path.replace("{%s}" % t, quote(str(v).strip(), safe=""))
        body = None
        if self.method.upper() == "POST":
            body = dict(self.static_body or {})
            for field_name, t in self.body_map:
                if t in ids:
                    body[field_name] = ids[t].strip()
        return self.method, path, None, body


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
CAPABILITIES: List[Capability] = [
    # ---- MONEY MOVEMENT (guard SSOT only — NEVER dispatchable / retrievable) ----
    Capability("penny_drop", "Real ₹1 bank penny-drop (moves money)", ("ifsc",),
               "POST", "/api/kyc/bank/penny-drop", money=True, dispatchable=False),
    Capability("reverse_penny", "Payer-initiated penny credit (moves money)", (),
               "POST", "/api/kyc/bank/reverse-penny", money=True, dispatchable=False),

    # ---- CONSENT-GATED Tier-1 paths (guard SSOT; already have intent tools) ----
    Capability("collections_phone", "Skip-trace alt phones + addresses", ("phone",),
               "POST", "/api/collections/phone", consent=True, dispatchable=False),
    Capability("screening_person", "Full person due-diligence panel", ("phone",),
               "POST", "/api/screening/person", consent=True, beta=True, dispatchable=False),
    Capability("aadhaar_okyc_init", "Aadhaar OKYC step 1 (OTP send)", (),
               "POST", "/api/kyc/aadhaar/okyc/init", consent=True, dispatchable=False),
    Capability("aadhaar_okyc_verify", "Aadhaar OKYC step 2 (OTP verify)", (),
               "POST", "/api/kyc/aadhaar/okyc/verify", consent=True, dispatchable=False),

    # ---- PHONE-keyed ----
    Capability("upi_from_phone", "Find UPI / VPA payment handles linked to a mobile number",
               ("phone",), "POST", "/api/kyc/upi/from-phone",
               keywords=("upi", "vpa", "payment", "handle", "gpay", "phonepe"),
               body_map=(("phone", "phone"),)),
    Capability("mobile_to_uan", "Find the EPFO UAN (provident-fund account) for a mobile number",
               ("phone",), "POST", "/api/kyc/mobile-to-uan",
               keywords=("uan", "epfo", "pf", "provident", "employment"),
               body_map=(("mobile", "phone"),)),
    Capability("telco_hlr", "Telco / HLR status of a number — operator, roaming, active/valid",
               ("phone",), "POST", "/api/hlr/lookup",
               keywords=("hlr", "telco", "operator", "roaming", "network", "active", "valid"),
               body_map=(("msisdn", "phone"),)),
    Capability("whatsapp_presence", "Check WhatsApp presence / profile for a mobile number",
               ("phone",), "GET", "/api/whatsapp/{phone}",
               keywords=("whatsapp", "wa", "messenger")),
    Capability("mobile_to_pan", "Find the PAN linked to a mobile number",
               ("phone",), "POST", "/api/kyc/mobile-to-pan", consent=True,
               keywords=("pan", "mobile to pan", "tax"),
               body_map=(("mobile", "phone"),)),

    # ---- PAN-keyed ----
    Capability("pan_to_mobile", "Find the masked mobile / email linked to a PAN",
               ("pan",), "POST", "/api/kyc/pan-to-mobile", consent=True,
               keywords=("mobile", "email", "contact", "pan to mobile"),
               body_map=(("pan", "pan"),)),
    Capability("gst_from_pan", "Find GST registrations (GSTINs) under a PAN",
               ("pan",), "GET", "/api/kyb/gst/from-pan/{pan}",
               keywords=("gst", "gstin", "registration", "business")),
    Capability("director_by_pan", "Find director (DIN) and directorships for a PAN",
               ("pan",), "GET", "/api/kyb/director/pan/{pan}",
               keywords=("director", "din", "board", "company", "directorship")),
    Capability("irdai_agent", "Verify an IRDAI insurance agent by PAN",
               ("pan",), "GET", "/api/kyb/irdai/agent/{pan}",
               keywords=("irdai", "insurance", "agent")),

    # ---- VPA-keyed ----
    Capability("vpa_analysis", "Analyse a UPI VPA handle — validity and linked name",
               ("vpa",), "POST", "/api/kyc/upi/vpa-analysis",
               keywords=("upi", "vpa", "handle", "analyse", "analyze"),
               body_map=(("vpa", "vpa"),)),

    # ---- UAN-keyed ----
    Capability("uan_history", "Employment timeline for a UAN (provident-fund history)",
               ("uan",), "POST", "/api/kyc/uan-history", consent=True,
               keywords=("employment", "uan", "history", "epfo", "jobs", "work"),
               body_map=(("uan", "uan"),)),

    # ---- DIN-keyed ----
    Capability("director_by_din", "Directorships for a DIN (director identification number)",
               ("din",), "GET", "/api/kyb/director/{din}",
               keywords=("director", "din", "board", "companies", "directorship")),

    # ---- CIN-keyed ----
    Capability("company_by_cin", "Company profile for a CIN",
               ("cin",), "GET", "/api/kyb/company/cin/{cin}",
               keywords=("company", "cin", "profile", "roc")),
    Capability("company_directors", "Directors / board of a company by CIN",
               ("cin",), "GET", "/api/kyb/company/cin/{cin}/directors",
               keywords=("directors", "board", "owners", "who runs")),

    # ---- RC-keyed ----
    Capability("rc_to_mobile", "Find the registered owner's mobile for a vehicle RC",
               ("rc",), "POST", "/api/vehicle/rc-to-mobile", consent=True,
               keywords=("owner", "mobile", "phone", "vehicle", "rc"),
               body_map=(("rc_number", "rc"),)),

    # ---- IFSC-keyed ----
    Capability("ifsc_lookup", "Bank and branch details for an IFSC code",
               ("ifsc",), "GET", "/api/kyc/bank/ifsc/{ifsc}",
               keywords=("ifsc", "bank", "branch")),

    # ---- UDIN-keyed ----
    Capability("udin_verify", "Verify a UDIN (CA document unique number)",
               ("udin",), "GET", "/api/kyb/udin/{udin}",
               keywords=("udin", "ca", "verify", "document")),

    # ---- COMPANY-NAME-keyed ----
    Capability("company_resolve", "Resolve a company from its name to CIN / GSTIN / MSME",
               ("company_name",), "POST", "/api/orchestrate/company/resolve",
               keywords=("company", "resolve", "cin", "gstin", "legit", "registered"),
               body_map=(("company_name", "company_name"),)),
]

_BY_ID: Dict[str, Capability] = {c.id: c for c in CAPABILITIES}
_MONEY_PATHS = {c.path for c in CAPABILITIES if c.money}
_CONSENT_PATHS = {c.path for c in CAPABILITIES if c.consent}


def _clean(path: str) -> str:
    return path.split("?", 1)[0].rstrip("/")


def is_money_movement_path(path: str) -> bool:
    return _clean(path) in {_clean(p) for p in _MONEY_PATHS}


def requires_consent(path: str) -> bool:
    return _clean(path) in {_clean(p) for p in _CONSENT_PATHS}


def get(cap_id: str) -> Optional[Capability]:
    return _BY_ID.get(cap_id)


def find_candidates(typed_ids: Dict[str, str], question: str) -> List[Tuple[int, Capability]]:
    """Score dispatchable capabilities against the typed identifiers + question.

    Rules:
      * a capability is a candidate only if ALL its identifier_types are present
        (so we never call an endpoint we lack an input for);
      * score = 10 per matching identifier type + 1 per keyword overlap with the
        question. Ties are returned in catalog order (stable).
    """
    q = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z]+", q))
    scored: List[Tuple[int, Capability]] = []
    for cap in CAPABILITIES:
        if not cap.dispatchable or cap.money:
            continue
        if not cap.identifier_types:
            continue
        if not all(t in typed_ids for t in cap.identifier_types):
            continue
        score = 10 * len(cap.identifier_types)
        score += sum(1 for kw in cap.keywords if kw in q or set(kw.split()) & q_tokens)
        scored.append((score, cap))
    scored.sort(key=lambda sc: sc[0], reverse=True)
    return scored


def client_catalog() -> List[dict]:
    """Client-safe projection for the ``list_capabilities`` resource."""
    out = []
    for c in CAPABILITIES:
        if not c.dispatchable and not c.money:
            # consent Tier-1 SSOT rows are surfaced via their intent tools already
            continue
        out.append({
            "id": c.id,
            "summary": c.summary,
            "inputs": list(c.identifier_types),
            "consent_required": c.consent,
            "premium": c.beta,
            "moves_money": c.money,
            "note": ("Not available via the assistant — must be initiated by a human."
                     if c.money else None),
        })
    return out
