"""Freeze the existing probability-ID sample for a blinded Codex comparison.

This command selects no cases from model results and creates no classifications.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Refuse to overwrite an existing comparison input: " + str(path))
    path.write_bytes(raw)


def prepare(work, per_shard=56):
    if type(per_shard) is not int or per_shard < 1:
        raise ValueError("Positive integer shard size required")
    inputs = work / "inputs"
    roles_path = inputs / "frames/sample_roles.json"
    frame_path = inputs / "frames/blind_all.json"
    roles = json.loads(roles_path.read_text())
    frame = json.loads(frame_path.read_text())["cases"]
    role_ids = [r["case_id"] for r in roles]
    ids = {c["case_id"] for c in frame}
    if len(role_ids) != len(set(role_ids)) or len(ids) != len(frame) or not set(role_ids) <= ids:
        raise ValueError("Missing or repeated case IDs")
    selected = sorted((c for c in frame if c["case_id"] in set(role_ids)), key=lambda c: c["case_id"])
    if len(selected) != 331 or sum(len(c["records"]) for c in selected) != 801:
        raise ValueError("Frozen 331-ID/801-occurrence sample differs")
    out = work / "codex_comparison_v1"
    shards = []
    for start in range(0, len(selected), per_shard):
        path = out / "inputs" / ("sample_shard_%02d.json" % (len(shards) + 1))
        cases = selected[start:start + per_shard]
        freeze(path, {"cases": cases})
        shards.append({"path": path.relative_to(work).as_posix(), "sha256": digest(path),
                       "cases": len(cases), "records": sum(len(c["records"]) for c in cases)})
    freeze(out / "inputs/sample_blind_331.json", {"cases": selected})
    manifest = {"analysis_package": "1.5.0", "protocol": "codex_blind_comparison_v1",
                "seed": 20261011, "selection": "All existing 319 probability IDs and 12 targeted supplements; no model-result selection",
                "source_files": {roles_path.relative_to(work).as_posix(): digest(roles_path),
                                 frame_path.relative_to(work).as_posix(): digest(frame_path)},
                "cases": len(selected), "records": 801, "shards": shards,
                "reviewer": "Codex session model", "human_adjudication": False,
                "model_results_read_by_preparer": False, "classifications_generated": False}
    freeze(out / "sample_preparation_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--cases-per-shard", type=int, default=56)
    args = parser.parse_args()
    print(json.dumps(prepare(args.work_dir.resolve(), args.cases_per_shard), indent=2))
