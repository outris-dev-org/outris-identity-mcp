"""
Phase-3c tests: async job path for due_diligence_person (offline, no network/DB).

Run: python test_async_jobs.py

Stubs the jobs store (in-memory), generic.call_backend, and credits.record_tool_result.
Proves: start returns a job_id fast; the background worker completes + masks the
result; check_job is account-scoped; a backend 5xx refunds via the SAME credit id;
and the sync fallback fires when the job store is unavailable (pre-migration).
"""
import asyncio

import mcp_server.tools.generic as generic
import mcp_server.core.jobs as jobs_mod
import mcp_server.core.credits as credits_mod
from mcp_server.tools import intent_tools
from mcp_server.tools.helpers import BackendError
from mcp_server.core.context import current_account
from mcp_server.core.auth import MCPAccount

FAIL, CALLS, REFUNDS = [], [], []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


async def _fake_call_backend(endpoint, method="GET", params=None, json_data=None, api_key=None):
    CALLS.append({"endpoint": endpoint, "body": json_data})
    return {"verdict": "clear", "name": "Rahul Sharma", "status": "ok"}


generic.call_backend = _fake_call_backend
# legacy consent flag stays True (default) so consent='Y' is accepted in this test.


class JobsFake:
    def __init__(self):
        self.jobs = {}
        self.raise_on_create = False

    async def create_job(self, *, account_id, tool_name, capability_path, input_summary, credits_request_id):
        if self.raise_on_create:
            raise RuntimeError("job store unavailable (table not migrated)")
        jid = f"job-{len(self.jobs) + 1}"
        self.jobs[jid] = {"job_id": jid, "user_account_id": account_id, "status": "running",
                          "result": None, "error_code": None}
        return jid

    async def mark_complete(self, job_id, result):
        self.jobs[job_id].update(status="complete", result=result)

    async def mark_failed(self, job_id, error_code, error_message):
        self.jobs[job_id].update(status="failed", error_code=error_code)

    async def get_job(self, account_id, job_id):
        row = self.jobs.get(job_id)
        if row is None or (account_id is not None and row["user_account_id"] != account_id):
            return None
        return row


jf = JobsFake()
jobs_mod.create_job = jf.create_job
jobs_mod.mark_complete = jf.mark_complete
jobs_mod.mark_failed = jf.mark_failed
jobs_mod.get_job = jf.get_job


async def _fake_record(**kw):
    REFUNDS.append(kw)


credits_mod.record_tool_result = _fake_record


async def main():
    tok = current_account.set(MCPAccount(
        id=7, user_email="t@x.com", display_name=None, credits_balance=100,
        credits_tier="free", is_active=True, allow_raw_records=False,
    ))
    try:
        # 1. start returns quickly with a job_id; background worker then completes.
        CALLS.clear()
        r = await intent_tools.due_diligence_person_start("9876543210", consent="Y", name="Rahul")
        check(r["status"] == "running" and r.get("job_id"), "start returns status=running + job_id fast")
        jid = r["job_id"]
        await asyncio.sleep(0.05)  # let the background task run
        check(jf.jobs[jid]["status"] == "complete", "background worker marked the job complete")

        # 2. check_job returns the masked result.
        cj = await intent_tools.check_job(jid)
        check(cj["status"] == "complete" and "*" in cj["result"].get("name", ""),
              "check_job returns the completed, PII-masked result")

        # 3. ownership: another account cannot read the job.
        tok2 = current_account.set(MCPAccount(
            id=8, user_email="other@x.com", display_name=None, credits_balance=1,
            credits_tier="free", is_active=True, allow_raw_records=False))
        try:
            cj2 = await intent_tools.check_job(jid)
        finally:
            current_account.reset(tok2)
        check(cj2["status"] == "not_found", "check_job is account-scoped (other account -> not_found)")

        # 4. refund on backend 5xx (drive the worker directly with a failing backend).
        REFUNDS.clear()
        async def _boom(endpoint, method="GET", params=None, json_data=None, api_key=None):
            raise BackendError(502, "Upstream service returned HTTP 502.")
        generic.call_backend = _boom
        jf.jobs["job-x"] = {"job_id": "job-x", "user_account_id": 7, "status": "running", "result": None, "error_code": None}
        await intent_tools._run_dd_job("job-x", {"phone": "91xxxx"}, None, "Y", "cr-123")
        check(jf.jobs["job-x"]["status"] == "failed", "backend 5xx marks the job failed")
        check(REFUNDS and REFUNDS[-1].get("is_backend_error") is True and REFUNDS[-1].get("request_id") == "cr-123",
              "backend 5xx refunds via the ORIGINAL credit_request_id")
        generic.call_backend = _fake_call_backend

        # 5. sync fallback when the job store is unavailable (pre-migration).
        CALLS.clear()
        jf.raise_on_create = True
        r = await intent_tools.due_diligence_person_start("9876543210", consent="Y")
        check(r["status"] == "complete" and "result" in r, "sync fallback returns the result inline when job store is down")
        check(CALLS and CALLS[-1]["endpoint"].endswith("/screening/person"), "fallback actually called the backend")
        jf.raise_on_create = False
    finally:
        current_account.reset(tok)


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
