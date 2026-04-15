"""Migration 003 — add config_encrypted and openclaw_instance_id to agents table."""
import sqlite3
import os

DB_PATH = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/data/agent_marketplace.db")
# Strip the driver prefix to get a plain file path
DB_PATH = DB_PATH.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(agents)")
existing_cols = {row[1] for row in cursor.fetchall()}

new_cols = [
    ("config_encrypted", "TEXT"),
    ("openclaw_instance_id", "VARCHAR(36)"),
]

for col_name, col_type in new_cols:
    if col_name not in existing_cols:
        print(f"Adding column: {col_name}")
        cursor.execute(f"ALTER TABLE agents ADD COLUMN {col_name} {col_type}")
    else:
        print(f"Column already exists: {col_name}")

conn.commit()
conn.close()
print("Migration 003 complete.")
