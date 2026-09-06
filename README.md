# DataProof

**A CSV data quality workbench with reusable contracts, precise validation findings, and a CLI for CI pipelines.** Built by [Uzair Waseem](https://github.com/Assembler-Fourier).

[Live workbench](https://dataproof-quality-workbench.vercel.app/) · [API schema](https://dataproof-quality-workbench.vercel.app/openapi.json) · [Source](https://github.com/Assembler-Fourier/dataproof-quality-workbench)

DataProof answers a practical pipeline question: **does this file meet the expectations of its consumers?** Upload a CSV, inspect its column profile, define explicit rules, and export a report or rejected records. The same validation engine powers the browser, HTTP API, and command line.

The included orders fixture loads automatically: **12 records, 8 rule violations, 7 rejected records**. It demonstrates a duplicate order, missing customer, negative and excessive amounts, an invalid calendar date, a disallowed status, and incorrect number and boolean values. These are intentional test data, not production metrics.

## What works

- UTF-8 CSV uploads with BOM, quoted commas, and multiline fields.
- Per-column type inference, null and distinct counts, duplicate counts, and numeric/date minimum and maximum.
- Reusable JSON contracts: required columns, non-null cells, data types, uniqueness, allowed values, numeric bounds, and extra-column policy.
- A visual contract editor, contract import/export, and validation against the current dataset.
- Findings identify the logical record, column, rule, original value, and reason. Rule filters and aggregate counts remain accurate when displayed findings are capped.
- JSON reports and rejected-record CSV exports. Spreadsheet formula prefixes are neutralized in exported cells and headers.
- A stateless FastAPI API and a Python CLI with meaningful exit codes.

No API key or LLM is involved: data contracts produce deterministic, reproducible results.

## Run locally

Requires Python 3.12+ and Node.js 24+ for the static build.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm ci
npm run build
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`. The Python app serves the workbench locally. The build copies `public/` into `dist/` for Vercel static delivery. The canonical sample contract lives in `contracts/orders.json`; the build copies it into the public assets.

## Use in a data pipeline

```sh
python -m dataproof public/samples/orders.csv \
  --contract contracts/orders.json \
  --report report.json \
  --rejected rejected.csv
```

The sample exits **1** because it intentionally fails validation. Exit **0** means the contract passes; exit **1** means contract violations; exit **2** means invalid input, invalid contract, or file I/O failure. Installed environments can also use the `dataproof` command. JSON is printed to stdout; input errors go to stderr.

Example CI gate after producing a dataset:

```sh
dataproof build/orders.csv --contract contracts/orders.json --report quality-report.json
```

## Contract format

```json
{
  "name": "Orders quality contract",
  "version": 1,
  "allow_extra_columns": false,
  "columns": {
    "order_id": {"type": "string", "required": true, "nullable": false, "unique": true},
    "amount": {"type": "number", "nullable": false, "minimum": "0.01", "maximum": "5000"},
    "status": {"type": "string", "allowed_values": ["pending", "paid", "shipped"]},
    "ordered_at": {"type": "date"}
  }
}
```

`required` means the **column must exist**; `nullable` controls **empty cells**. Each column defaults to `string`, required, nullable, and not unique. Allowed values and numeric bounds default to no restriction. Extra columns are allowed by default. Unknown properties are rejected to catch misspelled rules.

Use **JSON strings for numeric bounds**, as shown above, to preserve precision across JavaScript and other clients. The engine uses `Decimal` comparisons, including numbers larger than JavaScript's safe integer range. The browser rejects numeric JSON bounds that are decimals or unsafe integers; use strings for these. API and CLI JSON parsing also preserve exact decimal bounds.

### Validation semantics

- Header names and cell values are trimmed for matching and validation. Original cell text is retained in rejected exports.
- Empty or whitespace-only cells are null. Literal strings such as `null` and `NA` are ordinary values. Null cells are checked only by the non-null rule; uniqueness, types, bounds, and allowed values skip nulls.
- Integers accept an optional sign and ASCII digits; `2.0` is not an integer. Numbers accept decimal and scientific notation, reject non-finite values, and limit their adjusted decimal exponent to ±308. Integers retain exact precision up to the cell length limit.
- Dates must be real calendar dates in exactly `YYYY-MM-DD` format. Timestamps are not supported. Booleans accept `true` and `false`, case-insensitively.
- Allowed values are exact, case-sensitive comparisons after trimming. Numeric bounds are inclusive.
- Unique numeric values use exact numeric equality (`1`, `1.00`, and `1e0` are equivalent). Boolean uniqueness is case-insensitive. The **first occurrence passes** the uniqueness rule; subsequent duplicates fail. Other rules can still reject the first occurrence.
- Record numbers start at 2 because the header is record 1. A quoted multiline field is a single CSV record, so record numbers can differ from physical file line numbers.
- Missing required columns and unexpected columns are schema findings (`row: null`). Any schema finding rejects every data record. A header-only file passes if its schema passes; the contract currently has no minimum-row-count rule.
- Type inference describes the uploaded file and does not prove correctness. A dirty numeric column can be inferred as a string; set its expected type explicitly or import a known contract.

## HTTP API

All upload endpoints accept `multipart/form-data`. Files and results are not stored by the application.

| Endpoint | Fields | Result |
| --- | --- | --- |
| `GET /api/health` | — | Version, status, storage mode |
| `POST /api/profile` | `file` | Column profile and bounded preview |
| `POST /api/validate` | `file`, `contract` (JSON string) | Validation report |
| `POST /api/rejected.csv` | `file`, `contract` (JSON string) | Rejected records as CSV |
| `GET /openapi.json` | — | OpenAPI schema |

```sh
curl -sS http://127.0.0.1:8000/api/validate \
  -F 'file=@public/samples/orders.csv' \
  -F 'contract=<contracts/orders.json'
```

Invalid CSV or contracts return HTTP 400, missing fields return 422, and requests over the whole-body limit return 413. Contract violations are a successful HTTP 200 response with `passed: false`. API responses use `Cache-Control: no-store`. The UI uses a strict same-origin Content Security Policy; API documentation is provided as JSON rather than a CDN-hosted Swagger UI.

## Architecture and engineering decisions

```text
Browser workbench ── multipart HTTP ── FastAPI adapter ─┐
                                                    ├── CSV engine ── typed report
CLI ─────────────── file + contract ─────────────────┘
```

- `src/dataproof/engine.py`: CSV parsing, profiles, deterministic validation, and safe exports. No HTTP framework dependencies.
- `src/dataproof/models.py`: Pydantic contracts and report schemas, including rule validation.
- `src/dataproof/api.py`: bounded request parsing, error translation, security headers, and endpoints.
- `src/dataproof/__main__.py`: pipeline-friendly CLI and exit codes.
- `public/`: accessible, responsive vanilla JavaScript workbench. Uploaded values use text nodes, never HTML interpolation. Browser state lives in the current tab; there is no local storage.
- `tests/`: parsing boundaries, semantic rules, exact numeric behavior, export safety, HTTP integration, and CLI exit-code tests.

A standard-library CSV engine keeps deployment small and makes rules easy to inspect. A database or query engine would add operational cost without improving the supported 10,000-row workflow. Each request is independent, and the source CSV is uploaded again when profiling, validating, or exporting.

## Limits and privacy

- CSV only: UTF-8, comma delimiter, explicit header. No Excel, automatic delimiter detection, relational checks, custom expressions, cross-file joins, or background jobs.
- Maximum **2 MiB per CSV, 10,000 data records, 50 columns, 20,000 characters per cell**, and 200 characters per header. Ragged records, duplicate headers, null bytes, and malformed quoted fields are rejected. A blank record is not silently skipped.
- Contracts are limited to **100 KB**, 50 column rules, and 200 allowed values per column. Total HTTP bodies are capped at **3 MiB before multipart parsing**. Vercel can apply additional platform limits.
- Reports retain the first **500 violations**, while total counts and rejected record numbers include all findings. Displayed violation values are capped at **300 characters** and marked `value_truncated`; previews use the same cell limit. Rejected CSV exports include every rejected record with original values, apart from protective formula prefixes.
- Formula-leading cells and headers (`=`, `+`, `-`, `@`, including whitespace variants or leading tab/newline) receive an apostrophe on CSV export. This intentionally changes exported negative numeric cells into safe text. Always treat untrusted CSVs carefully in spreadsheet tools.
- No accounts, server-side persistence, historical reports, application-level rate limiting, or service-level guarantee. The app does not write uploads to a database or persistent storage. Request processing uses memory; infrastructure may retain normal request metadata. Use non-sensitive example data in the public demonstration.
- Refreshing clears the current dataset, contract edits, and report. Export anything you want to keep.

## Verification

```sh
npm run check
npm run build
python -m unittest discover -s tests -v
```

The GitHub Actions workflow runs these checks on Python 3.12 and Node.js 24. Tests cover malformed CSV, BOM and quoted multiline content, limits, calendar boundaries, null handling, optional and missing columns, exact decimal bounds, uniqueness precision, formula-safe exports, capped findings with complete rejected exports, request limits, invalid contracts, and CLI outcomes.

## Deploy

The repository is configured for Vercel's FastAPI framework with root `app.py`, Python 3.12, and a static frontend build. Connect the GitHub repository to Vercel, choose the FastAPI framework preset, and deploy. No environment variables or external services are required.

## License

[MIT](LICENSE), © 2026 Uzair Waseem.
