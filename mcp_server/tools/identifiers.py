"""
Deterministic identifier typing for the smart_lookup router.

We classify a raw value into an identifier type by SHAPE (regex/format) — no LLM,
no guessing. This is the first stage of routing: most long-tail endpoints are
keyed by exactly one identifier type, so typing alone resolves most ambiguity.
"""
import re
from typing import Dict, List, Optional

# Order matters: most-specific shapes first.
_PAN_RE = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
_GSTIN_RE = re.compile(r"^\d{2}[A-Za-z]{5}\d{4}[A-Za-z]\d[A-Za-z\d]{2}$")
_CIN_RE = re.compile(r"^[LUu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
_IFSC_RE = re.compile(r"^[A-Za-z]{4}0[A-Za-z0-9]{6}$")
_RC_RE = re.compile(r"^[A-Za-z]{2}\d{1,2}[A-Za-z]{1,3}\d{1,4}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VPA_RE = re.compile(r"^[\w.\-]{2,}@[a-zA-Z]{2,}$")  # @ but no dot in the handle domain
_UDIN_RE = re.compile(r"^\d{18}$")
_UAN_RE = re.compile(r"^\d{12}$")
_DIN_RE = re.compile(r"^\d{8}$")

# The identifier types smart_lookup understands.
KNOWN_TYPES = (
    "phone", "email", "pan", "gstin", "cin", "din", "uan", "ifsc", "rc",
    "vpa", "udin", "company_name",
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _is_phone(value: str) -> bool:
    d = _digits(value)
    if len(d) == 10 and d[0] in "6789":
        return True
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return True
    return False


def classify(value: str) -> Optional[str]:
    """Return the single best identifier type for a raw value, or None.

    A value that matches nothing but looks like free text (has a letter and a
    space, or is long) is treated as ``company_name``.
    """
    if not value or not str(value).strip():
        return None
    v = str(value).strip()

    if _EMAIL_RE.match(v):
        return "email"
    if _IFSC_RE.match(v):
        return "ifsc"
    if _GSTIN_RE.match(v):
        return "gstin"
    if _CIN_RE.match(v):
        return "cin"
    if _PAN_RE.match(v):
        return "pan"
    # Phone (incl. 91-prefixed) BEFORE the bare 12-digit UAN rule.
    if _is_phone(v):
        return "phone"
    if _VPA_RE.match(v):
        return "vpa"
    if _RC_RE.match(v.replace(" ", "")):
        return "rc"
    if _UDIN_RE.match(v):
        return "udin"
    if _UAN_RE.match(v):
        return "uan"
    if _DIN_RE.match(v):
        return "din"
    # Free text with letters -> company name.
    if any(c.isalpha() for c in v) and (" " in v or len(v) >= 4):
        return "company_name"
    return None


def type_identifiers(identifiers) -> Dict[str, str]:
    """Normalise the caller-supplied ``identifiers`` into a {type: value} map.

    Accepts:
      * a dict {type: value} — we TRUST the caller's key but re-validate the
        value shape; a mismatch is re-classified so a mislabelled value still
        routes correctly (and never routes to the wrong money/consent endpoint).
      * a list/tuple of raw values — each is classified.
      * a single string — classified.
    Values that classify to nothing are dropped.
    """
    out: Dict[str, str] = {}

    def _add(t: Optional[str], val: str):
        if t and val and t not in out:
            out[t] = str(val).strip()

    if isinstance(identifiers, dict):
        for k, val in identifiers.items():
            if val in (None, ""):
                continue
            k_norm = str(k).strip().lower()
            detected = classify(str(val))
            # Trust an explicit known key unless the value clearly is another
            # concrete identifier type (protects against mislabelling).
            if detected and detected != k_norm and detected != "company_name":
                _add(detected, str(val))
            elif k_norm in KNOWN_TYPES:
                _add(k_norm, str(val))
            else:
                _add(detected, str(val))
    elif isinstance(identifiers, (list, tuple)):
        for val in identifiers:
            _add(classify(str(val)), str(val))
    elif isinstance(identifiers, str):
        _add(classify(identifiers), identifiers)

    return out
