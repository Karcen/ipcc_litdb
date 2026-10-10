#!/usr/bin/env python3
"""Launch the dashboard using environment variables and the repository .env."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or later is required.")
    from dotenv import load_dotenv
    from sqlalchemy.engine import make_url
    import uvicorn
    from restore_database import restore_database

    os.chdir(REPO)
    load_dotenv(REPO / ".env", override=False)
    database_url = os.environ.setdefault("DATABASE_URL", f"sqlite:///{REPO / 'database' / 'ipcc_litdb.sqlite'}")
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        restore_database(destination=Path(url.database))
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8077"))
    sys.path.insert(0, str(REPO / "backend"))
    print(f"Dashboard: http://{host}:{port}/", flush=True)
    uvicorn.run("app:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
