-- Run this migration to add the Persona column to the MCP accounts table
ALTER TABLE mcp.user_accounts ADD COLUMN IF NOT EXISTS persona VARCHAR(50) DEFAULT 'general';

-- Example updates for different clients
-- UPDATE mcp.user_accounts SET persona = 'fraud' WHERE user_email = 'fraud-team@company.com';
-- UPDATE mcp.user_accounts SET persona = 'collections' WHERE user_email = 'recovery@company.com';
