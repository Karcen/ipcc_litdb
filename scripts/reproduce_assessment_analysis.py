#!/usr/bin/env python3
"""Reproduce assessment-report analyses from the immutable distributed snapshot.

One occurrence is occurrence_id; one linked work is paper_id. Distinct-work
cohorts and occurrence-weighted ages have separate denominators. The scripts
write to a new output directory and never edit the input database.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
CODE = REPO / "reproducibility"
PROGRAMS = (
    "analyse_corpus.py", "deepen_analysis.py", "revision_identity.py",
    "revision_age_networks.py", "revision_outlets.py",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def compare_csv(actual: Path, reference: Path) -> None:
    """Compare complete tables, allowing only floating point rounding error."""
    with actual.open(newline="", encoding="utf-8") as stream:
        actual_rows = list(csv.reader(stream))
    with reference.open(newline="", encoding="utf-8") as stream:
        reference_rows = list(csv.reader(stream))
    if len(actual_rows) != len(reference_rows):
        raise ValueError(f"{reference.name}: row count differs")
    for row_number, (observed, expected) in enumerate(zip(actual_rows, reference_rows), 1):
        if len(observed) != len(expected):
            raise ValueError(f"{reference.name}: column count differs at row {row_number}")
        for column, (value, target) in enumerate(zip(observed, expected), 1):
            if value == target:
                continue
            try:
                equal = math.isclose(float(value), float(target), rel_tol=1e-10, abs_tol=1e-10)
            except ValueError:
                equal = False
            if row_number == 1 or not equal:
                raise ValueError(f"{reference.name}: value differs at row {row_number}, column {column}")


def run(database: Path, output: Path, check_reference: bool) -> dict:
    import numpy as np

    database = database.expanduser().resolve()
    output = output.expanduser().resolve()
    reference = json.loads((CODE / "reference" / "manifest.json").read_text(encoding="utf-8"))
    before = digest(database)
    if before != reference["database_sha256"]:
        raise ValueError("Input SHA-256 differs from the distributed analysis snapshot")
    if output.exists():
        raise ValueError("Choose a new output directory; existing outputs are preserved")
    if np.__version__ != reference["numpy_version"]:
        raise ValueError("Install the pinned analysis environment: pip install -r requirements-analysis.txt")
    output.mkdir(parents=True)
    for name in PROGRAMS:
        shutil.copy2(CODE / name, output / name)
    seed = str(reference["seed"])
    jobs = [
        ["analyse_corpus.py", "--database", str(database)],
        ["deepen_analysis.py"],
        ["revision_identity.py", "--database", str(database), "--seed", seed],
        ["revision_age_networks.py", "--database", str(database), "--replicates", str(reference["age_network_replicates"]), "--seed", seed],
        ["revision_outlets.py", "--database", str(database), "--replicates", str(reference["outlet_replicates"]), "--seed", seed],
    ]
    for number, arguments in enumerate(jobs, 1):
        print(f"[{number}/{len(jobs)}] {arguments[0]}", flush=True)
        log_path = output / f"step_{number}.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, *arguments], cwd=output, stdout=log, stderr=subprocess.STDOUT)
        if process.returncode:
            print(log_path.read_text(encoding="utf-8")[-8000:], file=sys.stderr)
            raise subprocess.CalledProcessError(process.returncode, arguments)
    after = digest(database)
    if after != before:
        raise ValueError("Input database changed during analysis")
    comparisons = []
    if check_reference:
        for item in reference["tables"]:
            target = CODE / "reference" / item["filename"]
            if digest(target) != item["sha256"]:
                raise ValueError(f"Reference table digest differs: {target.name}")
            actual = output / "results" / target.name
            compare_csv(actual, target)
            comparisons.append({"filename": target.name, "passed": True, "generated_sha256": digest(actual)})
    generated = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith("._"):
            generated.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)})
    record = {
        "database_sha256_before": before, "database_sha256_after": after,
        "database_unchanged": True, "python_version": sys.version.split()[0], "numpy_version": np.__version__,
        "seed": reference["seed"], "age_network_replicates": reference["age_network_replicates"],
        "outlet_replicates": reference["outlet_replicates"], "checked_reference": check_reference,
        "table_comparisons": comparisons, "all_requested_checks_passed": True,
        "code_sha256": {name: digest(CODE / name) for name in PROGRAMS}, "outputs": generated,
    }
    (output / "reproduction.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"database_unchanged": True, "reference_tables_passed": len(comparisons), "outputs": len(generated)}, indent=2))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=REPO / "database" / "ipcc_litdb.sqlite")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for tables, source extracts, logs and QC")
    parser.add_argument("--check-reference", action="store_true", help="Compare all registered result tables with the published snapshot")
    arguments = parser.parse_args()
    try:
        run(arguments.database, arguments.output_dir, arguments.check_reference)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Reproduction failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
