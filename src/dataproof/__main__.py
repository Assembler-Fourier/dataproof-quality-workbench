"""CLI exit codes: 0 passes, 1 contract violations, 2 invalid input."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from .engine import MAX_BYTES, DataError, parse_csv, rejected_csv, validate
from .models import Contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CSV data against a DataProof contract")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Also write the JSON report to this path")
    parser.add_argument("--rejected", type=Path, help="Write rejected records as spreadsheet-safe CSV")
    args = parser.parse_args()
    try:
        with args.csv.open("rb") as file:
            dataset = parse_csv(file.read(MAX_BYTES + 1))
        if args.contract.stat().st_size > 100_000:
            raise DataError("Contract exceeds the 100 KB limit")
        contract = Contract.model_validate(json.loads(args.contract.read_text(encoding="utf-8"), parse_float=Decimal))
        report = validate(dataset, contract, args.csv.name)
        output = report.model_dump_json(indent=2)
        print(output)
        if args.report:
            args.report.write_text(output + "\n", encoding="utf-8")
        if args.rejected:
            args.rejected.write_text(rejected_csv(dataset, report), encoding="utf-8", newline="")
        return 0 if report.passed else 1
    except (OSError, ValueError, RecursionError) as exc:
        print(f"dataproof: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
