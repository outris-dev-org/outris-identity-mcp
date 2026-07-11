"""
Phase-2 per-email canary tests (offline).

Run: python test_canary_billing.py

Proves the effective-billing-mode resolution: a JWT user in MCP_SHADOW_EMAILS is
routed to "shadow" even while the global mode is "ledger"; mcp_-key (no-JWT) users
are never shadowed; global shadow/sso applies to everyone; and the per-request
contextvar wins over the global setting.
"""
import mcp_server.core.config as cfg
from mcp_server.core.context import current_billing_mode

FAIL = []


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


class FS:
    def __init__(self, mode, emails):
        self.mcp_billing_mode = mode
        self.mcp_shadow_emails = emails


def set_settings(mode, emails):
    cfg.get_settings = lambda: FS(mode, emails)


from mcp_server.core.config import effective_billing_mode_for, get_effective_billing_mode

# --- global ledger + shadow list ---
set_settings("ledger", "saurabh@outris.com, tester@x.com")
check(effective_billing_mode_for("saurabh@outris.com", True) == "shadow",
      "JWT user in shadow list -> shadow (canary) while global is ledger")
check(effective_billing_mode_for("SAURABH@Outris.com ", True) == "shadow",
      "shadow-list match is case/space-insensitive")
check(effective_billing_mode_for("saurabh@outris.com", False) == "ledger",
      "no JWT (mcp_ key user) is NEVER shadowed")
check(effective_billing_mode_for("stranger@x.com", True) == "ledger",
      "JWT user NOT in shadow list stays ledger")

# --- global shadow applies to everyone ---
set_settings("shadow", "")
check(effective_billing_mode_for("anyone@x.com", True) == "shadow",
      "global shadow applies to all JWT users")

# --- contextvar precedence ---
set_settings("ledger", "")
check(get_effective_billing_mode() == "ledger", "no contextvar -> global setting (ledger)")
tok = current_billing_mode.set("shadow")
try:
    check(get_effective_billing_mode() == "shadow", "per-request contextvar wins over global setting")
finally:
    current_billing_mode.reset(tok)
check(get_effective_billing_mode() == "ledger", "contextvar reset -> back to global")

print("\n" + ("ALL PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
