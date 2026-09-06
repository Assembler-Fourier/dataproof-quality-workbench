"""Bounded CSV parsing, type inference and deterministic contract evaluation."""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import ColumnRule, Contract, Report, Violation

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 20_000
MAX_VIOLATIONS = 500
DISPLAY_CELL_CHARS = 300
INTEGER = re.compile(r"^[+-]?\d+$", re.ASCII)
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)


class DataError(ValueError):
    """A file cannot safely be interpreted as a supported CSV dataset."""


@dataclass(frozen=True)
class Dataset:
    columns: list[str]
    rows: list[list[str]]


def parse_csv(content: bytes) -> Dataset:
    if len(content) > MAX_BYTES:
        raise DataError("CSV exceeds the 2 MiB limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError("Use a UTF-8 encoded CSV file") from exc
    if "\x00" in text:
        raise DataError("CSV contains null bytes")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if not header:
            raise DataError("CSV is empty or has no header")
        columns = [value.strip() for value in header]
        if len(columns) > MAX_COLUMNS:
            raise DataError("CSV exceeds the 50-column limit")
        if any(not value or len(value) > 200 for value in columns):
            raise DataError("Column names must be nonempty and at most 200 characters")
        if len(set(columns)) != len(columns):
            raise DataError("CSV has duplicate column names after trimming whitespace")
        rows = []
        for row_number, values in enumerate(reader, 2):
            if len(rows) >= MAX_ROWS:
                raise DataError("CSV exceeds the 10,000-row limit")
            if len(values) != len(columns):
                raise DataError(f"Record {row_number} has {len(values)} fields; expected {len(columns)}")
            if any(len(value) > MAX_CELL_CHARS for value in values):
                raise DataError(f"Record {row_number} has a cell exceeding 20,000 characters")
            rows.append(values)
    except csv.Error as exc:
        raise DataError(f"Malformed CSV: {exc}") from exc
    return Dataset(columns=columns, rows=rows)


def is_type(value: str, kind: str) -> bool:
    if kind == "string":
        return True
    if kind == "integer":
        return bool(INTEGER.fullmatch(value))
    if kind == "number":
        if not NUMBER.fullmatch(value):
            return False
        try:
            number = Decimal(value)
            # Prevent pathological exponents from reaching arithmetic or clients.
            return number.is_finite() and abs(number.adjusted()) <= 308
        except InvalidOperation:
            return False
    if kind == "boolean":
        return value.lower() in {"true", "false"}
    if kind == "date":
        if not DATE.fullmatch(value):
            return False
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    return False


def infer_type(values: list[str]) -> str:
    if not values:
        return "string"
    for kind in ("boolean", "integer", "number", "date"):
        if all(is_type(value, kind) for value in values):
            return kind
    return "string"


def profile(dataset: Dataset) -> dict:
    columns = []
    for index, name in enumerate(dataset.columns):
        values = [row[index].strip() for row in dataset.rows]
        present = [value for value in values if value]
        kind = infer_type(present)
        numeric = kind in {"integer", "number"}
        extrema = [Decimal(value) for value in present] if numeric else present
        columns.append({
            "name": name,
            "inferred_type": kind,
            "null_count": len(values) - len(present),
            "non_null_count": len(present),
            "unique_count": len(set(present)),
            "duplicate_count": len(present) - len(set(present)),
            "minimum": str(min(extrema)) if extrema and (numeric or kind == "date") else None,
            "maximum": str(max(extrema)) if extrema and (numeric or kind == "date") else None,
        })
    return {"row_count": len(dataset.rows), "column_count": len(dataset.columns),
            "columns": columns,
            "preview": [[value[:DISPLAY_CELL_CHARS] + ("…" if len(value) > DISPLAY_CELL_CHARS else "")
                         for value in row] for row in dataset.rows[:8]],
            "preview_cell_limit": DISPLAY_CELL_CHARS,
            "preview_truncated": any(len(value) > DISPLAY_CELL_CHARS for row in dataset.rows[:8] for value in row)}


def canonical(value: str, rule: ColumnRule) -> str | Decimal:
    if rule.type in {"number", "integer"} and is_type(value, rule.type):
        # Decimal equality recognizes equivalent numeric spellings without losing precision.
        return Decimal(value)
    if rule.type == "boolean":
        return value.lower()
    return value


def validate(dataset: Dataset, contract: Contract, filename: str = "data.csv") -> Report:
    violations: list[Violation] = []
    counts: Counter = Counter()
    rejected: set[int] = set()
    schema_count = 0
    total = 0

    def add(row: int | None, column: str, rule: str, value: str | None, message: str) -> None:
        nonlocal total, schema_count
        total += 1
        counts[rule] += 1
        if row is None:
            schema_count += 1
        else:
            rejected.add(row)
        if len(violations) < MAX_VIOLATIONS:
            violations.append(Violation(row=row, column=column, rule=rule,
                                        value=value[:DISPLAY_CELL_CHARS] if value is not None else None,
                                        value_truncated=value is not None and len(value) > DISPLAY_CELL_CHARS,
                                        message=message))

    for name, rule in contract.columns.items():
        if name not in dataset.columns and rule.required:
            add(None, name, "required_column", None, "Required column is missing")
    if not contract.allow_extra_columns:
        for name in dataset.columns:
            if name not in contract.columns:
                add(None, name, "unexpected_column", None, "Column is not defined in the contract")
    for index, name in enumerate(dataset.columns):
        rule = contract.columns.get(name)
        if rule is None:
            continue
        seen: dict[str | Decimal, int] = {}
        for row_number, row in enumerate(dataset.rows, 2):
            raw = row[index]
            value = raw.strip()
            if not value:
                if not rule.nullable:
                    add(row_number, name, "non_null", raw, "A value is required")
                continue
            valid_type = is_type(value, rule.type)
            if not valid_type:
                add(row_number, name, "type", raw, f"Expected {rule.type}")
            if rule.unique:
                key = canonical(value, rule)
                if key in seen:
                    add(row_number, name, "unique", raw, f"Duplicates record {seen[key]}")
                else:
                    seen[key] = row_number
            if rule.allowed_values is not None and value not in rule.allowed_values:
                add(row_number, name, "allowed_values", raw, "Value is outside the allowed set")
            if valid_type and rule.type in {"integer", "number"}:
                numeric = Decimal(value)
                if rule.minimum is not None and numeric < Decimal(str(rule.minimum)):
                    add(row_number, name, "minimum", raw, f"Must be at least {rule.minimum:g}")
                if rule.maximum is not None and numeric > Decimal(str(rule.maximum)):
                    add(row_number, name, "maximum", raw, f"Must be at most {rule.maximum:g}")
    # Schema failures invalidate every record, even if its cells pass individual rules.
    if schema_count:
        rejected.update(range(2, len(dataset.rows) + 2))
    valid_rows = len(dataset.rows) - len(rejected)
    return Report(contract_name=contract.name, filename=filename, passed=total == 0,
                  total_rows=len(dataset.rows), valid_rows=valid_rows, rejected_rows=len(rejected),
                  total_violations=total, schema_violations=schema_count, violations=violations,
                  violations_truncated=total > MAX_VIOLATIONS, rule_counts=dict(counts),
                  rejected_row_numbers=sorted(rejected),
                  quality_percent=round(valid_rows / len(dataset.rows) * 100, 2) if dataset.rows else (0 if total else 100))


def safe_csv_cell(value: str) -> str:
    """Neutralize spreadsheet formulas, including leading whitespace variants."""
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def rejected_csv(dataset: Dataset, report: Report) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    row_label = "__dataproof_record__"
    while row_label in dataset.columns:
        row_label = "_" + row_label
    writer.writerow([row_label, *[safe_csv_cell(value) for value in dataset.columns]])
    for row_number in report.rejected_row_numbers:
        writer.writerow([row_number, *[safe_csv_cell(value) for value in dataset.rows[row_number - 2]]])
    return stream.getvalue()
