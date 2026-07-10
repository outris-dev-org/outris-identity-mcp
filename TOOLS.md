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
| **find_contacts** | 3 | phone + consent | Skip-trace alternate phone numbers + current geocoded addresses. **Consent required.** |
| **due_diligence_person** | 5 | phone + consent (+name/PAN/DOB/…) | Full background check — PEP, sanctions, enforcement, cybercrime, breaches, directorships, adverse media. **Consent + premium; 40–70s.** |
| **investigate_email** | 2 | email | Trace the person behind an email — names, phones, addresses, breaches. |
| **resolve_company** | 3 | company name | Resolve a company to its CIN + GSTIN/MSME registrations. |
| **lookup_gst** | 2 | GSTIN | GST registration details (name, status, address). |
| **verify_pan** | 2 | PAN | Verify a PAN and return the holder's name/status/type. |
| **lookup_vehicle** | 2 | RC number | Vehicle + registered-owner details from a registration number. |
| **verify_bank_account** | 2 | account + IFSC | No-debit bank-account validation + holder name. **No money moved.** |

## Safety guarantees (enforced server-side, not by prompt)

- **No money movement.** Penny-drop / reverse-penny are **not exposed** as tools
  and are hard-blocked in the generic executor. `verify_bank_account` uses the
  no-debit path only.
- **Consent.** Consent-required tools reject the call unless `consent='Y'` is
  supplied; the model must collect real user consent, never fabricate it.
- **No supplier leakage.** Internal data-provider names are scrubbed from every
  response and error.
- **PII masking.** Accounts without `allow_raw_records` get PII (names, PAN,
  phone, email, Aadhaar, addresses) masked by default, on any response shape.

## Roadmap

- **Phase 2:** `smart_lookup(question, identifiers)` router for the long tail of
  ~100 endpoints + a `list_capabilities` discovery resource; catalog-derived
  money/consent flags; per-user backend keys.
- **Phase 3:** MCP elicitation for consent + Aadhaar OKYC OTP; async jobs for
  long-running tools; explicit (default-off) money tools if ever needed.
