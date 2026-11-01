import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

DB = REPO / "database" / "ipcc_litdb.sqlite"


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def db_path() -> Path:
    if not DB.exists():
        pytest.skip("database not restored; run: python scripts/restore_database.py")
    return DB
