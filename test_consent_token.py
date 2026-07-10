"""
Phase-3a tests: server-issued consent-token gate (offline, no network/DB).

Run: python test_consent_token.py

Monkeypatches generic.call_backend (record calls), generic.get_settings (toggle
the legacy flag), and generic.validate_and_consume_consent_token (in-memory
FakeConsentStore that mirrors the real single-use / path- / account-scoped logic).
"""
import asyncio
from datetime import datetime, timedelta

import mcp_server.tools.generic as generic
from mcp_server.tools.helpers import ConsentRequiredError, PreflightRejection, classify_tool_error
from mcp_server.core.consent import ConsentGrant, _paths_match
from mcp_server.core.context import current_account
from mcp_server.core.auth import MCPAccount

FAIL, CALLS = [], []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


async def _fake_call_backend(endpoint, method="GET", params=None, json_data=None, api_key=None):
    CALLS.append({"endpoint": endpoint, "method": method, "body": json_data})
    return {"ok": True, "name": "Rahul Sharma"}


generic.call_backend = _fake_call_backend


class FakeSettings:
    def __init__(self, legacy):
        self.allow_legacy_consent_y = legacy


def set_legacy(flag):
    generic.get_settings = lambda: FakeSettings(flag)


class FakeConsentStore:
    """Mirrors core/consent.validate_and_consume_consent_token."""
    def __init__(self):
        self.tokens = {}

    def issue(self, token, account_id, path, ttl_min=15):
        self.tokens[token] = {
            "account_id": account_id, "path": path, "used": False,
            "expires_at": datetime.now() + timedelta(minutes=ttl_min),
            "consent_text": "consent by user",
        }

    async def validate(self, token, capability_path, account_id, *, consume=True):
        if not token:
            return None
        row = self.tokens.get(token)
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
        return ConsentGrant(row["path"], row["consent_text"], None)


store = FakeConsentStore()
generic.validate_and_consume_consent_token = store.validate

PATH = "/api/collections/phone"


async def main():
    tok = current_account.set(MCPAccount(
        id=1, user_email="t@x.com", display_name=None, credits_balance=100,
        credits_tier="free", is_active=True, allow_raw_records=False,
    ))
    try:
        # 1. valid token -> backend called, server-injects consent='Y' (model value ignored)
        set_legacy(False)
        store.issue("good1", account_id=1, path=PATH)
        CALLS.clear()
        await generic.execute_endpoint("POST", PATH, body={"phone": "9"},
                                       consent_token="good1", consent="whatever")
        check(len(CALLS) == 1 and CALLS[0]["body"].get("consent") == "Y",
              "valid token -> server injects consent='Y'")

        # 2. single-use: second use of same token -> rejected
        CALLS.clear()
        raised = False
        try:
            await generic.execute_endpoint("POST", PATH, body={"phone": "9"}, consent_token="good1")
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "consumed token is single-use (2nd use rejected, no backend call)")

        # 3. legacy flag OFF + typed 'Y' + no token -> rejected (Slice-B enforcement)
        CALLS.clear()
        raised = False
        try:
            await generic.execute_endpoint("POST", PATH, body={"phone": "9"}, consent="Y")
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "flag OFF: typed 'Y' is NOT accepted")

        # 4. legacy flag ON + typed 'Y' -> accepted (migration compatibility)
        set_legacy(True)
        CALLS.clear()
        await generic.execute_endpoint("POST", PATH, body={"phone": "9"}, consent="Y")
        check(len(CALLS) == 1 and CALLS[0]["body"].get("consent") == "Y",
              "flag ON: typed 'Y' accepted during migration")

        # 5. flag ON + neither token nor 'Y' -> rejected
        CALLS.clear()
        raised = False
        try:
            await generic.execute_endpoint("POST", PATH, body={"phone": "9"})
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "no token + no 'Y' -> rejected")

        # 6. account-scope: token for account 2 rejected for account 1
        set_legacy(False)
        store.issue("acct2", account_id=2, path=PATH)
        CALLS.clear()
        raised = False
        try:
            await generic.execute_endpoint("POST", PATH, body={"phone": "9"}, consent_token="acct2")
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "token bound to another account is rejected")

        # 7. path-scope: token for a different path rejected
        store.issue("otherpath", account_id=1, path="/api/kyc/uan-history")
        CALLS.clear()
        raised = False
        try:
            await generic.execute_endpoint("POST", PATH, body={"phone": "9"}, consent_token="otherpath")
        except ConsentRequiredError:
            raised = True
        check(raised and not CALLS, "token for a different path is rejected")

        # 8. classify_tool_error -> refund on consent rejection
        refund, code, _ = classify_tool_error(ConsentRequiredError("x"))
        check(refund and code == "consent_required" and issubclass(ConsentRequiredError, PreflightRejection),
              "consent rejection is refunded (PreflightRejection)")
    finally:
        current_account.reset(tok)


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
