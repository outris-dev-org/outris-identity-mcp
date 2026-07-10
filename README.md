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

## Available Tools

A small, curated set of **intent tools** — one per common identity/KYC journey —
instead of a flat list of ~100 endpoints. See [TOOLS.md](TOOLS.md) for details.

| Tool | Credits | Use Case |
|------|---------|----------|
| **investigate_phone** | 3 | Who is behind a mobile — names, addresses, alt-phones, footprint (`depth` basic/full) |
| **assess_fraud_risk** | 3 | Composite fraud-risk profile for a phone |
| **find_contacts** | 3 | Skip-trace alt phones + geocoded addresses (consent) |
| **due_diligence_person** | 5 | Background check — PEP/sanctions/enforcement/adverse media (consent, premium) |
| **investigate_email** | 2 | Trace the person behind an email |
| **resolve_company** | 3 | Company name → CIN + GSTIN/MSME |
| **lookup_gst** | 2 | GST registration details from a GSTIN |
| **verify_pan** | 2 | Verify a PAN, return holder details |
| **lookup_vehicle** | 2 | Vehicle + registered owner from an RC number |
| **verify_bank_account** | 2 | No-debit bank-account validation (no money moved) |
| **smart_lookup** | 3 | Long-tail router — NL question + any identifier → the right lookup/sequence |

## Transports

| Transport | URL | Use Case | Status |
|-----------|-----|----------|--------|
| **Streamable HTTP** | `POST /http` | Cloud, Claude Desktop | ✅ PRIMARY |
| **SSE** | `GET /sse` | Legacy clients, proxies | ⚠️ Supported |
| **STDIO** | `python -m mcp_server` | Local CLI, direct integration | 🟢 Native |

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
