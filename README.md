# Outris Identity MCP Server

Outris Identity is a Model Context Protocol (MCP) server that lets AI agents investigate phone numbers and emails - find linked identities, check platform registrations, and detect data breaches.

**Version 2.0** - Now with Streamable HTTP, SSE, and STDIO support! 🚀

## Features

- 🔍 **Identity Resolution:** Find names, emails, addresses linked to phone numbers
- 🌐 **Platform Checks:** Detect registration on 31+ platforms (India) + 3 global
- 🛒 **Commerce Activity:** Check ecommerce, travel, quick-commerce activity
- 🚨 **Breach Detection:** Check if phone/email appears in known breaches
- 🌍 **Global + India:** Full India coverage, partial global support
- 📡 **Multiple Transports:** Streamable HTTP (new), SSE (legacy), STDIO (local)
- 🔐 **Secure:** API key authentication, credit-based rate limiting
- 🚀 **Ready for Registry:** Meet all MCP official registry requirements

## Quick Start

### Option 1: Cloud Deployment (Fastest) ☁️

**Step 1:** Get API Key from [Outris Portal](https://portal.outris.com)

**Step 2:** Configure Claude Desktop

Edit `claude_desktop_config.json`:

**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "outris-identity": {
      "command": "npx",
      "args": [
        "-y", 
        "mcp-remote", 
        "https://mcp-server.outris.com/http",
        "--transport", 
        "streamable-http",
        "--header", 
        "Authorization=Bearer YOUR_API_KEY"
      ]
    }
  }
}
```

**Step 3:** Restart Claude and start investigating!

### Option 2: Local Installation 🏠

```bash
git clone https://github.com/outris/outris-identity-mcp.git
cd outris-identity-mcp

pip install -r requirements.txt
python -m mcp_server --http
# Server runs on http://localhost:8000
```

### Option 3: Docker 🐳

```bash
docker build -t outris-identity .
docker run -e OUTRIS_API_KEY="your_key" -p 8000:8000 outris-identity
```

See [SETUP.md](SETUP.md) for detailed configuration instructions.

## Available Tools & Modular Stacks

A curated set of **intent tools** — one per common identity/KYC/compliance journey — instead of a flat list of hundreds of raw endpoints. See [TOOLS.md](TOOLS.md) for details.

### Modular Stacks
You can launch or query a focused MCP surface tailored strictly for your product domain:
- **`kyb` (7 tools):** MCA VPD filings (AOC-4/MGT-7), UBO, GSTIN, PAN, Bank account validation, 58-body regulatory enforcement.
- **`ubo` (5 tools):** Beneficial ownership tree unravelling, regulatory sanctions, company profile, person due-diligence.
- **`collections` (6 tools):** Collections phone bundle, consent-gated alternate contacts/addresses, vehicle RC, caller ID.
- **`fraud` (5 tools):** Carrier SIM telemetry (age/swap/port), phone/email investigation, digital footprint.
- **`compliance` (6 tools):** Unified enforcement (SEBI/MCA/IBBI/CBI), UBO, PEP/sanctions, law enforcement dossiers.

| Tool | Credits | Category | Use Case |
|------|---------|----------|----------|
| **investigate_phone** | 3 | phone | Identity bundle behind a mobile — names, addresses, alt-phones (`depth` basic/full) |
| **assess_fraud_risk** | 3 | phone | Composite carrier fraud-risk profile (SIM age, swap, port, risk exposure) |
| **find_contacts** | 3 | phone | Skip-trace alt phones + geocoded addresses (consent token required) |
| **due_diligence_person_start** / **check_job** | 5 / 0 | screening | Full background check — PEP/sanctions/enforcement/adverse media (consent, async 40–70s) |
| **investigate_email** | 2 | email | Trace the person behind an email (names, phones, breaches) |
| **resolve_company** | 3 | business | Company name → CIN + GSTIN/MSME registrations |
| **fetch_company_filings** | 3 | business | MCA VPD filings: AOC-4 balance sheet, MGT-7 annual return, P&L, charges |
| **lookup_beneficial_ownership** | 5 | compliance | Ultimate Beneficial Ownership (UBO) unravelling >=10% natural persons |
| **lookup_gst** | 2 | business | GST registration details from a GSTIN |
| **verify_pan** | 2 | identity | Verify PAN and return legal name/status/type |
| **lookup_vehicle** | 2 | vehicle | Vehicle + registered owner from an RC number |
| **verify_bank_account** | 2 | banking | Pennyless bank-account validation via NPCI (no money moved) |
| **run_collections_intelligence** | 3 | collections | Macro debt-recovery phone intelligence bundle |
| **run_law_enforcement_intel** | 5 | law_enforcement | Law Enforcement Intelligence (LEI) privileged dossier |
| **check_caller_id** | 2 | identity | Multi-app caller ID aggregation (tags, names, emails) |
| **search_unified_enforcement** | 5 | business | Screen across 58 regulatory bodies (SEBI, MCA, IBBI, CBI, SFIO) |
| **run_digital_footprint** | 3 | osint | Deep digital footprint mapping web presence & breach exposures |
| **smart_lookup** | 3 | router | Long-tail router — NL question + any identifier → the right lookup/sequence |

## Transports & Stack Launch

| Transport | Endpoint / Command | Modular Stack Support |
|-----------|--------------------|-----------------------|
| **Streamable HTTP** | `POST /http` or `POST /stacks/{stack}/http` | Query `?stack=kyb` or path `/stacks/kyb/http` |
| **SSE** | `GET /sse` or `GET /stacks/{stack}/sse` | Query `?stack=ubo` or path `/stacks/ubo/sse` |
| **STDIO (CLI)** | `python -m mcp_server --stdio` | Flag `--stack kyb` or `--stack ubo` |
| **Vertex AI Agent Builder** | `GET /api/vertex-agent/openapi.json` | Query `?stack=kyb` or `vertex_openapi_kyb.json` |

## Documentation

- 📖 [Setup Guide](SETUP.md) - Installation & configuration
- 🔧 [Tool Reference](TOOLS.md) - Complete tool documentation
- 🏗️ [Architecture](docs/ARCHITECTURE.md) - System design & transports
- 💳 [Credit System](docs/CREDIT_SYSTEM.md) - Pricing & quotas

## Example Usage

```bash
# Test the server
curl https://mcp-server.outris.com/health

# List available tools
curl https://mcp-server.outris.com/tools

# Execute a tool
curl -X POST "https://mcp-server.outris.com/http" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"tools/call",
    "params":{
      "name":"check_online_platforms",
      "arguments":{"identifier":"+919876543210"}
    }
  }'
```

## MCP Registry Listing

This server is registered on the official MCP registry: https://registry.modelcontextprotocol.io/

- **Type:** Streamable HTTP + SSE + STDIO
- **Auth:** Bearer token (API key)
- **Region:** Global + India optimized

## License

MIT - See [LICENSE](LICENSE) file for details

## Support & Community

- 📝 [Issues](https://github.com/outris/outris-identity-mcp/issues)
- 💬 [Discussions](https://github.com/outris/outris-identity-mcp/discussions)
- 📧 [Email Support](mailto:support@outris.com)
- 🌐 [Documentation](https://docs.outris.com)

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

**Built with:** Official MCP SDK • FastAPI • PostgreSQL • Neon

**Maintained by:** Outris Technologies
