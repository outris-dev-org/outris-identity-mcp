"""
Phase-2 tests: identifier typing + smart_lookup routing (offline, no network/DB).

Run: python test_smart_lookup.py

call_backend is stubbed so execute_endpoint runs its full guard chain (money /
consent / scrub / mask) without hitting the backend.
"""
import asyncio

import mcp_server.tools.generic as generic
from mcp_server.tools import smart_lookup as sl
from mcp_server.tools.identifiers import classify, type_identifiers
from mcp_server.tools.helpers import ConsentRequiredError
from mcp_server.core.context import current_account
from mcp_server.core.auth import MCPAccount

FAIL = []
CALLS = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


async def _fake_call_backend(endpoint, method="GET", params=None, json_data=None, api_key=None):
    """Record the call and return a canned, PII-bearing payload."""
    CALLS.append({"endpoint": endpoint, "method": method, "body": json_data})
    if "orchestrate/company/resolve" in endpoint:
        return {"cin": "L12345MH2020PTC123456", "company_name": "ABC Cotspin Private Limited"}
    if "/directors" in endpoint:
        return {"directors": [{"name": "Rahul Sharma", "din": "01234567"}]}
    return {"echo": endpoint, "name": "Rahul Sharma", "pan": "ABCDE1234F", "status": "ok"}


generic.call_backend = _fake_call_backend  # monkeypatch the executor's backend call


# ---------------------------------------------------------------------------
print("\n[1] identifier typing")
cases = {
    "ABCDE1234F": "pan",
    "27ABCDE1234F1Z5": "gstin",
    "L12345MH2020PTC123456": "cin",
    "HDFC0001234": "ifsc",
    "9876543210": "phone",
    "919876543210": "phone",
    "rahul@gmail.com": "email",
    "rahul@okhdfcbank": "vpa",
    "MH12AB1234": "rc",
    "100234567890": "uan",       # 12-digit non-phone
    "01234567": "din",           # 8-digit
    "Reliance Industries Ltd": "company_name",
}
for val, exp in cases.items():
    got = classify(val)
    check(got == exp, f"{val!r} -> {got} (expected {exp})")

td = type_identifiers({"pan": "ABCDE1234F", "phone": "9876543210"})
check(td == {"pan": "ABCDE1234F", "phone": "9876543210"}, "type_identifiers keeps well-formed dict")
td2 = type_identifiers({"phone": "ABCDE1234F"})  # mislabelled: value is really a PAN
check(td2.get("pan") == "ABCDE1234F", "mislabelled identifier re-classified to its true type")


# ---------------------------------------------------------------------------
def run(coro):
    return asyncio.run(coro)


async def _routing():
    tok = current_account.set(MCPAccount(
        id=1, user_email="t@x.com", display_name=None, credits_balance=100,
        credits_tier="free", is_active=True, allow_raw_records=False,
    ))
    try:
        print("\n[2] smart_lookup routing")

        # phone + UPI question -> upi_from_phone (POST body phone)
        CALLS.clear()
        r = await sl.smart_lookup("what UPI ids does this number have?", {"phone": "9876543210"})
        check(r["status"] == "ok" and r["routing_trace"]["capability"] == "upi_from_phone",
              "phone + 'UPI' routes to upi_from_phone")
        check(CALLS and CALLS[-1]["endpoint"] == "/api/kyc/upi/from-phone", "dispatched to correct backend path")
        check(CALLS[-1]["body"] == {"phone": "9876543210"}, "correct request body built")
        check("*" in r["result"]["pan"], "PII masked in smart_lookup result (masked user)")

        # IFSC -> ifsc_lookup (GET path param)
        CALLS.clear()
        r = await sl.smart_lookup("which bank is this ifsc?", {"ifsc": "HDFC0001234"})
        check(r["routing_trace"]["capability"] == "ifsc_lookup", "IFSC routes to ifsc_lookup")
        check(CALLS[-1]["endpoint"] == "/api/kyc/bank/ifsc/HDFC0001234" and CALLS[-1]["method"] == "GET",
              "GET path-param filled correctly")

        # consent-required (pan_to_mobile) WITHOUT consent -> ConsentRequiredError (refunded upstream)
        CALLS.clear()
        raised = False
        try:
            await sl.smart_lookup("find the mobile for this PAN", {"pan": "ABCDE1234F"})
        except ConsentRequiredError:
            raised = True
        check(raised, "consent-required lookup with no consent raises ConsentRequiredError")
        check(not CALLS, "no backend call fired when consent missing")

        # same, WITH consent='Y' -> dispatches
        CALLS.clear()
        r = await sl.smart_lookup("find the mobile for this PAN", {"pan": "ABCDE1234F"}, consent="Y")
        check(CALLS and CALLS[-1]["endpoint"] == "/api/kyc/pan-to-mobile", "consent='Y' dispatches pan_to_mobile")
        check(CALLS[-1]["body"].get("consent") == "Y", "consent forwarded in body")

        # company name + directors question -> coded sequence (resolve then directors)
        CALLS.clear()
        r = await sl.smart_lookup("who are the directors of this company?", {"company_name": "ABC Cotspin"})
        eps = [c["endpoint"] for c in CALLS]
        check(any("resolve" in e for e in eps) and any("/directors" in e for e in eps),
              "company + 'directors' runs resolve -> directors sequence")
        check(r.get("directors") is not None, "sequence returns directors")

        # no identifier -> disambiguation
        r = await sl.smart_lookup("tell me about this person", {})
        check(r["status"] == "needs_disambiguation", "no identifier -> disambiguation")

        # money path can never be reached via smart_lookup (not in retrieval set)
        CALLS.clear()
        r = await sl.smart_lookup("penny drop this account and send a rupee", {"ifsc": "HDFC0001234"})
        eps = [c["endpoint"] for c in CALLS]
        check(all("penny-drop" not in e for e in eps), "smart_lookup never dispatches a money-movement path")
    finally:
        current_account.reset(tok)


run(_routing())

print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
