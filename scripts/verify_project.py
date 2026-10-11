#!/usr/bin/env python3
"""Check the packaged snapshot, database, English interface, and repository layout.

    python scripts/verify_project.py
    python scripts/verify_project.py --require-api
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

from restore_database import verify_compressed_snapshot

REPO = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", "paper", "analysis-reproduction"}
AUTHORED = {"backend", "analytics", "scripts", "tests", "frontend", ".github", "reproducibility"}
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".yml", ".yaml", ".sql", ".sh", ".example", ".json"}


def package_files():
    for directory, subdirectories, filenames in os.walk(REPO):
        subdirectories[:] = [name for name in subdirectories if name not in IGNORED and not name.startswith("._")]
        for name in filenames:
            if name.startswith("._"):
                continue
            path = Path(directory) / name
            yield path, path.relative_to(REPO)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-api", action="store_true", help="Fail if the dashboard is not running")
    parser.add_argument("--database", type=Path, default=REPO / "database" / "ipcc_litdb.sqlite")
    args = parser.parse_args()
    passed = 0
    failures = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed
        if condition:
            passed += 1
        else:
            failures.append(name)
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))

    print("Snapshot and database")
    manifest = None
    try:
        manifest = verify_compressed_snapshot(REPO / "database")
        check("Compressed snapshot matches its SHA-256 and size", True)
        check("Snapshot fits GitHub's 100 MiB file limit", manifest["compressed"]["bytes"] < 100 * 1024 * 1024)
    except (OSError, ValueError) as error:
        check("Compressed snapshot matches its SHA-256 and size", False, str(error))
    if not args.database.is_file():
        check("Working database exists", False, "Run bash scripts/import_data.sh")
    else:
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            def scalar(query: str):
                return connection.execute(query).fetchone()[0]

            check("SQLite integrity check", connection.execute("PRAGMA quick_check").fetchall() == [("ok",)])
            check("Foreign keys resolve", connection.execute("PRAGMA foreign_key_check").fetchall() == [])
            if manifest is not None:
                for label, table in (("reports", "assessment_reports"), ("working_groups", "working_groups"),
                                     ("chapters", "chapters"), ("papers", "papers"),
                                     ("reference_occurrences", "references_occurrence")):
                    actual = scalar(f'SELECT COUNT(*) FROM "{table}"')
                    check(f"{label} matches the snapshot count", actual == manifest["counts"][label], str(actual))
                actual = scalar("SELECT COUNT(*) FROM references_occurrence WHERE match_status='unmatched'")
                check("Unmatched occurrences are retained", actual == manifest["counts"]["unmatched_occurrences"], str(actual))
                actual = scalar("SELECT COUNT(*) FROM references_occurrence WHERE match_status IN ('matched_doi','matched_title')")
                check("Matched occurrences are retained", actual == manifest["counts"]["wos_matched_occurrences"], str(actual))
            check("Occurrence counts agree with paper totals", scalar("SELECT SUM(n_occurrences) FROM papers") == scalar("SELECT COUNT(*) FROM references_occurrence"))
            check("Every occurrence has a paper", scalar("SELECT COUNT(*) FROM references_occurrence WHERE paper_id IS NULL") == 0)
            check("Papers have citation occurrences", scalar("SELECT COUNT(*) FROM papers p WHERE NOT EXISTS (SELECT 1 FROM references_occurrence r WHERE r.paper_id=p.paper_id)") == 0)
            check("Occurrence identifiers are unique", scalar("SELECT COUNT(*) FROM (SELECT occurrence_id FROM references_occurrence GROUP BY occurrence_id HAVING COUNT(*)>1)") == 0)
            check("Normalized DOI identifiers are unique", scalar("SELECT COUNT(*) FROM (SELECT doi_norm FROM papers WHERE doi_norm IS NOT NULL AND doi_norm<>'' GROUP BY doi_norm HAVING COUNT(*)>1)") == 0)
            check("Analysis results include provenance", scalar("SELECT COUNT(*) FROM analysis_results WHERE query IS NOT NULL OR parameters IS NOT NULL") > 0)
            check("Stored insights are available", scalar("SELECT COUNT(*) FROM insights") > 0)

    print("Repository and interface")
    files = list(package_files())
    markdown = sorted(str(relative) for path, relative in files if path.suffix.lower() == ".md")
    check("Project guide uses a single README", markdown == ["README.md"], ", ".join(markdown))
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    check("README includes Quick Start", "quick start" in readme.lower())
    locales = sorted(path.name for path in (REPO / "frontend" / "locales").glob("*.json") if not path.name.startswith("._"))
    check("English locale is the only interface locale", locales == ["en.json"], ", ".join(locales))
    english = json.loads((REPO / "frontend" / "locales" / "en.json").read_text(encoding="utf-8"))
    check("English interface strings are present", len(english) > 150)
    html = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (REPO / "frontend" / "app.js").read_text(encoding="utf-8")
    css = (REPO / "frontend" / "styles.css").read_text(encoding="utf-8")
    check("Frontend references its assets", "/static/app.js" in html and "/static/styles.css" in html)
    check("Frontend references bundled chart libraries", "/static/vendor/echarts.min.js" in html and "/static/vendor/cytoscape.min.js" in html)
    check("Theme switcher and both palettes are present", 'id="theme"' in html and '[data-theme="dark"]' in css and '[data-theme="light"]' in css)
    check("Statistics come from the API", not re.search(r"\b(?:160029|113591|91832|68197|61264)\b", html + js))
    untranslated = []
    credentials = []
    private_paths = []
    secret_pattern = re.compile(r"sk-[A-Za-z0-9_-]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
    for path, relative in files:
        if path.suffix not in TEXT_SUFFIXES or "vendor" in relative.parts:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if relative.parts[0] in AUTHORED or path.suffix == ".md":
            if re.search(r"[\u4e00-\u9fff]", content):
                untranslated.append(str(relative))
        if secret_pattern.search(content):
            credentials.append(str(relative))
        if re.search(r"/(?:Users|Volumes)/[^/\s]+/", content):
            private_paths.append(str(relative))
    check("Authored text is English", not untranslated, ", ".join(untranslated))
    check("No API keys or private-key blocks detected in package text", not credentials, ", ".join(credentials))
    check("Package text uses portable paths", not private_paths, ", ".join(private_paths))
    check("Local configuration and SQLite files are git-ignored", all(pattern in (REPO / ".gitignore").read_text(encoding="utf-8") for pattern in (".env", ".venv/", "*.sqlite")))

    print("Dashboard API")
    api = os.environ.get("API_BASE", "http://127.0.0.1:8077").rstrip("/")

    def get(route: str):
        with urllib.request.urlopen(api + route, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    try:
        get("/api/health")
    except (OSError, ValueError, urllib.error.URLError):
        if args.require_api:
            check("Dashboard API is reachable", False, "Start bash scripts/dev.sh")
        else:
            print("[SKIP] Dashboard API is not running; use --require-api to require it.")
    else:
        for route in ("/api/statistics", "/api/reports", "/api/chapters?report_id=AR6&limit=1000",
                      "/api/papers?page=1&size=25", "/api/metrics", "/api/bi/query?metric=reference_count&dimension=report",
                      "/api/analysis", "/api/insights?lang=en", "/api/networks", "/api/networks/chapter-chapter",
                      "/api/data-quality", "/api/data-quality/audit?sample_limit=2",
                      "/api/research/compare?report_a=AR5&report_b=AR6&top=2",
                      "/api/duplicates?limit=25", "/api/unresolved?page=1&size=25",
                      "/api/collection-progress", "/api/papers/1", "/api/papers/1/citations", "/api/ai/status", "/api/changelog"):
            try:
                status, payload = get(route)
                check(route, status == 200 and isinstance(payload, (dict, list)))
            except (OSError, ValueError, urllib.error.URLError) as error:
                check(route, False, str(error))
    print(f"Result: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
