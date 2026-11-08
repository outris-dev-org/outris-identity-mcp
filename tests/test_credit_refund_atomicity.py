"""Disposable localhost PostgreSQL only; no backend tool calls or real accounts."""
import asyncio
import os
import uuid
from urllib.parse import urlsplit
import asyncpg
import pytest
from mcp_server.core import credits, config
from mcp_server.core.database import Database


@pytest.mark.asyncio
async def test_concurrent_refunds_and_transaction_failure(monkeypatch):
    dsn=os.getenv('TEST_MCP_PG_DSN')
    if not dsn:pytest.skip('Set TEST_MCP_PG_DSN to disposable localhost PostgreSQL')
    assert urlsplit(dsn).hostname in {'localhost','127.0.0.1'}
    monkeypatch.setattr(config,'get_effective_billing_mode',lambda:'ledger')
    root=await asyncpg.connect(dsn)
    name='mcp_refund_test_'+uuid.uuid4().hex
    await root.execute('CREATE DATABASE '+name)
    target=dsn.rsplit('/',1)[0]+'/'+name
    pool=await asyncpg.create_pool(target,min_size=1,max_size=4)
    monkeypatch.setattr(Database,'pool',pool)
    try:
        async with pool.acquire() as conn:
            await conn.execute('''CREATE SCHEMA mcp;
                CREATE TABLE mcp.user_accounts(id int PRIMARY KEY,credits_balance int,total_credits_used int,updated_at timestamp);
                CREATE TABLE mcp.user_tool_calls(request_id uuid PRIMARY KEY,user_account_id int,credits_cost int,credits_charged int);
                CREATE TABLE mcp.user_credit_transactions(user_account_id int,transaction_type text,
                    amount int,balance_before int,balance_after int,reference_type text,reference_id text,description text,created_at timestamp);
                INSERT INTO mcp.user_accounts VALUES(1,97,3,now());''')
            request=uuid.uuid4()
            # Cost may differ from the actual outstanding charge: refund charge.
            await conn.execute('INSERT INTO mcp.user_tool_calls VALUES($1,1,10,3)',request)
        await asyncio.gather(*(credits._refund_credits_for_backend_error(str(request)) for _ in range(12)))
        async with pool.acquire() as conn:
            assert await conn.fetchval('SELECT credits_balance FROM mcp.user_accounts')==100
            assert await conn.fetchval('SELECT total_credits_used FROM mcp.user_accounts')==0
            assert await conn.fetchval('SELECT COUNT(*) FROM mcp.user_credit_transactions')==1
            assert await conn.fetchval('SELECT credits_charged FROM mcp.user_tool_calls')==0
            await conn.execute('ALTER TABLE mcp.user_credit_transactions ADD CONSTRAINT injected_failure CHECK(amount<0) NOT VALID')
            second=uuid.uuid4()
            await conn.execute('UPDATE mcp.user_accounts SET credits_balance=98,total_credits_used=2')
            await conn.execute('INSERT INTO mcp.user_tool_calls VALUES($1,1,2,2)',second)
        with pytest.raises(asyncpg.CheckViolationError):
            await credits._refund_credits_for_backend_error(str(second))
        async with pool.acquire() as conn:
            assert await conn.fetchval('SELECT credits_balance FROM mcp.user_accounts')==98
            assert await conn.fetchval('SELECT credits_charged FROM mcp.user_tool_calls WHERE request_id=$1',second)==2
            await conn.execute('ALTER TABLE mcp.user_credit_transactions DROP CONSTRAINT injected_failure')
        await credits._refund_credits_for_backend_error(str(second))
        await credits._refund_credits_for_backend_error(str(second))
        async with pool.acquire() as conn:
            assert await conn.fetchval('SELECT credits_balance FROM mcp.user_accounts')==100
            assert await conn.fetchval('SELECT COUNT(*) FROM mcp.user_credit_transactions')==2
    finally:
        await pool.close()
        await root.execute('DROP DATABASE '+name)
        await root.close()
