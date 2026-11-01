"""Run stored analyses, live audits, report comparisons, and network exports."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics.engine import Engine  # noqa: E402
from backend.config import sqlite_database_path  # noqa: E402


def _json_bytes(data) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _write_output(content: bytes, output: Path | None) -> int:
    if output is None:
        sys.stdout.buffer.write(content)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        print(f"Saved {output}")
    return 0


def cmd_bundle(output: Path, report_a: str, report_b: str, *, database: Path | None = None,
               limit: int = 300, top: int = 20) -> int:
    """Create a checksummed research export directory without changing the database."""
    from analytics import networks
    from analytics.graph_analysis import graph_download
    from analytics.research import compare_reports, quality_audit

    database = sqlite_database_path() if database is None else database.expanduser().resolve()
    output = output.expanduser().absolute()
    if output.exists():
        raise ValueError(f"Output directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".ipcc-research-", dir=output.parent))
    input_folder = Path(tempfile.mkdtemp(prefix=".ipcc-input-", dir=output.parent))
    snapshot = input_folder / database.name
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
        comparison = compare_reports(report_a, report_b, db=snapshot, top=top)
        (temporary / "comparison.json").write_bytes(_json_bytes(comparison))
        (temporary / "quality-audit.json").write_bytes(_json_bytes(quality_audit(db=snapshot)))
        folder = temporary / "networks"
        folder.mkdir()
        for kind in networks.KINDS:
            data = networks.build(kind, db=snapshot, limit=limit)
            (folder / f"{kind}.json").write_bytes(_json_bytes(data))
            for fmt in ("graphml", "gexf", "csv"):
                content, _, extension = graph_download(data, fmt)
                (folder / f"{kind}.{extension}").write_bytes(content)
        with snapshot.open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        files = [{"path": str(path.relative_to(temporary)), "bytes": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in sorted(temporary.rglob("*"))
                 if path.is_file() and not path.name.startswith("._")]
        manifest = {
            "schema_version": "1.0", "source": "IPCC Literature Database",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": {"file": database.name, "bytes": snapshot.stat().st_size,
                         "sha256": digest.hexdigest()},
            "input_consistency": "All analyses read one transaction-consistent SQLite backup. The digest identifies that input.",
            "parameters": {"report_a": report_a, "report_b": report_b,
                           "edge_limit": limit, "top": top},
            "files": files,
        }
        (temporary / "manifest.json").write_bytes(_json_bytes(manifest))
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(input_folder, ignore_errors=True)
    print(f"Saved {len(files)} analysis files and manifest to {output}")
    return 0


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("refresh", help="Refresh stored analyses and insights")
    sub.add_parser("list", help="List stored analyses")
    sub.add_parser("show", help="Print the latest stored analysis").add_argument("analysis_type")
    audit = sub.add_parser("audit", help="Run live data-quality checks")
    compare = sub.add_parser("compare", help="Compare two report literature cohorts")
    network = sub.add_parser("network", help="Export a network with metrics and provenance")
    bundle = sub.add_parser("bundle", help="Create checksummed comparison, audit, and network exports")
    for command in (audit, compare, network, bundle):
        command.add_argument("--database", type=Path, help="SQLite file; default: DATABASE_URL")
        command.add_argument("--output", type=Path, required=command is bundle,
                             help="Output file, or output directory for bundle; default: stdout")
    audit.add_argument("--sample-limit", type=int, default=10)
    for command in (compare, bundle):
        command.add_argument("report_a")
        command.add_argument("report_b")
        command.add_argument("--top", type=int, default=20)
    for command in (network, bundle):
        command.add_argument("--limit", type=int, default=300)
    network.add_argument("kind", choices=("paper-chapter", "chapter-chapter", "report-report",
                                         "journal-chapter", "author-author"))
    network.add_argument("--min-weight", type=float)
    network.add_argument("--format", choices=("json", "graphml", "gexf", "csv"), default="json")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "refresh"):
            return cmd_refresh()
        if args.command == "list":
            return cmd_list()
        if args.command == "show":
            return cmd_show(args.analysis_type)
        if args.command == "bundle":
            return cmd_bundle(args.output, args.report_a, args.report_b, database=args.database,
                              limit=args.limit, top=args.top)
        if args.command == "audit":
            from analytics.research import quality_audit
            content = _json_bytes(quality_audit(db=args.database, sample_limit=args.sample_limit))
        elif args.command == "compare":
            from analytics.research import compare_reports
            content = _json_bytes(compare_reports(args.report_a, args.report_b, db=args.database, top=args.top))
        else:
            from analytics import networks
            from analytics.graph_analysis import graph_download
            data = networks.build(args.kind, db=args.database, min_weight=args.min_weight, limit=args.limit)
            content = _json_bytes(data) if args.format == "json" else graph_download(data, args.format)[0]
        return _write_output(content, args.output)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
