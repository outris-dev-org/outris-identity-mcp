"""
Phase-3b tests: Aadhaar OKYC 2-step flow (offline, no network/DB).

Run: python test_okyc_flow.py

Proves: init needs a valid consent token; the opaque transaction_id survives PII
masking (control_fields fix); verify forwards otp+transaction_id and masks PII for
a masked account; the token is live across init (consume=False) and burned on
verify (consume=True).
"""
import asyncio
from datetime import datetime, timedelta

import mcp_server.tools.generic as generic
from mcp_server.tools import aadhaar
from mcp_server.core.consent import ConsentGrant, _paths_match
from mcp_server.tools.helpers import ConsentRequiredError
from mcp_server.core.context import current_account
from mcp_server.core.auth import MCPAccount

FAIL, CALLS = [], []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


async def _fake_call_backend(endpoint, method="GET", params=None, json_data=None, api_key=None):
    CALLS.append({"endpoint": endpoint, "method": method, "body": json_data})
    if endpoint.endswith("/okyc/init"):
        return {"transaction_id": "1234567890123", "message": "OTP sent"}
    return {"name": "Rahul Sharma", "dob": "1990-01-01", "address": "12 MG Road",
            "status": "success", "transaction_id": "1234567890123"}


generic.call_backend = _fake_call_backend
generic.get_settings = lambda: type("S", (), {"allow_legacy_consent_y": False})()


class FakeStore:
    def __init__(self):
        self.tokens = {}

    def issue(self, token, account_id, path):
        self.tokens[token] = {"account_id": account_id, "path": path, "used": False,
                              "expires_at": datetime.now() + timedelta(minutes=15)}

    async def validate(self, token, capability_path, account_id, *, consume=True):
        row = self.tokens.get(token) if token else None
        if row is None or row["used"]:
            return None
        if account_id is not None and row["account_id"] != account_id:
            return None
        if not _paths_match(row["path"], capability_path):
            return None
        if datetime.now() > row["expires_at"]:
            return None
        if consume:
            row["used"] = True
        return ConsentGrant(row["path"], "consent", None)


store = FakeStore()
generic.validate_and_consume_consent_token = store.validate


async def main():
    tok = current_account.set(MCPAccount(
        id=7, user_email="t@x.com", display_name=None, credits_balance=100,
        credits_tier="free", is_active=True, allow_raw_records=False,
    ))
    try:
        # 1. init WITHOUT a valid token -> rejected, no backend hit
        CALLS.clear()
        raised = False
        try:
            await aadhaar.aadhaar_okyc_init("123456789012", consent_token="nope")
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "OKYC init without a valid token is rejected (no OTP sent)")

        # 2. init WITH a valid OKYC token -> OTP sent; transaction_id returned UNMASKED
        store.issue("okyc1", account_id=7, path="/api/kyc/aadhaar/okyc/init")
        CALLS.clear()
        r = await aadhaar.aadhaar_okyc_init("123456789012", consent_token="okyc1")
        check(CALLS and CALLS[-1]["endpoint"].endswith("/okyc/init"), "init calls the OKYC init endpoint")
        check(r.get("transaction_id") == "1234567890123",
              "transaction_id survives PII masking (control_fields fix)")
        check(store.tokens["okyc1"]["used"] is False, "token stays live after init (consume=False)")

        # 3. verify WITH the same token -> forwards otp + txn; masks PII; burns token
        CALLS.clear()
        r = await aadhaar.aadhaar_okyc_verify(transaction_id="1234567890123", otp="123456",
                                              consent_token="okyc1")
        body = CALLS[-1]["body"]
        check(CALLS[-1]["endpoint"].endswith("/okyc/verify"), "verify calls the OKYC verify endpoint")
        check(body.get("otp") == "123456" and body.get("transaction_id") == "1234567890123",
              "verify forwards otp + transaction_id")
        check("*" in r.get("name", ""), "masked account: identity fields are masked")
        check(r.get("transaction_id") == "1234567890123", "verify keeps transaction_id unmasked")
        check(store.tokens["okyc1"]["used"] is True, "token burned after verify (consume=True)")

        # 4. token reuse after verify -> rejected
        raised = False
        try:
            await aadhaar.aadhaar_okyc_verify("1234567890123", "654321", "okyc1")
        except ConsentRequiredError:
            raised = True
        check(raised, "burned OKYC token cannot be reused")
    finally:
        current_account.reset(tok)


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
