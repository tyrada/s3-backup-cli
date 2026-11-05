"""argparse interface, structured logs, and automation-friendly exit codes."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from s3_backup.manifest import ManifestError
from s3_backup.storage import BackupVerificationError, list_backup, restore_backup, upload_backup

EXIT_SUCCESS = 0
EXIT_UNEXPECTED = 1
EXIT_INPUT_OR_VERIFICATION = 2
EXIT_AWS_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload, list, or safely restore an S3 folder backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    upload = subparsers.add_parser("upload")
    upload.add_argument("--source", required=True, type=Path)
    upload.add_argument("--bucket", required=True)
    upload.add_argument("--prefix", required=True)
    upload.add_argument("--dry-run", action="store_true")
    listing = subparsers.add_parser("list")
    listing.add_argument("--bucket", required=True)
    listing.add_argument("--prefix", required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--destination", required=True, type=Path)
    restore.add_argument("--bucket", required=True)
    restore.add_argument("--prefix", required=True)
    restore.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
    logger = logging.getLogger(__name__)
    try:
        client = boto3.client("s3")  # Uses the standard AWS credential provider chain.
        if args.command == "upload":
            result = upload_backup(args.source, args.bucket, args.prefix, client, args.dry_run)
        elif args.command == "restore":
            result = restore_backup(args.destination, args.bucket, args.prefix, client, args.dry_run)
        else:
            objects = list_backup(args.bucket, args.prefix, client)
            for item in objects:
                print(f"{item['size']:>12}  {item['key']}")
            result = {"objects": len(objects), "bytes": sum(int(item["size"]) for item in objects)}
        logger.info(json.dumps({"event": f"{args.command}_complete", **result}, sort_keys=True))
        return EXIT_SUCCESS
    except (ManifestError, BackupVerificationError, FileNotFoundError) as exc:
        logger.error(json.dumps({"event": "input_or_verification_error", "error": str(exc)}, sort_keys=True))
        return EXIT_INPUT_OR_VERIFICATION
    except (BotoCoreError, ClientError) as exc:
        logger.error(json.dumps({"event": "aws_error", "error": str(exc)}, sort_keys=True))
        return EXIT_AWS_ERROR
    except Exception as exc:
        logger.exception(json.dumps({"event": "unexpected_error", "error": str(exc)}, sort_keys=True))
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())

