"""Restore pinned public panel inputs from local repository files, offline."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parents[1]
DEFAULT_DESTINATION = HERE / "work" / "source_panel"
DEFAULT_MANIFEST = HERE / "source_manifest.json"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Artifact path must be a nonempty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Artifact path escapes its declared root")
    return path


def _safe_path(root, relative):
    root = Path(root).resolve()
    parts = _relative(relative).parts
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not accepted as artifact paths")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Artifact path escapes its declared root")
    return current


def _check_bytes(data, descriptor, label):
    size, checksum = descriptor["bytes"], descriptor["sha256"]
    if type(size) is not int or size < 0 or not isinstance(checksum, str) or len(checksum) != 64:
        raise ValueError("Invalid byte-count or SHA-256 descriptor: " + label)
    if len(data) != size or sha256(data) != checksum:
        raise ValueError("Artifact byte count or SHA-256 differs: " + label)


def _read_verified(repo, descriptor):
    relative = descriptor["repository_path"]
    path = _safe_path(repo, relative)
    if not path.is_file():
        raise ValueError("Required repository artifact is not a regular file: " + relative)
    data = path.read_bytes()
    _check_bytes(data, descriptor, relative)
    return data


def _write_unchanged(destination, relative, data):
    path = _safe_path(destination, relative)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError("Existing destination differs; refusing overwrite: " + relative)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def restore_sources(repo=DEFAULT_REPO, destination=DEFAULT_DESTINATION, manifest_path=DEFAULT_MANIFEST):
    """Verify and restore all declared artifacts; perform no network requests.

    Source files are immutable copies. Matching destination files are reused;
    conflicts, symlinks, non-regular archive members, and unsafe paths fail.
    """
    repo, destination, manifest_path = Path(repo).resolve(), Path(destination).resolve(), Path(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    members, direct = manifest["members"], manifest["direct_files"]
    if manifest["artifact_count"] != len(members) + len(direct):
        raise ValueError("Declared source artifact count differs")
    destinations = [item["destination"] for item in members + direct]
    if len(destinations) != len(set(destinations)):
        raise ValueError("Duplicate source artifact destination")
    for relative in destinations:
        _relative(relative)
    frozen = json.loads(_read_verified(repo, manifest["frozen_manifest"]))
    original_members = {item["path"]: item for item in frozen["members"]}
    archives = {item["repository_path"]: item for item in manifest["archives"]}
    if len(archives) != len(manifest["archives"]):
        raise ValueError("Duplicate declared repository archive")
    # Each member remains tied to the independently pinned R3 frozen index.
    for member in members:
        _relative(member["archive_member"])
        original = original_members.get(member["archive_member"])
        if original is None or any(member[name] != original[name] for name in ("bytes", "sha256")):
            raise ValueError("Member differs from the original frozen index")
        expected_archive = "revision_preparation/review_round3/" + original["archive"]
        if member["repository_archive"] != expected_archive or expected_archive not in archives:
            raise ValueError("Member archive differs from the original frozen index")
    source_archives = {"revision_preparation/review_round3/" + item["path"]: item for item in frozen["archives"]}
    verified_archive_bytes = {}
    for name, descriptor in archives.items():
        original = source_archives.get(name)
        if original is None or any(descriptor[key] != original[key] for key in ("bytes", "sha256")):
            raise ValueError("Archive differs from the original frozen index")
        verified_archive_bytes[name] = _read_verified(repo, descriptor)
    # Verify all direct files before writing any destination content.
    direct_data = [(item, _read_verified(repo, item)) for item in direct]
    written = []
    for name, descriptor in archives.items():
        expected = {item["archive_member"]: item for item in members if item["repository_archive"] == name}
        found = set()
        with tarfile.open(fileobj=io.BytesIO(verified_archive_bytes[name]), mode="r:gz") as archive:
            for archived in archive:
                if archived.name not in expected:
                    continue
                if archived.name in found or not archived.isfile():
                    raise ValueError("Selected archive member is duplicated or not a regular file")
                item = expected[archived.name]
                stream = archive.extractfile(archived)
                if stream is None:
                    raise ValueError("Cannot read selected archive member")
                data = stream.read()
                _check_bytes(data, item, archived.name)
                _write_unchanged(destination, item["destination"], data)
                written.append({"path": item["destination"], "bytes": len(data), "sha256": sha256(data)})
                found.add(archived.name)
        if found != set(expected):
            raise ValueError("Selected archive member is missing")
    for item, data in direct_data:
        _write_unchanged(destination, item["destination"], data)
        written.append({"path": item["destination"], "bytes": len(data), "sha256": sha256(data)})
    return {"analysis_package": manifest["analysis_package"], "source_commit": manifest["source_commit"],
            "source_manifest_sha256": sha256(manifest_bytes), "verified_archives": len(archives),
            "restored_artifacts": len(written), "artifacts": sorted(written, key=lambda item: item["path"]),
            "original_bytes_unchanged": True, "network_requests": 0, "model_calls_made": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = restore_sources(args.repo, args.destination, args.manifest)
    if args.report:
        data = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        _write_unchanged(args.report.parent, args.report.name, data)
    print(json.dumps({key: value for key, value in report.items() if key != "artifacts"}))


if __name__ == "__main__":
    main()
