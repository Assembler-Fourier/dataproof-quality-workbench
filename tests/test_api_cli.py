import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dataproof.api import app

ROOT = Path(__file__).resolve().parents[1]


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_and_api_schema(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.json(), {"status": "ok", "version": "1.0.0", "storage": "none"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    def test_profile_upload(self):
        response = self.client.post("/api/profile", files={"file": ("test.csv", b"x\n1\n2\n", "text/csv")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["row_count"], 2)

    def test_validation_and_rejected_csv(self):
        files = {"file": ("orders.csv", b"x\n1\nno\n", "text/csv")}
        data = {"contract": json.dumps({"columns": {"x": {"type": "integer"}}})}
        response = self.client.post("/api/validate", files=files, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rejected_row_numbers"], [3])
        export = self.client.post("/api/rejected.csv", files=files, data=data)
        self.assertEqual(export.status_code, 200)
        self.assertIn("3,no", export.text)
        self.assertIn("attachment", export.headers["content-disposition"])

    def test_exact_decimal_json_bounds(self):
        response = self.client.post("/api/validate", files={"file": ("x.csv", b"x\n0.12345678901234567889\n")},
                                    data={"contract": '{"columns":{"x":{"type":"number","minimum":0.12345678901234567890}}}'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rule_counts"], {"minimum": 1})

    def test_invalid_file_is_400(self):
        response = self.client.post("/api/profile", files={"file": ("x.csv", b"x,x\n1,2\n")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("duplicate", response.json()["detail"])

    def test_invalid_contract_is_400(self):
        for content in ("{", '{"columns":{}}', '{"columns":{"x":{"type":"nope"}}}'):
            with self.subTest(content=content):
                response = self.client.post("/api/validate", files={"file": ("x.csv", b"x\n1\n")}, data={"contract": content})
                self.assertEqual(response.status_code, 400)

    def test_missing_upload_is_422(self):
        self.assertEqual(self.client.post("/api/profile").status_code, 422)

    def test_deeply_nested_contract_is_400(self):
        response = self.client.post("/api/validate", files={"file": ("x.csv", b"x\n1\n")},
                                    data={"contract": "[" * 2000 + "0" + "]" * 2000})
        self.assertEqual(response.status_code, 400)

    def test_file_size_limit_and_whole_request_limit(self):
        response = self.client.post("/api/profile", files={"file": ("x.csv", b"a" * (2 * 1024 * 1024 + 1))})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/profile", content=b"x" * (3 * 1024 * 1024 + 1), headers={"content-type": "application/octet-stream"})
        self.assertEqual(response.status_code, 413)

    def test_oversized_contract_rejected(self):
        response = self.client.post("/api/validate", files={"file": ("x.csv", b"x\n1\n")}, data={"contract": " " * 100_001})
        self.assertEqual(response.status_code, 400)
        self.assertIn("100 KB", response.json()["detail"])

    def test_static_ui_sample_and_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Data quality workbench", response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/samples/orders.csv").status_code, 200)
        self.assertEqual(self.client.get("/contracts/orders.json").status_code, 200)


class CLITests(unittest.TestCase):
    def test_cli_exit_codes_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = folder / "input.csv"
            rules = folder / "contract.json"
            output = folder / "report.json"
            rejected = folder / "rejected.csv"
            rules.write_text('{"columns":{"x":{"type":"integer"}}}')
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            command = [sys.executable, "-m", "dataproof", str(data), "--contract", str(rules), "--report", str(output), "--rejected", str(rejected)]
            data.write_text("x\n1\n")
            passed = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            data.write_text("x\nno\n")
            failed = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertEqual(json.loads(output.read_text())["rejected_rows"], 1)
            self.assertIn("2,no", rejected.read_text())
            data.write_text("x,x\n1,2\n")
            invalid = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("duplicate", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
