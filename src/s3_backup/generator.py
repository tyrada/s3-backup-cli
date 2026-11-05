"""Create a deterministic nested folder for backup demonstrations."""

from __future__ import annotations

import argparse
import random
from pathlib import Path


def generate_sample(output_dir: Path, seed: int = 2025) -> None:
    rng = random.Random(seed)
    (output_dir / "documents").mkdir(parents=True, exist_ok=True)
    (output_dir / "data" / "nested").mkdir(parents=True, exist_ok=True)
    (output_dir / "README.txt").write_text("Deterministic S3 backup sample\n", encoding="utf-8")
    (output_dir / "documents" / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (output_dir / "data" / "values.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    (output_dir / "data" / "nested" / "payload.bin").write_bytes(bytes(rng.randrange(256) for _ in range(256)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic nested backup sample")
    parser.add_argument("--output-dir", type=Path, default=Path("sample-source"))
    parser.add_argument("--seed", type=int, default=2025)
    args = parser.parse_args()
    generate_sample(args.output_dir, args.seed)
    print(f"generated sample folder at {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

