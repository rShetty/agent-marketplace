# Database Migrations

This directory contains SQL migrations for the Hive database schema.

## Migration Files

- `001_add_marketplace_fields.sql` - Adds marketplace fields to agents table (is_public, marketplace_description, pricing_model)

## How to Apply Migrations

### Manual Application (SQLite)

```bash
# From backend directory
sqlite3 ../data/agent_marketplace.db < migrations/001_add_marketplace_fields.sql
```

### Production Recommendation

For production deployments, consider using a proper migration tool:

**Option 1: Alembic (Python)**
```bash
pip install alembic
alembic init alembic
alembic revision --autogenerate -m "Add marketplace fields"
alembic upgrade head
```

**Option 2: PostgreSQL + Flyway**
For production scale, migrate from SQLite to PostgreSQL and use Flyway for migrations.

## Migration Status

Run this query to check if migrations have been applied:

```sql
-- Check if marketplace fields exist
PRAGMA table_info(agents);

-- Check indexes
SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agents';
```

## Notes

- SQLite has limited ALTER TABLE support
- The `owner_id NOT NULL` constraint needs to be handled during table recreation
- For production, use PostgreSQL which has better ALTER TABLE support
- Indexes are created to optimize marketplace queries

## Future Migrations

When adding new migrations:
1. Create a new file: `00X_description.sql`
2. Document the changes
3. Test on a copy of the database first
4. Apply to production during low-traffic periods
