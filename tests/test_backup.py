from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys

import pytest
from botocore.exceptions import ClientError

from s3_backup import cli
from s3_backup.generator import generate_sample
from s3_backup.manifest import ManifestError, build_manifest, sha256_bytes
from s3_backup.storage import BackupVerificationError, list_backup, restore_backup, upload_backup


class MockS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        assert ExtraArgs["ServerSideEncryption"] == "AES256"
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)]), "ETag": '"not-a-sha256"'}

    def list_objects_v2(self, **kwargs):
        contents = [
            {"Key": key, "Size": len(value)}
            for (bucket, key), value in sorted(self.objects.items())
            if bucket == kwargs["Bucket"] and key.startswith(kwargs["Prefix"])
        ]
        return {"Contents": contents, "IsTruncated": False}


def test_manifest_and_generator_are_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_sample(first, seed=7)
    generate_sample(second, seed=7)
    assert build_manifest(first) == build_manifest(second)
    assert len(build_manifest(first)["files"]) == 4


def test_mocked_upload_list_restore_round_trip_verifies_sha256(tmp_path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    generate_sample(source, seed=7)
    client = MockS3()

    assert upload_backup(source, "private-bucket", "/backups/demo/", client) == {"files": 4, "uploaded": 4}
    objects = list_backup("private-bucket", "backups/demo", client)
    assert len(objects) == 5
    assert restore_backup(restored, "private-bucket", "backups/demo", client) == {"files": 4, "restored": 4, "verified": 4}
    assert build_manifest(restored) == build_manifest(source)


def test_upload_dry_run_makes_no_aws_writes(tmp_path):
    source = tmp_path / "source"
    generate_sample(source)
    client = MockS3()
    assert upload_backup(source, "bucket", "prefix", client, dry_run=True) == {"files": 4, "uploaded": 0}
    assert client.objects == {}


@pytest.mark.parametrize("unsafe", ["../escape.txt", "/absolute.txt", "nested\\escape.txt", "C:/drive.txt"])
def test_restore_rejects_path_traversal_before_writing(tmp_path, unsafe):
    client = MockS3()
    manifest = {
        "version": 1,
        "files": [
            {"path": "safe.txt", "size": 4, "sha256": sha256_bytes(b"safe")},
            {"path": unsafe, "size": 4, "sha256": sha256_bytes(b"evil")},
        ],
    }
    client.objects[("bucket", "prefix/manifest.json")] = json.dumps(manifest).encode()
    with pytest.raises(ManifestError, match="unsafe"):
        restore_backup(tmp_path / "restore", "bucket", "prefix", client)
    assert not (tmp_path / "restore").exists()


def test_hash_mismatch_is_rejected_before_file_write(tmp_path):
    client = MockS3()
    manifest = {"version": 1, "files": [{"path": "file.txt", "size": 4, "sha256": sha256_bytes(b"good")}]}
    client.objects[("bucket", "prefix/manifest.json")] = json.dumps(manifest).encode()
    client.objects[("bucket", "prefix/files/file.txt")] = b"evil"
    destination = tmp_path / "restore"
    with pytest.raises(BackupVerificationError, match="does not match"):
        restore_backup(destination, "bucket", "prefix", client)
    assert not (destination / "file.txt").exists()


def test_cli_success_input_failure_and_aws_failure_exit_codes(monkeypatch, tmp_path):
    source = tmp_path / "source"
    generate_sample(source)
    client = MockS3()
    monkeypatch.setattr(cli.boto3, "client", lambda service: client)
    monkeypatch.setattr(sys, "argv", ["s3-backup", "upload", "--source", str(source), "--bucket", "bucket", "--prefix", "prefix", "--dry-run"])
    assert cli.main() == cli.EXIT_SUCCESS

    monkeypatch.setattr(sys, "argv", ["s3-backup", "upload", "--source", str(tmp_path / "missing"), "--bucket", "bucket", "--prefix", "prefix"])
    assert cli.main() == cli.EXIT_INPUT_OR_VERIFICATION

    def deny(_service):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2")

    monkeypatch.setattr(cli.boto3, "client", deny)
    monkeypatch.setattr(sys, "argv", ["s3-backup", "list", "--bucket", "bucket", "--prefix", "prefix"])
    assert cli.main() == cli.EXIT_AWS_ERROR
