import csv
import io
import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from dataproof.engine import DataError, MAX_BYTES, MAX_ROWS, parse_csv, profile, rejected_csv, validate
from dataproof.models import Contract


def contract(**rules):
    return Contract.model_validate({"columns": rules})


class ParsingTests(unittest.TestCase):
    def test_bom_and_multiline_quotes(self):
        dataset = parse_csv(b'\xef\xbb\xbfid,note\r\n1,"hello, friend\r\nsecond line"\r\n')
        self.assertEqual(dataset.columns, ["id", "note"])
        self.assertEqual(dataset.rows, [["1", "hello, friend\r\nsecond line"]])

    def test_duplicate_header_after_trimming_is_rejected(self):
        with self.assertRaisesRegex(DataError, "duplicate"):
            parse_csv(b"name, name\na,b\n")

    def test_empty_headers_and_files_are_rejected(self):
        for data in (b"", b"\n", b"id,\n1,2\n"):
            with self.subTest(data=data), self.assertRaises(DataError):
                parse_csv(data)

    def test_bad_quotes_and_ragged_records_are_rejected(self):
        for data in (b'a,b\n1,"unterminated', b"a,b\n1\n", b"a,b\n1,2,3\n"):
            with self.subTest(data=data), self.assertRaises(DataError):
                parse_csv(data)

    def test_invalid_utf8_and_null_bytes_are_rejected(self):
        for data in (b"x\n\xff\n", b"x\na\x00\n"):
            with self.subTest(data=data), self.assertRaises(DataError):
                parse_csv(data)

    def test_file_row_column_and_cell_limits(self):
        for data in (b"x\n" + b"a" * MAX_BYTES, b"x\n" + b"a\n" * (MAX_ROWS + 1),
                     (",".join(f"c{i}" for i in range(51)) + "\n").encode(), b"x\n" + b"a" * 20_001):
            with self.subTest(length=len(data)), self.assertRaises(DataError):
                parse_csv(data)
        self.assertEqual(len(parse_csv(b"x\n" + b"a\n" * MAX_ROWS).rows), MAX_ROWS)


class ProfileTests(unittest.TestCase):
    def test_profile_types_nulls_distinct_and_numeric_extrema(self):
        dataset = parse_csv(b"id,amount,date,flag,note\n1,9,2024-02-29,true, hi \n2,10.5,2024-03-01,FALSE,hi\n3,-2,,, \n")
        result = profile(dataset)
        columns = {column["name"]: column for column in result["columns"]}
        self.assertEqual(columns["amount"]["inferred_type"], "number")
        self.assertEqual(columns["amount"]["minimum"], "-2")
        self.assertEqual(columns["amount"]["maximum"], "10.5")
        self.assertEqual(columns["date"]["inferred_type"], "date")
        self.assertEqual(columns["flag"]["inferred_type"], "boolean")
        self.assertEqual(columns["note"]["unique_count"], 1)
        self.assertEqual(columns["note"]["duplicate_count"], 1)
        self.assertEqual(columns["note"]["null_count"], 1)

    def test_header_only_dataset_has_no_division_errors(self):
        dataset = parse_csv(b"id\n")
        self.assertEqual(profile(dataset)["columns"][0]["inferred_type"], "string")
        report = validate(dataset, contract(id={"nullable": False}))
        self.assertTrue(report.passed)
        self.assertEqual(report.quality_percent, 100)


class ValidationTests(unittest.TestCase):
    def test_sample_has_exact_expected_defects(self):
        root = Path(__file__).resolve().parents[1]
        dataset = parse_csv((root / "public/samples/orders.csv").read_bytes())
        rules = Contract.model_validate_json((root / "contracts/orders.json").read_text())
        report = validate(dataset, rules)
        self.assertEqual(report.total_rows, 12)
        self.assertEqual(report.rejected_rows, 7)
        self.assertEqual(report.total_violations, 8)
        self.assertEqual(report.rule_counts, {"unique": 1, "non_null": 1, "minimum": 1, "type": 3, "maximum": 1, "allowed_values": 1})

    def test_non_null_distinguishes_null_from_literal_null(self):
        result = validate(parse_csv(b'x\n" "\nnull\n'), contract(x={"nullable": False}))
        self.assertEqual(result.rejected_row_numbers, [2])

    def test_type_date_calendar_boolean_integer_and_number_boundaries(self):
        rules = contract(day={"type": "date"}, flag={"type": "boolean"}, count={"type": "integer"}, amount={"type": "number"})
        dataset = parse_csv(b"day,flag,count,amount\n2024-02-29,TRUE,+2,1e2\n2023-02-29,yes,2.0,NaN\n2024-2-01,0,1e2,Infinity\n2024-01-01,false,12,1e99999\n")
        report = validate(dataset, rules)
        self.assertEqual(report.valid_rows, 1)
        self.assertEqual(report.total_violations, 9)

    def test_minimum_maximum_are_inclusive(self):
        report = validate(parse_csv(b"x\n0\n10\n-0.01\n10.01\n"), contract(x={"type": "number", "minimum": "0", "maximum": "10"}))
        self.assertEqual(report.valid_rows, 2)
        self.assertEqual(report.rule_counts, {"minimum": 1, "maximum": 1})

    def test_large_numeric_bounds_keep_exact_precision(self):
        rules = contract(x={"type": "integer", "minimum": "9007199254740993"})
        report = validate(parse_csv(b"x\n9007199254740992\n9007199254740993\n"), rules)
        self.assertEqual(rules.columns["x"].minimum, Decimal("9007199254740993"))
        self.assertEqual(report.rejected_row_numbers, [2])

    def test_uniqueness_is_exact_for_large_numbers(self):
        data = parse_csv(b"x\n123456789012345678901234567890\n123456789012345678901234567891\n123456789012345678901234567890\n")
        result = validate(data, contract(x={"type": "integer", "unique": True}))
        self.assertEqual(result.rejected_row_numbers, [4])

    def test_uniqueness_uses_typed_equivalence_and_first_occurrence(self):
        result = validate(parse_csv(b"x\n1\n1.00\n1e0\n2\n"), contract(x={"type": "number", "unique": True}))
        self.assertEqual(result.rejected_row_numbers, [3, 4])
        self.assertIn("record 2", result.violations[0].message)

    def test_allowed_values_are_case_sensitive_and_trimmed(self):
        result = validate(parse_csv(b"x\n paid \nPAID\n"), contract(x={"allowed_values": ["paid"]}))
        self.assertEqual(result.rejected_row_numbers, [3])

    def test_missing_required_column_rejects_all_records(self):
        result = validate(parse_csv(b"x\n1\n2\n"), contract(y={"required": True}))
        self.assertEqual(result.schema_violations, 1)
        self.assertEqual(result.valid_rows, 0)
        self.assertIsNone(result.violations[0].row)

    def test_optional_missing_column_and_extra_column_policy(self):
        dataset = parse_csv(b"x\n1\n")
        rules = contract(y={"required": False})
        self.assertTrue(validate(dataset, rules).passed)
        rules.allow_extra_columns = False
        self.assertEqual(validate(dataset, rules).rule_counts, {"unexpected_column": 1})

    def test_violation_cap_preserves_exact_totals_and_rejected_export(self):
        dataset = parse_csv(b"x\n" + b"bad\n" * 900)
        result = validate(dataset, contract(x={"type": "integer", "unique": True}))
        self.assertEqual(len(result.violations), 500)
        self.assertEqual(result.total_violations, 1799)
        self.assertEqual(result.rejected_rows, 900)
        self.assertTrue(result.violations_truncated)
        self.assertEqual(len(list(csv.reader(io.StringIO(rejected_csv(dataset, result))))), 901)

    def test_csv_export_neutralizes_formula_and_control_prefixes(self):
        rows = [["=1+1"], [" +SUM(A1)"], ["-2"], ["@x"], ["\tformula"], ["normal"]]
        stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(["=heading"]); writer.writerows(rows)
        dataset = parse_csv(stream.getvalue().encode())
        result = validate(dataset, contract(**{"=heading": {"allowed_values": []}}))
        exported = list(csv.reader(io.StringIO(rejected_csv(dataset, result))))
        self.assertEqual(exported[0][1], "'=heading")
        for record in exported[1:6]:
            self.assertTrue(record[1].startswith("'"))
        self.assertEqual(exported[6][1], "normal")

    def test_long_cell_displays_are_bounded_but_rejected_export_is_complete(self):
        value = "x" * 20_000
        dataset = parse_csv(("x\n" + value + "\n").encode())
        result = validate(dataset, contract(x={"type": "integer"}))
        self.assertTrue(result.violations[0].value_truncated)
        self.assertEqual(len(result.violations[0].value), 300)
        self.assertTrue(profile(dataset)["preview_truncated"])
        self.assertIn(value, rejected_csv(dataset, result))

    def test_unknown_rules_and_inverted_or_nonnumeric_bounds_are_rejected(self):
        for rule in ({"unexpected": True}, {"minimum": "0"}, {"type": "number", "minimum": "2", "maximum": "1"}, {"type": "number", "minimum": "NaN"}):
            with self.subTest(rule=rule), self.assertRaises(ValidationError):
                contract(x=rule)


if __name__ == "__main__":
    unittest.main()
