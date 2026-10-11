"""Signed-in playground runs must execute get_email, not the demo allowlist.

Run: python test_playground_run_tool.py
"""
import asyncio

from mcp_server.core.auth import MCPAccount
from mcp_server.routes import user_routes
from mcp_server.routes.public_routes import ALLOWED_TOOLS
from mcp_server.tools import investigation  # noqa: F401  registers get_email

FAIL = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


async def main():
    check("get_email" not in ALLOWED_TOOLS, "anonymous demo allowlist still excludes get_email")

    account = MCPAccount(
        id=1,
        user_email="a@outris.com",
        display_name="A",
        credits_balance=10,
        credits_tier="free",
        is_active=True,
        allow_raw_records=True,
    )
    seen = {}

    async def fake_user(_auth):
        return {"email": "a@outris.com"}

    async def fake_info(_email):
        return {"id": 1, "is_active": True}

    async def fake_by_id(_account_id):
        return account

    async def fake_deduct(**kwargs):
        seen["deducted"] = kwargs["tool_name"]
        return (10, 8)

    async def fake_execute(name, arguments, **kwargs):
        seen["executed"] = (name, arguments, kwargs.get("user_jwt"))
        return {"success": True, "phone": arguments["phone"], "emails": ["a@b.com"], "count": 1}, 12.0

    async def fake_record(**kwargs):
        seen["recorded"] = kwargs["success"]

    async def fake_balance(_query, *_args):
        return 8

    user_routes.effective_billing_mode_for = lambda _email, _has_jwt: "ledger"
    user_routes.get_current_user = fake_user
    user_routes.get_mcp_account_by_email = fake_info
    user_routes.get_account_by_id = fake_by_id
    user_routes.deduct_credits = fake_deduct
    user_routes.execute_tool = fake_execute
    user_routes.record_tool_result = fake_record
    user_routes.Database.fetchval = fake_balance

    class Req:
        headers = {"Authorization": "Bearer ey.test-jwt"}

    body = user_routes.RunToolRequest(tool="get_email", inputs={"phone": "8431271501"})
    resp = await user_routes.run_tool_authenticated(body, Req())

    check(resp.success is True, "get_email run succeeds for a signed-in account")
    check(resp.demo_mode is False, "signed-in run is not demo mode")
    check(resp.error is None, "signed-in get_email is not rejected as unavailable")
    check(resp.result and resp.result.get("emails") == ["a@b.com"], "email payload is returned")
    check(seen.get("executed", (None, None, None))[0] == "get_email", "executor receives get_email")
    check(seen.get("executed", (None, None, None))[2] == "ey.test-jwt", "portal JWT is forwarded for billing")
    check(resp.credits_remaining == 8, "credit balance is returned to the playground")


if __name__ == "__main__":
    asyncio.run(main())
    if FAIL:
        raise SystemExit(f"{len(FAIL)} check(s) failed")
    print("playground run-tool checks passed")
