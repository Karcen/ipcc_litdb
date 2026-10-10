"""CLI:  python -m analytics.cli refresh | show | list"""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics.engine import Engine  # noqa: E402
from backend.config import sqlite_database_path  # noqa: E402


def cmd_refresh() -> int:
    eng = Engine()
    p = eng.persist()
    print(f"run_id={p['run_id']}  results={len(p['results'])}  "
          f"insights={len(p['insights'])}  errors={p['errors']}  {p['start']} → {p['end']}")
    for i in p["insights"]:
        print(f"  [{i['severity']:7s}] {i['title_en']}")
    return 0 if p["errors"] == 0 else 1


def cmd_list() -> int:
    with closing(sqlite3.connect(sqlite_database_path().as_uri() + "?mode=ro", uri=True)) as con:
        for r in con.execute("""SELECT analysis_id, analysis_type, created_at, software_version
                                FROM analysis_results ORDER BY analysis_id"""):
            print(f"  {r[0]:4d}  {r[1]:32s} {r[2]}  {r[3]}")
    return 0


def cmd_show(atype: str) -> int:
    with closing(sqlite3.connect(sqlite_database_path().as_uri() + "?mode=ro", uri=True)) as con:
        r = con.execute("""SELECT output, query, parameters FROM analysis_results
                           WHERE analysis_type=? ORDER BY analysis_id DESC LIMIT 1""", (atype,)).fetchone()
    if not r:
        print(f"no stored result for {atype}")
        return 1
    print(json.dumps(json.loads(r[0]), indent=2, ensure_ascii=False)[:6000])
    print("\n--- query ---\n" + (r[1] or ""))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if cmd == "refresh":
        raise SystemExit(cmd_refresh())
    if cmd == "list":
        raise SystemExit(cmd_list())
    if cmd == "show" and len(sys.argv) > 2:
        raise SystemExit(cmd_show(sys.argv[2]))
    print(__doc__)
    raise SystemExit(2)
