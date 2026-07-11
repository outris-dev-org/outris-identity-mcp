# Outris Identity MCP — Testing Guide

A complete, hands-on guide for testers to exercise the Outris Identity MCP server
end-to-end. It covers connecting, authenticating, every tool, the consent
handshake, async jobs, PII masking, the safety guardrails, discovery, and the
(optional) SSO-billing canary.

- **Server:** `https://mcp-server.outris.com`
- **Version under test:** 12-tool curated surface (Phases 0–3 + portal-SSO billing)
- **Transports:** Streamable HTTP (`POST /http`, primary), SSE (`GET /sse`), STDIO (local)

> **Golden rule for testers:** every lookup hits **real data** and (in `ledger`
> mode) **costs credits**. Only look up identifiers you are authorised to query
> (DPDPA). There are **no money-movement tools** exposed — if you ever see one,
> that's a bug: report it immediately.

---

## Table of contents
1. [Quick smoke test (no auth)](#1-quick-smoke-test-no-auth)
2. [Getting credentials](#2-getting-credentials)
3. [How to call a tool](#3-how-to-call-a-tool)
4. [The 12 tools — test cases](#4-the-12-tools--test-cases)
5. [smart_lookup (the router)](#5-smart_lookup-the-router)
6. [Consent handshake (find_contacts, due_diligence)](#6-consent-handshake)
7. [Async jobs (due diligence)](#7-async-jobs)
8. [PII masking](#8-pii-masking)
9. [Safety / negative tests (must-pass)](#9-safety--negative-tests-must-pass)
10. [Discovery resource](#10-discovery-resource)
11. [Billing modes + SSO canary](#11-billing-modes--sso-canary)
12. [Full test matrix / checklist](#12-full-test-matrix--checklist)
13. [Known limitations](#13-known-limitations)
14. [Reporting bugs](#14-reporting-bugs)

---

## 1. Quick smoke test (no auth)

These endpoints need no credentials — run them first to confirm the server is up
and on the new build.

```bash
# Health — expect status "healthy" and tools_count = 12
curl -s https://mcp-server.outris.com/health

# Tool list (human view)
curl -s https://mcp-server.outris.com/tools

# Tool list (MCP protocol) — expect 12 tools incl. smart_lookup
curl -s -X POST https://mcp-server.outris.com/http \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**PASS:** `tools_count: 12`, and the names are: `investigate_phone`,
`assess_fraud_risk`, `find_contacts`, `due_diligence_person_start`, `check_job`,
`investigate_email`, `resolve_company`, `lookup_gst`, `verify_pan`,
`lookup_vehicle`, `verify_bank_account`, `smart_lookup`.
**FAIL:** you see old names (`get_name`, `get_identity_profile`, `check_breaches`,
`verify_pan_detailed`, …) — that's the old build; report it.

---

## 2. Getting credentials

`tools/call` requires a `Authorization: Bearer <token>`. Two token types work:

### A) MCP API key (`mcp_...`) — simplest for testing
1. Go to **https://portal.outris.com/mcp**, sign in.
2. Click **Enable MCP** → copy your `mcp_...` key (shown once).
3. Use it as `Authorization: Bearer mcp_xxx`.

### B) Portal session JWT (`ey...`) — needed for consent tokens + SSO billing
1. Sign in at **https://portal.outris.com**.
2. Open browser DevTools → Network (or Application → Local Storage) and copy the
   session **JWT** (a long `eyJ...` string sent as `Authorization: Bearer` on
   portal API calls).
3. Use it as `Authorization: Bearer eyJ...`.

> Use **(B)** whenever a test needs `consent_token` minting (§6) or the SSO
> billing canary (§11). Use **(A)** for everything else.

### Claude Desktop config (optional, for interactive testing)
```json
{
  "mcpServers": {
    "outris-identity": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp-server.outris.com/http",
               "--transport", "streamable-http",
               "--header", "Authorization=Bearer YOUR_KEY"]
    }
  }
}
```
Restart Claude Desktop; the 12 tools appear under the 🔌 menu.

---

## 3. How to call a tool

Every `tools/call` is a JSON-RPC POST to `/http`:

```bash
curl -s -X POST https://mcp-server.outris.com/http \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_KEY" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"tools/call",
    "params":{"name":"<tool>","arguments":{ ... }}
  }'
```

**Response shape:** the result text is JSON with a trailing metadata line, e.g.
`...}\n\n[Credits: -3 | Remaining: 97 | Time: 812ms]` (ledger mode) or
`[Time: 812ms]` (shadow/sso mode). A masked response carries `"_masked": true`.

**Auth failures:** missing/invalid token → HTTP 401. Unknown tool → JSON-RPC
error `-32602`. Insufficient credits (ledger mode) → an `isError` result telling
you to top up.

---

## 4. The 12 tools — test cases

For each: the arguments, what to send, and what a PASS looks like. Substitute
your own **authorised** test identifiers. `id` can be any number.

| Tool | Required | Optional | What it returns |
|---|---|---|---|
| `investigate_phone` | `phone` | `depth` (`basic`\|`full`) | name(s), addresses, alt phones, footprint |
| `assess_fraud_risk` | `phone` | `detailed` (bool) | composite risk profile |
| `find_contacts` | `phone` | `consent_token`, `consent` | alt phones + geocoded addresses (**consent**) |
| `due_diligence_person_start` | `phone` | `consent_token`, `consent`, `name`, `pan`, `dob`, `email`, `city` | **async** → `job_id` (**consent**, premium) |
| `check_job` | `job_id` | — | status/result of an async job (free) |
| `investigate_email` | `email` | — | person behind an email |
| `resolve_company` | `company_name` | — | CIN + GSTIN/MSME |
| `lookup_gst` | `gstin` | — | GST registration details |
| `verify_pan` | `pan` | — | PAN holder name/status/type |
| `lookup_vehicle` | `rc_number` | — | vehicle + registered owner |
| `verify_bank_account` | `account_number`, `ifsc` | — | no-debit validation + holder name (**no money moved**) |
| `smart_lookup` | `question`, `identifiers` | `consent_token`, `consent` | routes to the right lookup/sequence |

### Examples

```bash
# investigate_phone (fast)
-d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"investigate_phone","arguments":{"phone":"9876543210","depth":"basic"}}}'

# investigate_phone (comprehensive — slower)
... "arguments":{"phone":"9876543210","depth":"full"}

# assess_fraud_risk
... "name":"assess_fraud_risk","arguments":{"phone":"9876543210","detailed":true}

# verify_pan
... "name":"verify_pan","arguments":{"pan":"ABCDE1234F"}

# lookup_gst
... "name":"lookup_gst","arguments":{"gstin":"27ABCDE1234F1Z5"}

# resolve_company
... "name":"resolve_company","arguments":{"company_name":"Reliance Industries Limited"}

# lookup_vehicle
... "name":"lookup_vehicle","arguments":{"rc_number":"MH12AB1234"}

# verify_bank_account (NO money moved — validation only)
... "name":"verify_bank_account","arguments":{"account_number":"1234567890","ifsc":"HDFC0001234"}

# investigate_email
... "name":"investigate_email","arguments":{"email":"someone@example.com"}
```

**PASS criteria (all tools):**
- HTTP 200 with a JSON result (not `isError`) for a valid input.
- **No internal supplier/vendor names anywhere** (see §9).
- PII masked unless your key has raw access (see §8).
- A clean, structured error (not a stack trace / raw upstream text) for a bad input.

---

## 5. smart_lookup (the router)

`smart_lookup` is for "I have an identifier and a question, figure out the right
API." It types the identifier deterministically, picks the best capability, and
dispatches — or asks you to disambiguate.

```bash
# phone + UPI question -> UPI handles
... "name":"smart_lookup","arguments":{"question":"what UPI ids does this number have?","identifiers":{"phone":"9876543210"}}

# IFSC -> bank/branch
... "arguments":{"question":"which bank is this ifsc?","identifiers":{"ifsc":"HDFC0001234"}}

# DIN -> directorships
... "arguments":{"question":"what companies is this director on?","identifiers":{"din":"01234567"}}

# company name + directors -> resolve then directors (a 2-step sequence)
... "arguments":{"question":"who are the directors of this company?","identifiers":{"company_name":"ABC Cotspin Private Limited"}}

# no identifier -> disambiguation
... "arguments":{"question":"tell me about this person","identifiers":{}}
```

**Identifier typing to test** (put the value under any key, or the wrong key — it
re-classifies by shape): `pan` (ABCDE1234F), `gstin` (15-char), `cin` (21-char
`L…`/`U…`), `ifsc` (`XXXX0######`), `phone` (10-digit / `91…`), `email`,
`vpa` (`name@bank`), `rc` (`MH12AB1234`), `din` (8-digit), `uan` (12-digit),
`udin` (18-digit), `company_name` (free text).

**PASS:**
- `status: "ok"` with a `routing_trace.capability` that matches the intent.
- Ambiguous / no-identifier → `status: "needs_disambiguation"` with `options`.
- Result is scrubbed + masked like any other tool.

**Key negative check:** a money-ish question (`"penny drop this account and send a
rupee"`) must **never** dispatch a money endpoint — `smart_lookup` has no
money capability in its routing set. PASS = it disambiguates or routes to a
non-money lookup; **FAIL = any money movement.**

---

## 6. Consent handshake

`find_contacts` and `due_diligence_person_start` require the end user's consent.
There are **two ways** to satisfy it during the current migration window:

### Option A — server-issued consent token (the real mechanism)
Requires a **portal JWT** (§2B).

```bash
# 1) Mint a consent token for the capability you'll call.
#    capability_path: /api/collections/phone (find_contacts)
#                     /api/screening/person  (due_diligence_person_start)
curl -s -X POST https://mcp-server.outris.com/api/mcp/consent/authorize \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJ...(portal JWT)" \
  -d '{"capability_path":"/api/collections/phone","subject":"9876543210"}'
#   -> {"consent_token":"<token>","expires_in_minutes":15}

# 2) Use the token (valid 15 min, single-use).
... "name":"find_contacts","arguments":{"phone":"9876543210","consent_token":"<token>"}
```

**Tests:**
- Valid token → call succeeds.
- Reuse the same token → **rejected** (single-use).
- Token minted for `/api/collections/phone` used on `due_diligence` → **rejected**
  (path-scoped).
- Token minted by user A used by user B → **rejected** (account-scoped).
- Expired token (wait >15 min) → **rejected**.
- No token and no `consent` → **rejected** with a clear "needs consent" message,
  **no credits charged**.

### Option B — legacy `consent:"Y"` (temporary, migration only)
While `allow_legacy_consent_y` is `true`, a literal `consent:"Y"` is accepted:
```bash
... "name":"find_contacts","arguments":{"phone":"9876543210","consent":"Y"}
```
> This fallback exists only until the portal consent screen ships; then it is
> disabled and **only** `consent_token` works. Testers should validate **both**
> paths.

**PASS:** consent-gated tools refuse to run without valid consent, and the
server injects the consent of record (you never see the model's raw value leak
downstream).

---

## 7. Async jobs

`due_diligence_person_start` runs a 40–70s panel **asynchronously** so it never
blocks/times out. It returns a `job_id`; you poll `check_job`.

```bash
# 1) Start (needs consent — token or legacy Y)
... "name":"due_diligence_person_start","arguments":{"phone":"9876543210","consent":"Y","name":"Test Person"}
#   -> {"status":"running","job_id":"<uuid>","poll_with":"check_job","eta_seconds":"40-70"}

# 2) Poll every ~10s
... "name":"check_job","arguments":{"job_id":"<uuid>"}
#   -> {"status":"running", ...}   (keep polling)
#   -> {"status":"complete","result":{...}}   (done)
```

**Tests:**
- Start returns `running` + a `job_id` **fast** (< a couple of seconds).
- `check_job` eventually returns `complete` with a masked result.
- `check_job` with **another user's** `job_id` → `not_found` (account-scoped).
- `check_job` is **free** (0 credits) — poll freely.
- If the backend errors mid-job, `check_job` returns `failed` and **you are not
  charged** for the failed panel.

---

## 8. PII masking

By default, responses are **masked** for keys/users without raw-record access
(names, PAN, phone, email, Aadhaar, addresses are `***`-masked; the response
carries `"_masked": true`). Company names, statuses, scores, and booleans are
**not** masked.

**Tests:**
- Standard key → PII masked, `"_masked": true`.
- A key with raw access (`allow_raw_records`) → PII in the clear, `"_masked": false`.
- Masking holds even on **nested/unusual shapes** (mask is value-shape based, not
  just key-name based) — e.g. a PAN embedded in a free-text field is still masked.

> If you have both a raw-eligible and a standard key, run the same lookup with
> each and diff the output.

---

## 9. Safety / negative tests (must-pass)

These are the guardrails that must hold. **Any failure here is a P0 bug.**

1. **No money tools.** `tools/list` contains no penny-drop / reverse-penny /
   transfer tool. `smart_lookup` never dispatches one (§5). `verify_bank_account`
   is validation-only (no debit).
2. **No supplier/vendor names leak.** Grep every response, error, `routing_trace`,
   and the `outris://capabilities` resource for these banned strings — there must
   be **zero** hits: `sign3`, `hibp`, `gridlines`, `aitan`, `crimescan`,
   `enrichdata`, `totalekyc`, `trustfull`, `bulkpe`, `surepass`, `smartauth`,
   `paysprint`, `digitap`, and any `s1`–`s14` supplier code.
   ```bash
   # example: fail if any banned name appears
   RESP=$(curl -s -X POST .../http -H "Authorization: Bearer KEY" -d '...verify_pan...')
   echo "$RESP" | grep -iE "sign3|hibp|gridlines|aitan|crimescan|enrichdata|totalekyc|bulkpe|surepass" && echo "LEAK - FAIL" || echo "clean - PASS"
   ```
3. **Errors are clean.** Force an upstream error (e.g. a malformed PAN, or a value
   that 500s upstream). The error must be a generic scrubbed message
   (`"Upstream service returned HTTP 5xx."`) — **never** a stack trace, raw
   upstream body, or supplier name. On a 5xx/timeout you are **refunded**
   (ledger) or simply **not billed** (shadow).
4. **Consent cannot be skipped.** §6 — consent tools refuse without valid consent.
5. **PII masking cannot be bypassed** for a standard key. §8.

---

## 10. Discovery resource

The full long-tail catalog is exposed as an MCP **resource** (so a client can
discover what `smart_lookup` can answer without loading 100 tool schemas).

```bash
# list resources -> expect outris://capabilities
... '{"jsonrpc":"2.0","id":1,"method":"resources/list"}'

# read it -> a client-safe (supplier-free) JSON catalog
... '{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"outris://capabilities"}}'
```

**PASS:** `resources/read` returns a JSON array of capabilities
(`{id, summary, inputs, consent_required, premium, moves_money}`), all
supplier-name free, and `moves_money:true` entries carry a "not available via the
assistant" note. Works on both `/http` and stdio/SSE.

---

## 11. Billing modes + SSO canary

The server has a billing flag (`MCP_BILLING_MODE`), default **`ledger`**:

- **`ledger` (default, live now):** tools cost MCP credits; each successful call
  shows `[Credits: -N | Remaining: X]`; a 5xx is refunded. This is what testers
  see by default.
- **`shadow` (SSO billing):** the call is routed through the user's **own** portal
  key (`POST /api/portal/execute`), so the **backend** meters + bills natively;
  the MCP credit ledger is frozen and the metadata line drops the credit numbers
  (just `[Time: …ms]`).

### Enabling the SSO canary (owner step — one-time)
Because a blanket flip would break `mcp_`-key users, `shadow` is enabled **per
email**:
```bash
# On the MCP Railway service (owner runs once):
railway variables --set MCP_SHADOW_EMAILS=tester@outris.com   # comma-separated for several
# Prereqs: PORTAL_KEY_ENCRYPTION_KEY set on the BFF (confirmed ✅),
#          and the tester has a client_billing_config on their billing key.
```
The listed emails route through the proxy **only when they authenticate with a
portal JWT** (§2B); everyone else stays on `ledger`.

### Validating the SSO canary
As a shadow-listed tester, authenticate with your **portal JWT** and run any tool.
**PASS:**
- The response metadata is `[Time: …ms]` (no credit numbers).
- Exactly **one** `api_usage_log` row appears under **your** portal key per tool
  call (backend admin/usage view), at the correct price.
- Your MCP `credits_balance` is **unchanged** (ledger frozen).
- No double-billing (never both a credit deduction and a backend bill).

---

## 12. Full test matrix / checklist

| # | Area | Test | Pass |
|---|---|---|---|
| 1 | Smoke | `/health` = healthy, tools_count 12 | ☐ |
| 2 | Smoke | `tools/list` = the 12 new names (no old names) | ☐ |
| 3 | Auth | tools/call without token → 401 | ☐ |
| 4 | Auth | mcp_ key works; portal JWT works | ☐ |
| 5 | Tool | `investigate_phone` basic + full | ☐ |
| 6 | Tool | `assess_fraud_risk` | ☐ |
| 7 | Tool | `investigate_email` | ☐ |
| 8 | Tool | `verify_pan` | ☐ |
| 9 | Tool | `lookup_gst` | ☐ |
| 10 | Tool | `resolve_company` | ☐ |
| 11 | Tool | `lookup_vehicle` | ☐ |
| 12 | Tool | `verify_bank_account` (no money moved) | ☐ |
| 13 | Router | `smart_lookup` routes phone/pan/ifsc/din/cin correctly | ☐ |
| 14 | Router | mislabelled identifier re-classified | ☐ |
| 15 | Router | ambiguous/no-id → disambiguation | ☐ |
| 16 | Router | money-ish question never fires a money endpoint | ☐ |
| 17 | Consent | mint token via `/api/mcp/consent/authorize` | ☐ |
| 18 | Consent | token single-use / path-scoped / account-scoped / expiry | ☐ |
| 19 | Consent | legacy `consent:"Y"` works (migration) | ☐ |
| 20 | Consent | no consent → rejected, not charged | ☐ |
| 21 | Async | `due_diligence_person_start` → running + job_id fast | ☐ |
| 22 | Async | `check_job` polls to complete; free; account-scoped | ☐ |
| 23 | Masking | standard key masks PII (`_masked:true`) | ☐ |
| 24 | Masking | raw key returns unmasked | ☐ |
| 25 | Safety | zero supplier/vendor names anywhere | ☐ |
| 26 | Safety | errors are scrubbed, no stack traces | ☐ |
| 27 | Resource | `outris://capabilities` readable + supplier-free | ☐ |
| 28 | Billing | ledger: credit metadata + refund on 5xx | ☐ |
| 29 | Billing | shadow canary: backend-billed, ledger frozen, no double-bill | ☐ |

---

## 13. Known limitations

- **Aadhaar OKYC (OTP) is parked** — not exposed for now (kept simple). No OTP
  tools in the surface.
- **Consent enforcement is in migration** — `consent:"Y"` still works until the
  portal consent screen ships and `allow_legacy_consent_y` is flipped off.
- **SSO billing (`shadow`) is canary-gated** — off by default; only the emails in
  `MCP_SHADOW_EMAILS` (with a portal JWT) route through it.
- **Old tool names are gone** — any integration pinned to `get_name`/
  `get_identity_profile`/`check_breaches`/etc. must repoint to the new tools.
- **Long `depth=full` / `due_diligence` calls** are slower by design; use the
  async pattern for due-diligence.

---

## 14. Reporting bugs

For each finding, capture:
- The **full JSON-RPC request** (redact your token) and the **full response**.
- Tool name, arguments, and which auth type (mcp_ key vs portal JWT).
- Expected vs actual, and the **severity** (P0 for any §9 safety failure).
- Timestamp + your account email (for correlating server logs).

File in the repo issue tracker: `outris-dev-org/outris-identity-mcp`.
