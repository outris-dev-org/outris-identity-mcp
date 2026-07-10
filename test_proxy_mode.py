"""
Phase-2 Slice-0 tests: portal-proxy billing mode + frozen ledger (offline).

Run: python test_proxy_mode.py

Proves: in shadow/sso mode call_backend routes through /api/portal/execute with the
user's JWT, unwraps {status_code,ok,body}, maps a wrapped >=400 to BackendError, and
fails closed without a JWT; ledger mode keeps the direct shared-key path; and
deduct_credits does NOT mutate balance / never raises InsufficientCredits when the
ledger is frozen.
"""
import asyncio
import uuid

import httpx

import mcp_server.tools.helpers as helpers
import mcp_server.core.config as cfg
from mcp_server.tools.helpers import BackendError
from mcp_server.core.context import current_user_jwt
from mcp_server.core.auth import MCPAccount

FAIL = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class FakeResp:
    def __init__(self, json_data, http_status=200):
        self._json = json_data
        self.status_code = http_status
        self.text = str(json_data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._json


class FakeClient:
    def __init__(self):
        self.calls = []
        self._next = FakeResp({})

    async def post(self, url, json=None, headers=None, params=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "params": params, "m": "POST"})
        return self._next

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "m": "GET"})
        return self._next


fc = FakeClient()


async def _fake_client():
    return fc


helpers.get_http_client = _fake_client


class FakeSettings:
    def __init__(self, mode):
        self.mcp_billing_mode = mode
        self.backend_url = "https://api.outris.com"
        self.backend_api_key = "svc-key"


def set_mode(mode):
    helpers.get_settings = lambda: FakeSettings(mode)
    cfg.get_settings = lambda: FakeSettings(mode)


async def main():
    # ------------------------------------------------------------------ proxy
    set_mode("shadow")
    tok = current_user_jwt.set("ey.userjwt")
    try:
        # 1. proxy routing + unwrap + scrub
        fc.calls.clear()
        fc._next = FakeResp({"status_code": 200, "ok": True,
                             "body": {"name": "X", "provider": "EnrichData"}})
        r = await helpers.call_backend("/api/collections/phone", method="POST", json_data={"phone": "9"})
        call = fc.calls[-1]
        check(call["url"].endswith("/api/portal/execute"), "shadow mode routes through /api/portal/execute")
        check(call["headers"]["Authorization"] == "Bearer ey.userjwt", "forwards the user's JWT")
        check(call["json"] == {"method": "POST", "path": "/api/collections/phone", "body": {"phone": "9"}, "query": None},
              "builds the correct proxy payload {method,path,body,query}")
        check(r == {"name": "X", "provider": "supplier"}, "unwraps body + scrubs supplier name")

        # 2. wrapped >=400 -> BackendError with that status
        fc._next = FakeResp({"status_code": 502, "ok": False, "body": {"error": "x"}})
        raised_status = None
        try:
            await helpers.call_backend("/api/kyc/pan/comprehensive", method="POST", json_data={"pan": "P"})
        except BackendError as e:
            raised_status = e.status_code
        check(raised_status == 502, "wrapped status 502 maps to BackendError(502)")
    finally:
        current_user_jwt.reset(tok)

    # 3. no JWT in proxy mode -> fail closed (401), no call fired
    fc.calls.clear()
    raised = None
    try:
        await helpers.call_backend("/api/collections/phone", method="POST", json_data={"phone": "9"})
    except BackendError as e:
        raised = e.status_code
    check(raised == 401 and not fc.calls, "proxy mode without a user session fails closed (401), no call")

    # ------------------------------------------------------------------ ledger
    set_mode("ledger")
    fc.calls.clear()
    fc._next = FakeResp({"data": 1, "provider": "gridlines"})
    r = await helpers.call_backend("/api/lookup/919812345678", method="GET")
    call = fc.calls[-1]
    check("portal/execute" not in call["url"] and call["url"].endswith("/api/lookup/919812345678"),
          "ledger mode keeps the direct backend path (no proxy)")
    check(call["headers"].get("X-API-Key") == "svc-key", "ledger mode uses the shared service key")
    check(r == {"data": 1, "provider": "supplier"}, "ledger mode still scrubs supplier names")

    # ------------------------------------------------------ frozen ledger deduct
    from mcp_server.core import credits
    from mcp_server.core.database import Database

    async def _noop_execute(*a, **k):
        return None
    Database.execute = _noop_execute  # swallow the observability insert (no DB)

    set_mode("shadow")
    acct = MCPAccount(id=1, user_email="t@x.com", display_name=None, credits_balance=0,
                      credits_tier="free", is_active=True, allow_raw_records=False)
    before, after = await credits.deduct_credits(acct, "investigate_phone", 999, str(uuid.uuid4()))
    check((before, after) == (0, 0), "frozen ledger: deduct_credits does not mutate balance (returns 0,0)")
    # even with cost 999 > balance 0, no InsufficientCreditsError was raised (we got here)
    check(True, "frozen ledger: no InsufficientCreditsError when the mcp ledger is frozen")


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
