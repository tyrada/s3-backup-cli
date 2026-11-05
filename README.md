# S3 Backup CLI

A practical `argparse` tool for uploading a local folder to a user-selected private Amazon S3 prefix, listing its objects, and restoring files only after validating a deterministic manifest. Local source files are never deleted.

## Design

```text
local folder -> SHA-256 manifest -> s3://bucket/prefix/files/<relative path>
                                  -> s3://bucket/prefix/manifest.json (published last)

manifest + S3 objects -> validate every path -> download -> size/SHA-256 check
                                             -> local write -> second size/SHA-256 check
```

The version-1 manifest contains sorted POSIX relative paths, byte sizes, and lowercase SHA-256 hashes. Upload requests S3-managed encryption (`AES256`). The manifest is uploaded after data objects so a new manifest is not visible before its files have been sent.

Restore rejects absolute paths, `..`, Windows backslashes and drive prefixes, duplicate entries, malformed hashes, and any path resolving outside the destination. Every manifest path is validated before the first local write. Source symbolic links are rejected rather than followed.

S3 ETags are deliberately ignored for integrity. A single-part ETag is commonly an MD5, multipart ETags have a different construction, and encryption can further affect their meaning. They are not SHA-256 checksums. This tool computes and verifies its own SHA-256 values.

## Local and mocked-AWS execution

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

generate-backup-sample --output-dir sample-source --seed 2025
s3-backup upload --source sample-source --bucket example-private-bucket \
  --prefix backups/demo --dry-run
pytest -q --cov=s3_backup --cov-report=term-missing
```

Commands and exit codes:

```bash
s3-backup upload --source LOCAL_FOLDER --bucket BUCKET --prefix PREFIX [--dry-run]
s3-backup list --bucket BUCKET --prefix PREFIX
s3-backup restore --destination RESTORE_FOLDER --bucket BUCKET --prefix PREFIX [--dry-run]
```

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 1 | Unexpected internal failure |
| 2 | Invalid source/manifest, unsafe path, or content verification failure |
| 3 | AWS SDK/service failure |

Logs are JSON objects with operation and counts. Dry-run upload computes the real manifest and prints every destination but performs no AWS writes. Dry-run restore reads and validates the remote manifest, then prints safe destinations without downloading file bodies or writing locally.

## Actual verified results

Verified locally on November 5, 2026 using Python 3.12.16:

- **9 tests passed** with **84% total statement coverage**.
- A mocked S3 round trip uploaded four files plus the manifest, listed five objects, restored four files, and verified all four.
- Traversal attempts using `../`, absolute paths, backslashes, and Windows drive prefixes were rejected before any local write.
- A same-size object with the wrong content failed SHA-256 verification before being written.
- The default sample dry run discovered four files and returned exit code 0.

Tests use an in-memory S3 client and intentionally return a non-SHA ETag to ensure it is ignored. No AWS bucket was contacted, no real upload/restore was performed, and no AWS costs or transfer measurements are claimed.

## Optional real AWS verification

Use standard boto3 credential discovery from an IAM role, AWS IAM Identity Center, `AWS_PROFILE`, or standard AWS environment variables. Do not store access keys in `.env` or Git.

1. Select an existing private bucket and dedicated prefix.
2. Replace `BUCKET_NAME` and `BACKUP_PREFIX` in [`iam/least-privilege-policy.json`](iam/least-privilege-policy.json). Attach it only to the identity used for this backup. `DeleteObject` is included solely for the documented cleanup command; remove it if operators should not clean up.
3. Verify identity and preview upload:

```bash
aws sts get-caller-identity
aws s3api get-public-access-block --bucket "$BACKUP_BUCKET"
s3-backup upload --source sample-source --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX" --dry-run
```

4. Upload, list, preview restore, restore, and compare local directories:

```bash
s3-backup upload --source sample-source --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX"
s3-backup list --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX"
s3-backup restore --destination restored --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX" --dry-run
s3-backup restore --destination restored --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX"
```

The restore command reports only success after matching both downloaded bytes and the written files to manifest size and SHA-256.

## Permissions, cost, and cleanup

The policy template permits bucket listing only at the selected prefix and object get/put/delete only below it. If the bucket requires SSE-KMS, replace AES256 handling and grant the minimum encrypt/decrypt actions on one key ARN.

Potential costs are S3 storage, PUT/GET/LIST requests, data transfer, and optional KMS requests. Many small files increase request cost; storage classes have retrieval and minimum-duration considerations. Check current regional pricing and configure budgets/lifecycle rules before production use.

Preview deletion, then remove only the remote prefix when certain:

```bash
aws s3 rm "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/" --recursive --dryrun
aws s3 rm "s3://$BACKUP_BUCKET/$BACKUP_PREFIX/" --recursive
```

There is intentionally no CLI command that deletes the local source folder.

## Limitations

- Reusing a prefix overwrites like-named objects and the manifest; use bucket versioning or unique snapshot prefixes for history.
- Publishing the manifest last reduces partial visibility but is not a multi-object transaction.
- Upload does not delete remote objects absent from a newer manifest.
- Restore buffers one object in memory before writing so bad content is never partially written.
- File permissions, timestamps, empty directories, and symbolic links are not preserved.

## Interview questions and answers

1. **Why store a manifest?** It defines the expected snapshot independently of S3 listing order and provides sizes and hashes for verification.
2. **Why not use ETag as SHA-256?** Multipart and encrypted-object ETags are not SHA-256 content digests and cannot provide the required guarantee.
3. **How is path traversal prevented?** The entire manifest is validated as relative POSIX paths, resolved below the restore root, and rejected before any write if unsafe.
4. **Why publish the manifest last?** Readers are less likely to discover a new snapshot definition before its referenced objects exist, though this is not transactional.
5. **How would production snapshots improve?** Use immutable timestamp/content-addressed prefixes, bucket versioning, retention policies, concurrency controls, and lifecycle-managed manifests.
