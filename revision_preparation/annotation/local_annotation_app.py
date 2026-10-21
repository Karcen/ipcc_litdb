#!/usr/bin/env python3
"""Run the annotation workspace at http://127.0.0.1:8765 using Python stdlib."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlparse

from annotation_tools import (
    ROOT, ID_FIELDS, OCCURRENCE_FIELDS, canonical_templates, digest,
    is_ai_annotator, load_bundle, validate, write_csv, write_json,
)

AI_ANNOTATOR = "Codex (AI-assisted initial review)"


def atomic_json(path, obj):
    temp = path.with_suffix(path.suffix+".tmp")
    write_json(temp, obj)
    temp.replace(path)


class AnnotationStore:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.manifest, papers, occurrences, strata, self.summary = load_bundle(self.root)
        self.id_rows, self.occurrence_rows = canonical_templates(papers, occurrences, strata)
        self.ids = {r["paper_id"]: r for r in self.id_rows}
        self.occurrences = {r["occurrence_id"]: r for r in self.occurrence_rows}
        self.lock = threading.RLock()
        self.work = self.root/"work"
        self.work.mkdir(exist_ok=True)
        self.path = self.work/"annotations.json"
        fingerprint = digest(self.root/"input_manifest.json")
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            if self.state.get("source_manifest_sha256") != fingerprint:
                raise ValueError("Work file belongs to different source inputs")
            if set(self.state.get("ids", {})) != set(self.ids) or set(self.state.get("occurrences", {})) != set(self.occurrences):
                raise ValueError("Work file keys differ from the frozen source sample")
            for labels in self.state["ids"].values():
                if set(labels) != set(ID_FIELDS):
                    raise ValueError("Work file ID annotation fields are incomplete")
                self.check_fields(labels, ID_FIELDS)
            for labels in self.state["occurrences"].values():
                if set(labels) != set(OCCURRENCE_FIELDS):
                    raise ValueError("Work file occurrence annotation fields are incomplete")
                self.check_fields(labels, OCCURRENCE_FIELDS)
        else:
            self.state = {"schema_version": 1, "source_manifest_sha256": fingerprint,
                "ids": {k: dict.fromkeys(ID_FIELDS, "") for k in self.ids},
                "occurrences": {k: dict.fromkeys(OCCURRENCE_FIELDS, "") for k in self.occurrences},
                "updated_at": None}
        self.persist()

    @staticmethod
    def check_fields(labels, allowed):
        if not isinstance(labels, dict) or set(labels)-set(allowed):
            raise ValueError("Only annotation fields may be saved")
        for field, value in labels.items():
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError("Annotation values must be text of at most 20,000 characters")
            if allowed[field] is not None and value and value not in allowed[field]:
                raise ValueError(f"Invalid label for {field}")

    def persist(self):
        atomic_json(self.path, self.state)
        for filename, rows, labels, key in [
            ("id_annotations.csv", self.id_rows, self.state["ids"], "paper_id"),
            ("occurrence_annotations.csv", self.occurrence_rows, self.state["occurrences"], "occurrence_id"),
        ]:
            destination = self.work/filename
            temp = destination.with_suffix(".csv.tmp")
            write_csv(temp, [{**r, **labels[r[key]]} for r in rows])
            temp.replace(destination)

    def integrity(self):
        return validate(self.root, self.work/"id_annotations.csv", self.work/"occurrence_annotations.csv")[0]

    def dataset(self):
        with self.lock:
            return {"manifest": self.manifest, "papers": self.id_rows, "occurrences": self.occurrence_rows,
                "labels": self.state, "schema": {"id_fields": ID_FIELDS, "occurrence_fields": OCCURRENCE_FIELDS},
                "integrity": self.integrity()}

    def ai_review(self):
        """Read proposals separately; their presence changes no annotation."""
        path = self.root/"ai_review"/"proposals.json"
        if not path.is_file():
            return {"available": False, "human_validated": False, "cases": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict)
                or data.get("schema_version") != "ipcc-ai-initial-review-1"
                or data.get("review_type") != "ai_assisted_initial_review"
                or data.get("human_validated") is not False
                or not isinstance(data.get("cases"), list)):
            raise ValueError("AI proposal provenance is invalid; no proposals were loaded")
        seen, seen_occurrences = set(), set()
        for case in data["cases"]:
            if not isinstance(case, dict):
                raise ValueError("Each AI proposal must be an object")
            pid = str(case.get("paper_id", ""))
            if pid not in self.ids or pid in seen:
                raise ValueError("AI proposals contain an unknown or duplicate sampled ID")
            seen.add(pid)
            if case.get("review_complete") != "no":
                raise ValueError(f"AI proposal for ID {pid} must remain incomplete")
            if not is_ai_annotator(case.get("annotator")):
                raise ValueError(f"AI proposal for ID {pid} must identify its AI annotator")
            if not isinstance(case.get("occurrences"), list):
                raise ValueError(f"AI proposal for ID {pid} is missing its occurrence list")
            for field in ("identity_label", "shared_link_label", "missed_merge_search_status", "missed_merge_result"):
                if case.get(field, "") not in ("", *ID_FIELDS[field]):
                    raise ValueError(f"Invalid AI proposal field {field} for ID {pid}")
            expected = {oid for oid, row in self.occurrences.items() if row["paper_id"] == pid}
            actual = set()
            for row in case["occurrences"]:
                if not isinstance(row, dict):
                    raise ValueError("Each AI occurrence proposal must be an object")
                oid = row.get("occurrence_id")
                if oid not in expected or oid in actual or oid in seen_occurrences:
                    raise ValueError(f"AI proposal has an unknown, misplaced or duplicate occurrence for ID {pid}")
                actual.add(oid); seen_occurrences.add(oid)
                if row.get("metadata_judgment", "") not in ("", *OCCURRENCE_FIELDS["metadata_judgment"]):
                    raise ValueError(f"Invalid AI metadata proposal for occurrence {oid}")
            if actual != expected:
                raise ValueError(f"AI proposal occurrence keys differ from the frozen sample for ID {pid}")
        if seen != set(self.ids) or seen_occurrences != set(self.occurrences):
            raise ValueError("AI proposal keys differ from the complete frozen ID and occurrence sets")
        return {**data, "available": True, "human_validated": False,
                "proposal_sha256": digest(path), "case_count": len(seen)}

    def use_ai_draft(self, paper_id):
        """Copy blank fields only, leaving all review completion to the user."""
        pid = str(paper_id)
        proposals = self.ai_review()
        if not proposals["available"]:
            raise ValueError("No AI proposals are available")
        case = next((c for c in proposals["cases"] if str(c["paper_id"]) == pid), None)
        if case is None:
            raise ValueError("Unknown sample paper ID")

        def proposal_text(value):
            if isinstance(value, list):
                return " | ".join(str(v) for v in value)
            return str(value) if value is not None else ""

        with self.lock:
            current = self.state["ids"][pid]
            if current["review_complete"] == "yes":
                raise ValueError("This ID already has a completed review; its annotations were left unchanged")
            labels = {field: proposal_text(case[field]) for field in ID_FIELDS
                      if field in case and not current[field]}
            if not current["annotator"]:
                labels["annotator"] = AI_ANNOTATOR
            if not current["review_complete"]:
                labels["review_complete"] = "no"
            # A proposal may describe its offline search, but cannot complete a
            # human search by importing it into the annotation form.
            if labels.get("missed_merge_search_status") == "complete":
                labels["missed_merge_search_status"] = "incomplete"
            occurrence_labels = {}
            for row in case["occurrences"]:
                oid = row["occurrence_id"]
                existing = self.state["occurrences"][oid]
                fields = {field: proposal_text(row[field]) for field in OCCURRENCE_FIELDS
                          if field in row and not existing[field]}
                if not existing["annotator"]:
                    fields["annotator"] = AI_ANNOTATOR
                occurrence_labels[oid] = fields
            result = self.save({"paper_id": pid, "id_labels": labels,
                                "occurrence_labels": occurrence_labels}, origin="ai_proposal_draft")
            return {**result, "paper_id": pid, "id_labels": dict(self.state["ids"][pid]),
                    "occurrence_labels": {oid: dict(self.state["occurrences"][oid]) for oid in occurrence_labels},
                    "draft_only": True, "human_validated": False}

    def save(self, payload, *, origin=None):
        pid = str(payload.get("paper_id", ""))
        if pid not in self.ids:
            raise ValueError("Unknown sample paper ID")
        labels = payload.get("id_labels", {})
        self.check_fields(labels, ID_FIELDS)
        occurrences = payload.get("occurrence_labels", {})
        if not isinstance(occurrences, dict):
            raise ValueError("Occurrence annotations must be keyed by occurrence ID")
        for oid, fields in occurrences.items():
            if oid not in self.occurrences or self.occurrences[oid]["paper_id"] != pid:
                raise ValueError("Occurrence is outside this sampled ID")
            self.check_fields(fields, OCCURRENCE_FIELDS)
        with self.lock:
            effective = {**self.state["ids"][pid], **labels}
            ai_occurrences = any(is_ai_annotator({**self.state["occurrences"][oid], **occurrences.get(oid, {})}.get("annotator"))
                                 for oid, row in self.occurrences.items() if row["paper_id"] == pid)
            if ((effective.get("review_complete") == "yes" or effective.get("missed_merge_search_status") == "complete")
                    and (is_ai_annotator(effective.get("annotator")) or ai_occurrences)):
                raise ValueError("AI suggestions cannot complete a review or search. Check the sources, then change the annotator to the actual reviewer's name or code for the ID and its entries.")
            changes = []
            for group, key, new_labels in [("ids", pid, labels), *[("occurrences", oid, v) for oid, v in occurrences.items()]]:
                for field, value in new_labels.items():
                    old = self.state[group][key].get(field, "")
                    if old != value:
                        changes.append({"group": group, "key": key, "field": field, "old": old, "new": value})
                        self.state[group][key][field] = value
            if changes:
                stamp = datetime.now(timezone.utc).isoformat()
                self.state["updated_at"] = stamp
                with (self.work/"change_log.jsonl").open("a", encoding="utf-8") as stream:
                    record = {"at": stamp, "paper_id": pid, "changes": changes}
                    if origin:
                        record["origin"] = origin
                    stream.write(json.dumps(record, ensure_ascii=False)+"\n")
                self.persist()
            return {"saved": True, "updated_at": self.state["updated_at"], "integrity": self.integrity()}


def locate_database():
    for ancestor in ROOT.parents:
        candidate = ancestor/"database"/"ipcc_litdb.sqlite"
        if candidate.is_file():
            return candidate
    return None


def search_database(database, query, mode="title", publication_year=""):
    if not database:
        raise ValueError("Start the app with --db to search the full database")
    query = query.strip()
    minimum = 2 if mode == "author_year" or (mode == "raw_reference" and publication_year) else 3
    if len(query) < minimum:
        raise ValueError(f"Enter at least {minimum} characters")
    if mode == "raw_reference":
        condition, parameters = "lower(r.raw_reference) LIKE ? ESCAPE '\\'", ["%"+query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")+"%"]
        if publication_year:
            if not publication_year.isdigit() or len(publication_year) != 4:
                raise ValueError("Use a four-digit year from the original reference")
            condition += " AND r.raw_reference LIKE ?"; parameters.append("%"+publication_year+"%")
        query_sql = f"SELECT DISTINCT p.paper_id,p.title_best,p.year_best,p.doi_norm,p.authors_best FROM references_occurrence r JOIN papers p ON p.paper_id=r.paper_id WHERE {condition} ORDER BY p.paper_id LIMIT 26"
        basis = "Preserved original raw_reference strings; optional year is also searched in raw text"
    elif mode in {"doi", "preferred_doi"}:
        query = query.lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:").strip()
        condition, parameters = "lower(doi_norm)=?", [query]
        query_sql = f"SELECT paper_id,title_best,year_best,doi_norm,authors_best FROM papers WHERE {condition} ORDER BY paper_id LIMIT 26"
        basis = "Preferred DOI metadata, not DOI text in the original reference"
    elif mode == "title":
        condition, parameters = "lower(title_best) LIKE ? ESCAPE '\\'", ["%"+query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")+"%"]
        query_sql = f"SELECT paper_id,title_best,year_best,doi_norm,authors_best FROM papers WHERE {condition} ORDER BY paper_id LIMIT 26"
        basis = "Preferred title metadata"
    elif mode == "author_year":
        condition, parameters = "lower(authors_best) LIKE ?", ["%"+query.lower().replace("%", "").replace("_", "")+"%"]
        if publication_year:
            if not publication_year.isdigit() or len(publication_year) != 4:
                raise ValueError("Use a four-digit publication year")
            condition += " AND year_best=?"; parameters.append(publication_year)
        query_sql = f"SELECT paper_id,title_best,year_best,doi_norm,authors_best FROM papers WHERE {condition} ORDER BY paper_id LIMIT 26"
        basis = "Preferred author/year metadata"
    else:
        raise ValueError("Choose DOI, title or author-year search")
    with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(r) for r in connection.execute(query_sql, parameters)]
        truncated = len(rows) > 25
        rows = rows[:25]
        for row in rows:
            occurrences = [dict(r) for r in connection.execute("""
                SELECT r.occurrence_id,r.raw_reference,r.match_status,c.report_id,c.chapter_label,
                       s.file_url AS source_file_url,r.source_page
                FROM references_occurrence r JOIN chapters c ON c.chapter_id=r.chapter_id
                LEFT JOIN sources s ON s.source_id=c.source_id
                WHERE r.paper_id=? ORDER BY r.occurrence_id LIMIT 11
            """, (row["paper_id"],))]
            row["raw_entries_truncated"] = len(occurrences) > 10
            row["occurrences"] = occurrences[:10]
    return {"results": rows, "results_truncated": truncated,
        "scope": "Read-only saved database search; up to 25 IDs and 10 raw occurrences per ID",
        "query": query, "mode": mode, "publication_year": publication_year,"search_basis":basis}


def make_server(store, database=None, port=8765):
    token = secrets.token_hex(24)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, status=200, mime="application/json; charset=utf-8", attachment=None):
            body = json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            if not self.local_host():
                return
            url = urlparse(self.path)
            try:
                if url.path == "/":
                    self.send((ROOT/"app.html").read_bytes(), mime="text/html; charset=utf-8")
                elif url.path == "/api/dataset":
                    data = store.dataset(); data["token"] = token; data["database_search_available"] = database is not None
                    self.send(data)
                elif url.path == "/api/integrity":
                    self.send(store.integrity())
                elif url.path == "/api/ai-review":
                    self.send(store.ai_review())
                elif url.path == "/api/search":
                    q = parse_qs(url.query)
                    self.send(search_database(database, q.get("q", [""])[0], q.get("mode", ["title"])[0], q.get("year", [""])[0]))
                elif url.path in {"/export/id_annotations.csv", "/export/occurrence_annotations.csv", "/export/annotations.json"}:
                    name = url.path.rsplit("/", 1)[-1]
                    self.send((store.work/name).read_bytes(), mime="application/octet-stream", attachment=name)
                elif url.path == "/rules":
                    self.send((ROOT/"annotation_rules.txt").read_bytes(), mime="text/plain; charset=utf-8")
                else:
                    self.send({"error": "Page not found"}, 404)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.send({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.local_host():
                return
            if self.path not in {"/api/save", "/api/ai-review/draft"}:
                self.send({"error": "Page not found"}, 404); return
            if self.headers.get("X-Annotation-Token") != token:
                self.send({"error": "Invalid local annotation token"}, 403); return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1000000:
                self.send({"error": "Save payload is too large"}, 413); return
            try:
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/ai-review/draft":
                    self.send(store.use_ai_draft(payload.get("paper_id", "")))
                else:
                    self.send(store.save(payload))
            except (ValueError, KeyError, TypeError) as exc:
                self.send({"error": str(exc)}, 400)

        def local_host(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send({"error": "Use the local annotation address"}, 403)
                return False
            return True
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Directory holding frozen inputs and work files")
    parser.add_argument("--db", type=Path, default=locate_database(), help="Saved SQLite snapshot, opened read-only")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    store = AnnotationStore(args.root)
    if args.db and digest(args.db) != store.manifest["database_sha256"]:
        raise ValueError("Search database SHA-256 differs from the annotation source snapshot")
    server = make_server(store, args.db, args.port)
    print(f"Annotation workspace: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Autosave: {store.work}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
