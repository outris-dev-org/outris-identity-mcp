"""
Phase-0/1 safety unit tests (no network, no DB).

Run: python test_scrub_mask_guards.py

Covers:
  * scrub_supplier_names — no banned vendor name survives, nested + code aliases
  * mask_pii — default-deny value-shape masking; allow_raw passthrough; KYB safe
  * classify_tool_error — refund iff 5xx/timeout/preflight; never leaks str(e)
  * execute_endpoint — money gate + consent gate fire BEFORE any backend call
"""
import asyncio

from mcp_server.tools.helpers import (
    scrub_supplier_names, scrub_text, mask_pii, classify_tool_error,
    BackendError, ConsentRequiredError, MoneyPathBlockedError,
    PreflightRejection, _SUPPLIER_NAMES,
)
from mcp_server.tools import generic
from mcp_server.core.context import current_account
from mcp_server.core.auth import MCPAccount

FAIL = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# ---------------------------------------------------------------------------
print("\n[1] scrub_supplier_names")
payload = {
    "provider": "s3",
    "note": "Data sourced from EnrichData and Gridlines",
    "error": "Sign3 returned HTTP 500",
    "nested": [{"vendor": "s8"}, {"desc": "via CrimeScan pipeline"}],
    "safe": "customer name Rahul",
}
scrubbed = scrub_supplier_names(payload)
blob = str(scrubbed).lower()
leaks = [n for n in _SUPPLIER_NAMES if n in blob]
check(not leaks, f"no banned vendor name survives (leaks={leaks})")
check(scrubbed["provider"] == "outris", "supplier-code value 's3' under supplier key -> outris")
check(scrubbed["nested"][0]["vendor"] == "outris", "nested supplier-code value scrubbed")
check("supplier" in scrubbed["error"].lower() and "500" in scrubbed["error"], "error keeps HTTP code, drops vendor name")
check(scrubbed["safe"] == "customer name Rahul", "non-vendor text untouched")
check("sign3" not in scrub_text("Sign3 and hibp leaked").lower(), "scrub_text handles plain strings")


# ---------------------------------------------------------------------------
print("\n[2] mask_pii — default-deny (allow_raw=False)")
raw = {
    "full_name": "Rahul Sharma",
    "pan": "ABCDE1234F",
    "email": "rahul.sharma@gmail.com",
    "mobile": "9876543210",
    "aadhaar": "1234 5678 9012",
    "address": "12 MG Road, Bengaluru 560001",
    "company_name": "ABC Cotspin Private Limited",
    "status": "VALID",
    "risk_score": 82,
    "is_active": True,
    "alt_phones": ["9812345678", "9998887776"],
}
masked = mask_pii(raw, allow_raw=False)
check(masked["pan"] != "ABCDE1234F" and "*" in masked["pan"], "PAN masked by value shape")
check("*" in masked["email"] and masked["email"].endswith("@gmail.com"), "email masked, domain kept")
check("*" in masked["mobile"], "phone masked by value shape")
check("*" in masked["aadhaar"], "aadhaar masked by value shape")
check("*" in masked["full_name"], "name masked by key")
check("*" in masked["address"], "address masked by key")
check(masked["company_name"] == "ABC Cotspin Private Limited", "company name NOT masked (KYB stays useful)")
check(masked["status"] == "VALID", "status string untouched")
check(masked["risk_score"] == 82 and masked["is_active"] is True, "numbers/bools untouched")
check(all("*" in p for p in masked["alt_phones"]), "phones inside a list masked")

print("[2b] mask_pii — allow_raw=True passthrough")
passthru = mask_pii(raw, allow_raw=True)
check(passthru["pan"] == "ABCDE1234F" and passthru["full_name"] == "Rahul Sharma", "allow_raw returns data unchanged")


# ---------------------------------------------------------------------------
print("\n[3] classify_tool_error")
refund, code, msg = classify_tool_error(BackendError(502, "Upstream service returned HTTP 502."))
check(refund and code == "backend_error", "5xx -> refund")
refund, code, msg = classify_tool_error(BackendError(504, "timeout", is_timeout=True))
check(refund, "timeout -> refund")
refund, code, msg = classify_tool_error(BackendError(422, "Upstream service returned HTTP 422."))
check(not refund and code == "upstream_rejected", "4xx -> charged (caller input)")
refund, code, msg = classify_tool_error(ConsentRequiredError("need consent"))
check(refund and code == "consent_required", "consent rejection -> refund (no work done)")
refund, code, msg = classify_tool_error(ValueError("secret EnrichData token leaked in str(e)"))
check(not refund and "enrichdata" not in msg.lower(), "unknown error -> charged, message does not leak internals")


# ---------------------------------------------------------------------------
print("\n[4] execute_endpoint gates (raise BEFORE any network call)")


async def _gates():
    tok = current_account.set(MCPAccount(
        id=1, user_email="t@x.com", display_name=None, credits_balance=100,
        credits_tier="free", is_active=True, allow_raw_records=False,
    ))
    try:
        # money gate
        try:
            await generic.execute_endpoint("POST", "/api/kyc/bank/penny-drop", body={"account_number": "1", "ifsc": "X"})
            check(False, "money path should be blocked")
        except MoneyPathBlockedError:
            check(True, "penny-drop blocked by money gate")
        # reverse-penny blocked too
        try:
            await generic.execute_endpoint("POST", "/api/kyc/bank/reverse-penny", body={})
            check(False, "reverse-penny should be blocked")
        except MoneyPathBlockedError:
            check(True, "reverse-penny blocked by money gate")
        # pennyless is NOT money movement (must NOT raise the money gate)
        check(not generic.is_money_movement_path("/api/kyc/bank/pennyless"), "pennyless is money-SAFE (not gated)")
        # consent gate: missing consent
        try:
            await generic.execute_endpoint("POST", "/api/collections/phone", body={"phone": "9"}, consent=None)
            check(False, "collections should require consent")
        except ConsentRequiredError:
            check(True, "collections/phone requires consent when missing")
        # consent gate: fabricated non-Y value rejected
        try:
            await generic.execute_endpoint("POST", "/api/screening/person", body={}, consent="maybe")
            check(False, "non-'Y' consent should be rejected")
        except ConsentRequiredError:
            check(True, "non-'Y' consent rejected")
    finally:
        current_account.reset(tok)


asyncio.run(_gates())
check(issubclass(ConsentRequiredError, PreflightRejection) and issubclass(MoneyPathBlockedError, PreflightRejection),
      "consent + money errors are refundable PreflightRejections")


# ---------------------------------------------------------------------------
print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
