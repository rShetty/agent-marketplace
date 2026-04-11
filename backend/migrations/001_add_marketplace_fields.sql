-- Migration: Add marketplace fields to agents table
-- Date: 2026-04-11
-- Description: Adds is_public, marketplace_description, and pricing_model fields

-- Add marketplace visibility flag
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE;

-- Add marketplace description
ALTER TABLE agents ADD COLUMN IF NOT EXISTS marketplace_description TEXT;

-- Add pricing model (JSON)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS pricing_model TEXT; -- SQLite stores JSON as TEXT

-- Make owner_id NOT NULL (enforce human ownership)
-- Note: This requires existing NULL values to be updated first
-- UPDATE agents SET owner_id = (SELECT id FROM users LIMIT 1) WHERE owner_id IS NULL;
-- ALTER TABLE agents ALTER COLUMN owner_id SET NOT NULL; -- Not supported in SQLite directly

-- Create index on is_public for faster marketplace queries
CREATE INDEX IF NOT EXISTS idx_agents_is_public ON agents(is_public);

-- Create index on status for faster filtering
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

-- Create composite index for marketplace queries
CREATE INDEX IF NOT EXISTS idx_agents_marketplace ON agents(is_public, status);
