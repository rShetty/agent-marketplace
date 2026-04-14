-- Migration: Add columns added in code but missing from DB schema
-- Date: 2026-04-14

-- agents: api_key_prefix for O(1) key lookup
ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key_prefix TEXT;

-- agents: ready flag for delegation routing
ALTER TABLE agents ADD COLUMN IF NOT EXISTS ready BOOLEAN DEFAULT TRUE;

-- Back-fill api_key_prefix from existing api_key_hash rows (prefix stored separately going forward)
-- Existing rows won't have a real prefix, but they also have no api_key_hash that matches
-- the new bcrypt format — they'll need to re-register. Safe to leave NULL for now.

-- Create index on api_key_prefix for O(1) lookup
CREATE INDEX IF NOT EXISTS idx_agents_api_key_prefix ON agents(api_key_prefix);

-- transactions: platform fee ledger
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS platform_fee NUMERIC DEFAULT 0;

-- transactions: delegation chain depth
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS delegation_depth INTEGER DEFAULT 0;

-- transactions: structured result from executing agent
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS task_result TEXT; -- JSON stored as TEXT in SQLite

-- transactions: refund reason for reputation scoring
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS refund_reason TEXT;

-- transactions: originating human user (for full chain attribution)
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS originating_user_id TEXT;

-- transactions: session grouping for multi-step agent chains
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS session_id TEXT;
