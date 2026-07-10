# Outris Identity Tools

The MCP exposes a small, curated set of **intent tools** — one per common
identity/KYC journey — instead of a flat list of ~100 endpoints. Each tool is a
thin wrapper over an Outris backend orchestrator that fans out to multiple
sources internally, so you supply an identifier and a goal, and get a good
answer without choosing between dozens of low-level APIs.

| Tool | Credits | Input | What it does |
|------|---------|-------|--------------|
| **investigate_phone** | 3 | phone (+`depth`) | Who is behind a mobile — names, addresses, alternate phones, digital footprint. `depth=basic` (fast) or `full` (comprehensive). |
| **assess_fraud_risk** | 3 | phone | Composite fraud-risk profile — SIM age, revocation, SIM-swap, digital/financial exposure. |
| **find_contacts** | 3 | phone + `consent_token` | Skip-trace alternate phone numbers + current geocoded addresses. **Consent required.** |
| **due_diligence_person_start** | 5 | phone + `consent_token` (+name/PAN/DOB/…) | Full background check — PEP, sanctions, enforcement, cybercrime, breaches, directorships, adverse media. **Async (40–70s): returns a `job_id`, poll `check_job`.** Consent + premium. |
| **check_job** | 0 | job_id | Poll an async job (e.g. from `due_diligence_person_start`) until `complete`. Free. |
| **investigate_email** | 2 | email | Trace the person behind an email — names, phones, addresses, breaches. |
| **resolve_company** | 3 | company name | Resolve a company to its CIN + GSTIN/MSME registrations. |
| **lookup_gst** | 2 | GSTIN | GST registration details (name, status, address). |
| **verify_pan** | 2 | PAN | Verify a PAN and return the holder's name/status/type. |
| **lookup_vehicle** | 2 | RC number | Vehicle + registered-owner details from a registration number. |
| **verify_bank_account** | 2 | account + IFSC | No-debit bank-account validation + holder name. **No money moved.** |
| **smart_lookup** | 3 | question + any identifier(s) | The long-tail router — ask a natural-language question and pass the identifier(s) you have (phone/email/PAN/GSTIN/CIN/DIN/UAN/IFSC/RC/VPA/UDIN/company name); it picks the right lookup or short sequence. |

> **OTP-based tools (Aadhaar OKYC) are parked for now** to keep the surface simple.
> The code (`tools/aadhaar.py`) stays on disk — uncomment its import in
> `tools/intent_tools.py` to re-enable `aadhaar_okyc_init` / `aadhaar_okyc_verify`.

**Discovery resource:** `outris://capabilities` (MCP resource) lists every
long-tail lookup `smart_lookup` can reach and the identifier each needs — read it
on demand instead of loading dozens of tool schemas.

## Safety guarantees (enforced server-side, not by prompt)

- **No money movement.** Penny-drop / reverse-penny are **not exposed** as tools
  and are hard-blocked in the generic executor. `verify_bank_account` uses the
  no-debit path only.
- **Consent (Phase 3).** Consent-required tools accept only a server-issued,
  human-gated `consent_token` (from portal.outris.com/mcp) — the model cannot
  fabricate it. A legacy `consent='Y'` is honoured only while
  `allow_legacy_consent_y` is true (migration), then disabled.
- **No supplier leakage.** Internal data-provider names are scrubbed from every
  response and error.
- **PII masking.** Accounts without `allow_raw_records` get PII (names, PAN,
  phone, email, Aadhaar, addresses) masked by default, on any response shape.

## Roadmap

- **Phase 2 (done):** `smart_lookup` router + `outris://capabilities` resource;
  money/consent flags consolidated into a single in-repo catalog
  (`capability_catalog.py`) that the executor's guards derive from.
- **Phase 2 follow-up:** generate `capability_catalog.py` from a client-safe
  projection of number-lookup's `endpoint_catalog.py` (fetched at runtime) so the
  ultimate source of truth is the backend; per-user backend keys; expand the
  catalog to the full endpoint set.
- **Phase 3 (done):** server-issued consent-token handshake (real MCP elicitation
  is impossible on the stateless transport + unsupported by the Claude.ai client);
  async jobs (`due_diligence_person_start` + `check_job`) for long-running tools.
  Aadhaar OKYC (OTP) 2-step tool is built but **parked** (no OTP APIs for now).
- **Phase 3 go-live:** apply `phase3_consent_and_jobs.sql` to the shared Postgres,
  ship the portal consent screen (→ `POST /api/mcp/consent/authorize`), then flip
  `allow_legacy_consent_y` to false to enforce token-only consent.
