# Outris Identity Tools & Modular Stacks

The MCP exposes a curated set of **intent tools** — macro-orchestrators over Outris backend services — instead of a flat list of hundreds of raw endpoints. You supply an identifier and an objective, and the server orchestrates internal fan-out, PII protection, and compliance gates.

---

## 1. Tool Catalog

| Tool | Credits | Category | Input | What it does |
|------|---------|----------|-------|--------------|
| **investigate_phone** | 3 | phone | `phone` (+`depth`) | Identity bundle behind an Indian mobile: names, addresses, alternate phones, digital footprint. `depth=basic` (fast) or `full` (comprehensive). |
| **assess_fraud_risk** | 3 | phone | `phone` (+`detailed`) | Composite carrier fraud-risk profile: SIM age, revocation, SIM-swap / port telemetry, digital/financial risk exposure. |
| **find_contacts** | 3 | phone | `phone` + `consent_token` | Skip-trace alternate phone numbers + current geocoded addresses. **Consent required.** |
| **due_diligence_person_start** | 5 | screening | `phone` + `consent_token` (+`name`/`pan`/`dob`/…) | Full due-diligence report: PEP, sanctions, enforcement, cybercrime complaints, breach exposure, directorships, adverse media. **Async (40–70s): returns `job_id`, poll `check_job`.** |
| **check_job** | 0 | jobs | `job_id` | Poll an async job until `complete`. Free of credit charge. |
| **investigate_email** | 2 | email | `email` | Trace the subject behind an email: names, phones, addresses, breach history. |
| **resolve_company** | 3 | business | `company_name` | Resolve an Indian entity by name to its CIN, GSTIN, and MSME registrations. |
| **fetch_company_filings** | 3 | business | `cin` (+`callback_url`) | Official MCA View-Public-Documents (VPD) pack for unlisted/private companies. Balance sheets (AOC-4), annual returns (MGT-7), P&L, charges, and incorporation documents. Async fulfillment. |
| **lookup_beneficial_ownership** | 5 | compliance | `identifier` (CIN/PAN/Name) | Ultimate Beneficial Ownership (UBO) screen. Unravels multi-tier holding company trees to identify natural persons holding >=10% economic interest or control under RBI/PMLA guidelines. |
| **lookup_gst** | 2 | business | `gstin` | GSTIN registration details, trade/legal names, status, and principal/additional places of business. |
| **verify_pan** | 2 | identity | `pan` | Verify an Indian PAN and return holder's legal name, status, and PAN category. |
| **lookup_vehicle** | 2 | vehicle | `rc_number` | Vehicle registration (RC), make/model, fuel class, and registered owner. |
| **verify_bank_account** | 2 | banking | `account_number` + `ifsc` | Pennyless bank account validation + account holder name matching via NPCI. **No money moved.** |
| **run_collections_intelligence** | 3 | collections | `phone` | Macro debt-collection phone intelligence bundle. Amalgamates contactability, assets, and lifestyle signals. |
| **run_law_enforcement_intel** | 5 | law_enforcement | `phone` + `reason` | Law Enforcement Intelligence (LEI) dossier. Highly privileged macro-orchestrator combining collections, prefill identity, and caller-ID aggregation. |
| **check_caller_id** | 2 | identity | `phone` | Aggregate a phone number's identity across consumer caller-ID applications: tags, names, associated emails, and social handles. |
| **search_unified_enforcement** | 5 | business | `identifier` (CIN/PAN/DIN/Name) | Screen against the unified regulatory corpus (58 regulatory bodies, 1.3M+ records): SEBI defaulters, MCA disqualified directors, IBBI insolvency, SFIO, CBI. |
| **run_digital_footprint** | 3 | osint | `query` (phone/email/name) | Digital footprint analysis mapping online presence, breach leaks, and public exposure. |
| **smart_lookup** | 3 | router | `question` + identifiers | Natural-language query router across the entire Outris long-tail capability catalog. |

> **OTP-based Aadhaar OKYC tools are parked** to maintain surface simplicity. The module stays on disk (`tools/aadhaar.py`) and can be re-enabled at any time.

---

## 2. Modular Tool Stacks

Enterprises frequently wish to launch or deploy an MCP instance tailored strictly for a single business unit (e.g. KYB onboarding only, or Skip-Tracing/Collections only) without exposing irrelevant tools.

Outris MCP provides 5 pre-packaged modular stacks:

### 1. `kyb` — Corporate Verification & Filings Stack (7 tools)
For B2B onboarding, merchant underwriting, and vendor verification:
- `resolve_company` (Name to CIN/GSTIN)
- `fetch_company_filings` (MCA VPD filings: AOC-4, MGT-7, balance sheets)
- `lookup_beneficial_ownership` (UBO tree & controlling persons)
- `lookup_gst` (GSTIN verification & filings)
- `verify_pan` (Business/director PAN)
- `verify_bank_account` (Pennyless vendor bank validation)
- `search_unified_enforcement` (58 regulatory bodies screening)

### 2. `ubo` — Beneficial Ownership & Governance Stack (5 tools)
For AML/CFT compliance, ultimate beneficial ownership unravelling, and sanctions checks:
- `lookup_beneficial_ownership` (Multi-tier holding trees, >=10% natural persons)
- `search_unified_enforcement` (Regulatory enforcement & defaulter list)
- `resolve_company` (Entity identity & registrations)
- `due_diligence_person_start` (Deep background check on key executives/promoters)
- `check_job` (Poll background check status)

### 3. `collections` — Collections & Skip-Tracing Stack (6 tools)
For debt recovery, loan default skip-tracing, and asset contactability:
- `run_collections_intelligence` (Comprehensive recovery intelligence)
- `find_contacts` (Alternate numbers + geocoded addresses with consent)
- `lookup_vehicle` (Vehicle RC & asset verification)
- `verify_bank_account` (Bank account validation)
- `investigate_phone` (Phone intelligence & identity profile)
- `check_caller_id` (Caller-ID aggregator)

### 4. `fraud` — Fraud & Risk Intelligence Stack (5 tools)
For consumer onboarding risk, synthetic identity detection, and account takeover prevention:
- `assess_fraud_risk` (SIM swap/port telemetry, SIM age, network status)
- `investigate_phone` (Phone ownership & identity)
- `investigate_email` (Email footprint & known data breaches)
- `check_caller_id` (Caller-ID aggregation & spam tags)
- `run_digital_footprint` (Deep online presence & breach indicators)

### 5. `compliance` — AML & Regulatory Compliance Stack (6 tools)
For regulatory investigations and high-risk AML due diligence:
- `search_unified_enforcement` (58 regulatory bodies, 1.3M+ records)
- `lookup_beneficial_ownership` (UBO structure & ownership)
- `due_diligence_person_start` (PEP, sanctions, adverse media)
- `check_job` (Poll async due diligence)
- `run_law_enforcement_intel` (Privileged law enforcement dossier)
- `verify_pan` (PAN verification)

---

## 3. How to Launch / Connect to a Modular Stack

### A. CLI / STDIO Transport (e.g. Claude Desktop, Cursor, Local Dev)
Run with the `--stack` flag:
```bash
# Launch specifically for KYB stack
python -m mcp_server --stdio --stack kyb

# Launch specifically for UBO stack
python -m mcp_server --stdio --stack ubo

# List all available stacks
python -m mcp_server --list-stacks
```

In your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "outris-kyb": {
      "command": "python",
      "args": ["-m", "mcp_server", "--stdio", "--stack", "kyb"],
      "env": {
        "DATABASE_URL": "postgresql://...",
        "BACKEND_API_KEY": "..."
      }
    }
  }
}
```

### B. Streamable HTTP / Cloud Deployments
Point your MCP client directly to the stack-specific endpoint:
- **KYB Stack:** `POST https://mcp-server.outris.com/stacks/kyb/http`
- **UBO Stack:** `POST https://mcp-server.outris.com/stacks/ubo/http`
- **Collections Stack:** `POST https://mcp-server.outris.com/stacks/collections/http`
- **Fraud Stack:** `POST https://mcp-server.outris.com/stacks/fraud/http`

Or use the standard endpoint with a query parameter or header:
- `POST https://mcp-server.outris.com/http?stack=kyb`
- Header: `X-MCP-Stack: kyb`

### C. Google Cloud Vertex AI Agent Builder
Import the modular OpenAPI 3.0 specification tailored to your stack:
- Full catalog: `vertex_openapi.json`
- KYB stack only: `vertex_openapi_kyb.json` (or `GET /api/vertex-agent/stacks/kyb/openapi.json`)
- UBO stack only: `vertex_openapi_ubo.json` (or `GET /api/vertex-agent/stacks/ubo/openapi.json`)
- Collections stack only: `vertex_openapi_collections.json`
- Fraud stack only: `vertex_openapi_fraud.json`

*(Note: All exported OpenAPI specs have `components.securitySchemes` omitted to ensure seamless Vertex AI Agent Builder import).*

---

## 4. Safety & Governance Guarantees

- **No Money Movement:** Money-moving paths (penny-drop / reverse-penny) are blocked and strictly rejected by the executor.
- **DPDPA Consent:** Sensitive skip-trace and personal screening endpoints require a server-issued `consent_token`.
- **Supplier Scrubbing:** Zero vendor / upstream data partner names appear in any tool description, response, or error payload.
- **PII Posture:** Accounts without `allow_raw_records` automatically have personal PII masked.
