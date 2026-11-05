"""Deterministic backup manifest construction and validation."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath


class ManifestError(ValueError):
    """Raised when a source or remote manifest is unsafe or inconsistent."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(source: Path) -> dict[str, object]:
    if not source.is_dir():
        raise ManifestError(f"source is not a directory: {source}")
    files = []
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return {"version": 1, "files": files}


def safe_destination(root: Path, relative: str) -> Path:
    """Resolve one POSIX manifest path and prove it stays below the restore root."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ManifestError(f"unsafe manifest path: {relative!r}")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise ManifestError(f"unsafe manifest path: {relative!r}")
    if ":" in posix.parts[0]:
        raise ManifestError(f"unsafe manifest path: {relative!r}")
    root_resolved = root.resolve()
    destination = root_resolved.joinpath(*posix.parts).resolve()
    if destination == root_resolved or root_resolved not in destination.parents:
        raise ManifestError(f"manifest path escapes restore root: {relative!r}")
    return destination


def validate_manifest(manifest: object, restore_root: Path | None = None) -> list[dict[str, object]]:
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ManifestError("manifest must contain version 1 and a files list")
    files: list[dict[str, object]] = manifest["files"]  # type: ignore[assignment]
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ManifestError("manifest file entry must be an object")
        path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if not isinstance(path, str) or path in seen:
            raise ManifestError(f"duplicate or invalid manifest path: {path!r}")
        if not isinstance(size, int) or size < 0:
            raise ManifestError(f"invalid size for {path}")
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ManifestError(f"invalid SHA-256 for {path}")
        if restore_root is not None:
            safe_destination(restore_root, path)
        seen.add(path)
    return files

