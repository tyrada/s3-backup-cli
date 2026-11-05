"""S3 upload, listing, and safe hash-verified restore operations."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from s3_backup.manifest import ManifestError, build_manifest, safe_destination, sha256_bytes, sha256_file, validate_manifest


class BackupVerificationError(RuntimeError):
    """Raised when restored content does not match its manifest."""


def _key(prefix: str, suffix: str) -> str:
    normalized = prefix.strip("/")
    return f"{normalized}/{suffix}" if normalized else suffix


def upload_backup(source: Path, bucket: str, prefix: str, client: Any, dry_run: bool = False) -> dict[str, int]:
    manifest = build_manifest(source)
    files = validate_manifest(manifest)
    uploaded = 0
    for entry in files:
        relative = str(entry["path"])
        key = _key(prefix, f"files/{relative}")
        if dry_run:
            print(f"DRY RUN upload {source / relative} -> s3://{bucket}/{key}")
            continue
        content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        client.upload_file(
            str(source / Path(*PurePosixPath(relative).parts)), bucket, key,
            ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": content_type},
        )
        uploaded += 1
    manifest_body = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    if dry_run:
        print(f"DRY RUN put manifest -> s3://{bucket}/{_key(prefix, 'manifest.json')}")
    else:
        # Publish the manifest last so readers do not see a new manifest before its files exist.
        client.put_object(
            Bucket=bucket, Key=_key(prefix, "manifest.json"), Body=manifest_body,
            ContentType="application/json", ServerSideEncryption="AES256",
        )
    return {"files": len(files), "uploaded": uploaded}


def list_backup(bucket: str, prefix: str, client: Any) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    token = None
    normalized_prefix = prefix.strip("/")
    while True:
        arguments = {"Bucket": bucket, "Prefix": f"{normalized_prefix}/" if normalized_prefix else ""}
        if token:
            arguments["ContinuationToken"] = token
        response = client.list_objects_v2(**arguments)
        objects.extend({"key": item["Key"], "size": item["Size"]} for item in response.get("Contents", []))
        if not response.get("IsTruncated"):
            break
        token = response["NextContinuationToken"]
    return objects


def restore_backup(destination: Path, bucket: str, prefix: str, client: Any, dry_run: bool = False) -> dict[str, int]:
    manifest_response = client.get_object(Bucket=bucket, Key=_key(prefix, "manifest.json"))
    try:
        manifest = json.loads(manifest_response["Body"].read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(f"invalid manifest JSON: {exc}") from exc
    files = validate_manifest(manifest, destination)  # Validate every path before any local write.
    restored = verified = 0
    for entry in files:
        relative = str(entry["path"])
        target = safe_destination(destination, relative)
        if dry_run:
            print(f"DRY RUN restore s3://{bucket}/{_key(prefix, f'files/{relative}')} -> {target}")
            continue
        content = client.get_object(Bucket=bucket, Key=_key(prefix, f"files/{relative}"))["Body"].read()
        if len(content) != entry["size"] or sha256_bytes(content) != entry["sha256"]:
            raise BackupVerificationError(f"downloaded content does not match manifest: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        restored += 1
        if target.stat().st_size != entry["size"] or sha256_file(target) != entry["sha256"]:
            raise BackupVerificationError(f"restored file does not match manifest: {relative}")
        verified += 1
    return {"files": len(files), "restored": restored, "verified": verified}
