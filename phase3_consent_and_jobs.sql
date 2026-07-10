-- Phase 3 schema: consent tokens + async jobs.
-- Apply ONCE to the shared Postgres (idempotent). Sibling of mcp_oauth_schema.sql.
--   psql "$DATABASE_URL" -f phase3_consent_and_jobs.sql
-- NOTE (per project memory): the staging/prod Postgres is SHARED — applying this
-- makes both tables immediately live for both environments.

CREATE SCHEMA IF NOT EXISTS mcp;

-- Server-issued, single-use, human-gated consent tokens (core/consent.py).
-- Timestamps are naive (matches mcp.oauth_codes; avoids offset-aware pitfalls).
CREATE TABLE IF NOT EXISTS mcp.consent_tokens (
    token           TEXT PRIMARY KEY,            -- secrets.token_urlsafe(32)
    user_account_id INTEGER NOT NULL,
    user_email      TEXT NOT NULL,
    capability_path TEXT NOT NULL,               -- cleaned path, e.g. /api/collections/phone
    subject_ref     TEXT,                        -- sha256(identifier)[:16], NEVER raw PII
    consent_text    TEXT,
    expires_at      TIMESTAMP NOT NULL,
    used            BOOLEAN NOT NULL DEFAULT FALSE,
    used_at         TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consent_tokens_acct ON mcp.consent_tokens(user_account_id);
CREATE INDEX IF NOT EXISTS idx_consent_tokens_expires ON mcp.consent_tokens(expires_at);

-- Async job store for long-running tools (core/jobs.py). Shared across workers.
CREATE TABLE IF NOT EXISTS mcp.async_jobs (
    job_id             UUID PRIMARY KEY,
    user_account_id    INTEGER NOT NULL,
    tool_name          TEXT NOT NULL,
    capability_path    TEXT,
    status             TEXT NOT NULL DEFAULT 'running',   -- running | complete | failed
    input_summary      JSONB,                              -- keys only, no raw PII
    result             JSONB,                              -- scrubbed + masked, only when complete
    error_code         TEXT,
    error_message      TEXT,
    credits_request_id UUID,                               -- links to mcp.user_tool_calls for refund
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at         TIMESTAMP NOT NULL DEFAULT NOW() + INTERVAL '1 hour'
);
CREATE INDEX IF NOT EXISTS idx_async_jobs_acct ON mcp.async_jobs(user_account_id);
CREATE INDEX IF NOT EXISTS idx_async_jobs_status ON mcp.async_jobs(status);
