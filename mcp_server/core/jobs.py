"""
DB-backed async job store (Phase 3c).

Long-running tools (e.g. due_diligence_person, 40-70s) would block past MCP
client read timeouts. Instead they enqueue a job here and return a job_id the
model polls with the free ``check_job`` tool.

State lives in ``mcp.async_jobs`` (migrations/003_phase3_consent_and_jobs.sql) so
it is shared across stateless workers (a bare in-memory dict would not survive a
multi-worker deployment). ``create_job`` raises if the table is missing so the
caller can fall back to running synchronously; reads/writes elsewhere fail-closed.

Durability caveat (documented, not fully solved here): ``asyncio.create_task``
does not survive a redeploy — a job can be left ``running``. ``reap_stale_jobs``
sweeps jobs past ``expires_at`` to ``failed``; call it on startup / from a cron.
A real queue is a follow-up.
"""
import datetime as _dt
import json
import logging
import uuid
from typing import Optional

from .database import Database

logger = logging.getLogger(__name__)


def _json_default(obj):
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    return str(obj)


def _dumps(obj) -> str:
    # datetime-safe: DB/HTTP payloads carry datetimes (CLAUDE.md cache-write bug class)
    return json.dumps(obj, default=_json_default)


async def create_job(*, account_id: int, tool_name: str, capability_path: Optional[str],
                     input_summary: Optional[dict], credits_request_id: Optional[str]) -> str:
    """Insert a 'running' job and return its id. RAISES on DB/table error so the
    caller can fall back to a synchronous execution."""
    job_id = str(uuid.uuid4())
    await Database.execute(
        """INSERT INTO mcp.async_jobs
             (job_id, user_account_id, tool_name, capability_path, status,
              input_summary, credits_request_id, created_at, updated_at, expires_at)
           VALUES ($1,$2,$3,$4,'running',$5::jsonb,$6,NOW(),NOW(),NOW() + INTERVAL '1 hour')""",
        job_id, account_id, tool_name, capability_path,
        _dumps(input_summary or {}),
        uuid.UUID(credits_request_id) if credits_request_id else None,
    )
    return job_id


async def mark_complete(job_id: str, result) -> None:
    try:
        await Database.execute(
            """UPDATE mcp.async_jobs
                 SET status='complete', result=$2::jsonb, updated_at=NOW()
               WHERE job_id=$1""",
            job_id, _dumps(result),
        )
    except Exception as e:
        logger.error(f"mark_complete failed for job {job_id}: {e}")


async def mark_failed(job_id: str, error_code: str, error_message: str) -> None:
    try:
        await Database.execute(
            """UPDATE mcp.async_jobs
                 SET status='failed', error_code=$2, error_message=$3, updated_at=NOW()
               WHERE job_id=$1""",
            job_id, error_code, (error_message or "")[:500],
        )
    except Exception as e:
        logger.error(f"mark_failed failed for job {job_id}: {e}")


async def get_job(account_id: Optional[int], job_id: str) -> Optional[dict]:
    """Fetch a job SCOPED to the account (a user must not read another's job).
    Fail-closed: returns None on any error."""
    try:
        row = await Database.fetchrow(
            "SELECT * FROM mcp.async_jobs WHERE job_id=$1 AND user_account_id=$2",
            job_id, account_id,
        )
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"get_job failed (fail-closed) for {job_id}: {e}")
        return None


async def reap_stale_jobs() -> int:
    """Mark 'running' jobs past expires_at as 'failed'. Returns count. Call on
    startup / periodically. Best-effort; the credit for a reaped job stays
    charged (a stuck job did consume the enqueue slot) — a refunding sweep is a
    follow-up."""
    try:
        rows = await Database.fetch(
            """UPDATE mcp.async_jobs
                 SET status='failed', error_code='stale', updated_at=NOW()
               WHERE status='running' AND NOW() > expires_at
               RETURNING job_id""",
        )
        return len(rows or [])
    except Exception as e:
        logger.warning(f"reap_stale_jobs failed: {e}")
        return 0
