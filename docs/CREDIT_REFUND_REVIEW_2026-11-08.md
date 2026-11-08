# MCP credit refund review

Concurrent backend-error callbacks previously read the outstanding charge before the refund transaction and cleared it later. Two callbacks could both refund the same tool call. The refund now locks the tool-call row inside the transaction, refunds credits_charged, records the refund, and clears that charge before commit. A ledger failure rolls back every mutation. Portal-proxy mode remains non-mutating.

The localhost PostgreSQL regression sends 12 concurrent callbacks and verifies one refund, the actual outstanding charge when it differs from nominal cost, rollback after an injected ledger constraint failure, and safe retry. Run python -m pytest -q tests/test_credit_refund_atomicity.py with TEST_MCP_PG_DSN set to disposable localhost PostgreSQL. The fixture rejects remote hosts and creates/drops its own test database. CI supplies this environment.

No schema migration is required. Confirm the deployed revision and effective billing mode before rollout; this PR does not certify or repair historical balances.
