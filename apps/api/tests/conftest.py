import os

# app/db.py builds a module-level engine at import time. Tests use their own
# in-memory engine; point the unused module engine at SQLite so importing the
# app doesn't require the Postgres driver.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
