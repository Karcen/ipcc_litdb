"""Runtime configuration.

Environment variables configure the database, dashboard, and optional AI queries.
See .env.example for the available settings.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env", override=False)

# ------------------------------------------------------------------ database
# SQLite works out of the box.  For the team deployment point this at
# PostgreSQL, e.g. postgresql+psycopg://user:pw@host:5432/ipcc_litdb
DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{REPO_ROOT / 'database' / 'ipcc_litdb.sqlite'}"
)


def sqlite_database_path(database_url: str | None = None) -> Path:
    """Resolve the configured SQLite file for analytics and read-only queries."""
    url = make_url(DATABASE_URL if database_url is None else database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("This feature requires a SQLite database. Set DATABASE_URL to a SQLite file.")
    if not url.database or url.database == ":memory:":
        raise ValueError("This feature requires a SQLite file database.")
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


# Keep SQLAlchemy and direct SQLite readers on the same repository-relative file.
_database_url = make_url(DATABASE_URL)
if _database_url.get_backend_name() == "sqlite" and _database_url.database not in (None, "", ":memory:"):
    DATABASE_URL = _database_url.set(database=str(sqlite_database_path())).render_as_string(hide_password=False)

# --------------------------------------------------------------- frontend/app
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", REPO_ROOT / "frontend"))
DATA_PROCESSED = Path(os.environ.get("DATA_PROCESSED", REPO_ROOT / "data" / "processed"))
HOST = os.environ.get("API_HOST", "127.0.0.1")
PORT = int(os.environ.get("API_PORT", "8077"))

# --------------------------------------------------------------- write access
# ALLOW_WRITES controls the audited manual-review endpoints.
ALLOW_WRITES = os.environ.get("ALLOW_WRITES", "true").lower() == "true"
CHANGED_BY = os.environ.get("CHANGED_BY", "dashboard-user")
MAX_PAGE_SIZE = int(os.environ.get("MAX_PAGE_SIZE", "200"))

# ------------------------------------------------------------------- AI layer
# Set AI_API_KEY to enable optional AI queries.
AI_ENABLED = os.environ.get("AI_ENABLED", "true").lower() == "true"
AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai_compatible")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_TIMEOUT = int(os.environ.get("AI_TIMEOUT", "60"))

# ----------------------------------------------------------- SQL safety limits
SQL_ROW_LIMIT = int(os.environ.get("SQL_ROW_LIMIT", "500"))
SQL_TIMEOUT_SECONDS = int(os.environ.get("SQL_TIMEOUT_SECONDS", "15"))
