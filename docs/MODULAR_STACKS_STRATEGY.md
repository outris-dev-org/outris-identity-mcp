# Outris TraceFlow MCP Modular Stacks & Architecture Strategy

This document details the architectural decisions, design patterns, domain categorisation, and client audit/metering mechanics for the Outris Model Context Protocol (MCP) server.

---

## 1. Executive Summary & Design Rationale

As Outris expanded from consumer phone intelligence into enterprise **Corporate KYB, MCA VPD Filings, Ultimate Beneficial Ownership (UBO), Collections Skip-Tracing, and Enforcement Compliance**, the MCP tool catalogue grew to 19+ distinct tools.

Exposing all 19 tools in a single monolithic schema introduced three critical problems:
1. **LLM Cognitive Overload & Routing Errors**: Passing 19+ tools into Claude 3.5 Haiku or Vertex Gemini causes domain cross-talk. When asked to verify a business, the model often hallucinated consumer phone tools (`investigate_phone`) instead of enterprise corporate tools (`resolve_company`, `fetch_company_filings`).
2. **Token Latency & Cost Inefficiency**: 19 detailed JSON Schemas consume ~4,500 input tokens *per turn*. In an agentic loop running 3 iterations, 13,500+ tokens were consumed just on system schemas.
3. **Vertex AI Agent Builder Constraints**: Google Cloud Vertex AI Agent Builder imposes strict tool limits (typically capping actions at 30 tools and rejecting schemas with unresolved component references).

### The Solution: Modular Tool Stacks
Instead of a single monolithic MCP server, Outris MCP supports **Domain-Specific Stacks**. Stacks can be served individually as dedicated micro-MCPs, filtered dynamically at runtime over HTTP/SSE, or queried collectively in a unified mode.

---

## 2. Stack Categorisation & Tool Mapping

The catalogue is partitioned into 5 focused stacks, plus a unified stack:

| Stack ID | Display Name | Tool Count | Core Capabilities & Tools |
| :--- | :--- | :--- | :--- |
| `kyb` | **KYB & Corporate Intelligence** | 7 | Company resolution (`resolve_company`), MCA VPD document filing pack (`fetch_company_filings`), Beneficial ownership tree (`lookup_beneficial_ownership`), GSTIN verification (`lookup_gst`), PAN verification (`verify_pan`), Bank account penny-drop (`verify_bank_account`), Unified court enforcement (`search_unified_enforcement`). |
| `ubo` | **Ultimate Beneficial Ownership** | 5 | UBO multi-tier unwinding (`lookup_beneficial_ownership`), Company resolution (`resolve_company`), VPD filings (`fetch_company_filings`), GSTIN intelligence (`lookup_gst`), Unified court enforcement (`search_unified_enforcement`). |
| `collections` | **Collections & Skip-Trace** | 5 | Skip-trace orchestrator (`run_collections_investigation`), Alternate phone discovery (`get_alternate_phones`), Contact resolution (`find_contacts`), Address resolution (`get_address`), Identity profiling (`get_identity_profile`). |
| `fraud` | **Fraud & Risk Investigation** | 6 | Fraud risk score (`assess_fraud_risk`), Phone investigation (`investigate_phone`), Social footprint (`check_online_platforms`), E-commerce activity (`check_digital_commerce_activity`), Data breach check (`check_breaches`), Email risk (`investigate_email`). |
| `compliance` | **AML & Legal Compliance** | 4 | Unified court enforcement (`search_unified_enforcement`), PAN verification (`verify_pan`), Bank verification (`verify_bank_account`), Corporate filings (`fetch_company_filings`). |
| `all` | **All Capabilities (Unified)** | 19 | Complete tool registry with persona-based dynamic filtering. |

---

## 3. Modular Multi-Transport Implementation

The server supports stack selection across all supported MCP transports without requiring code duplication:

1. **CLI / STDIO Launch (for Desktop Claude, Cursor, Windsurf):**
   ```bash
   # Launch exclusively with KYB tools
   python -m mcp_server --stdio --stack kyb

   # Launch Collections stack
   python -m mcp_server --stdio --stack collections
   ```

2. **HTTP Streamable / SSE Transports (Cloud & Web):**
   Clients can filter on the fly via query parameters or custom headers:
   - **Query Parameter:** `POST /http?stack=kyb` or `GET /sse?stack=ubo`
   - **HTTP Header:** `X-MCP-Stack: collections`

3. **Vertex AI Agent Builder OpenAPI Specs:**
   The `scripts/generate_openapi.py` utility generates 7 standalone OpenAPI 3.0 specifications:
   - `vertex_openapi_all.json`
   - `vertex_openapi_kyb.json`
   - `vertex_openapi_ubo.json`
   - `vertex_openapi_collections.json`
   - `vertex_openapi_fraud.json`
   - `vertex_openapi_compliance.json`
   - `vertex_openapi_identity.json`
   *Note: Security schemes in `components.securitySchemes` are automatically stripped in Vertex specs to comply with Agent Builder validation rules.*

---

## 4. Client Tracking, Auditability & API Key Governance

Every MCP invocation—whether via the AI Playground chat, manual tool runner, or external developer client—is strictly attributed, metered, and logged.

### 4.1 Database Audit Architecture
MCP uses PostgreSQL tables in the `mcp` schema:

1. **`mcp.user_accounts`**:
   - Stores tenant balance (`credits_balance`), tier (`credits_tier`), persona access level (`persona`), and key hashes (`mcp_key_hash`).
   - Links to `public.api_keys` via `user_email` for master API permissions (e.g. `allow_raw_records`).

2. **`mcp.ai_chat_log`**:
   Logs every AI agent chat turn:
   - `user_account_id` & `user_email`: Identifies exactly which client/agent initiated the chat.
   - `model`: Model used (e.g., `claude-3-5-haiku-20241022`).
   - `input_tokens` & `output_tokens`: Exact LLM token consumption.
   - `tools_used`: Array of tools invoked during the agentic reasoning loop.
   - `credits_used`: Total TraceFlow credits charged.
   - `duration_ms`: End-to-end latency.

3. **`mcp.user_tool_calls`**:
   Atomic log for every single tool execution:
   - `request_id`: UUID for distributed tracing.
   - `user_account_id`: Client identity.
   - `tool_name`: Exact tool executed.
   - `credits_cost` & `credits_charged`: Ledger audit.
   - `input_params`: Sanitized input arguments.
   - `latency_ms` & `success`: Backend SLA monitoring.

### 4.2 Authentication Modalities
- **Web Portal / AI Playground:** Uses the authenticated user's session JWT (`Authorization: Bearer <jwt>`). The backend extracts the client's verified email and binds execution to their `mcp.user_accounts` row.
- **Desktop Clients (Claude Desktop / Windsurf / Cursor):** Uses tenant MCP keys (`mcp_live_...`) passed as `Authorization: Bearer mcp_...`. Keys are hashed with SHA-256 and matched against `mcp.user_accounts.mcp_key_hash`.
- **Direct API Keys (`outp_...`):** Can be mapped to the tenant's primary account, allowing existing TraceFlow direct API customers to consume MCP without generating separate credentials.
