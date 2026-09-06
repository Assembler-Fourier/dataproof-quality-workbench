"""Stateless HTTP adapter. The domain engine has no framework dependency."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .engine import MAX_BYTES, DataError, parse_csv, profile, rejected_csv, validate
from .models import Contract

app = FastAPI(title="DataProof API", version=__version__, description="Stateless CSV data quality contracts.", docs_url=None, redoc_url=None)


class BoundedBodyMiddleware:
    """Enforce total multipart size before the framework parses or spools it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        limit = 3 * 1024 * 1024
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                response = Response('{"detail":"Request exceeds the 3 MiB limit"}', status_code=413,
                                    media_type="application/json", headers={"Cache-Control": "no-store"})
                return await response(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app.add_middleware(BoundedBodyMiddleware)


@app.middleware("http")
async def secure_responses(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


async def read_dataset(file: UploadFile):
    content = await file.read(MAX_BYTES + 1)
    await file.close()
    try:
        return parse_csv(content)
    except DataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def read_contract(value: str) -> Contract:
    if len(value.encode("utf-8")) > 100_000:
        raise HTTPException(status_code=400, detail="Contract exceeds the 100 KB limit")
    try:
        return Contract.model_validate(json.loads(value, parse_float=Decimal))
    except ValidationError as exc:
        errors = [f"{'.'.join(str(part) for part in error['loc']) or 'contract'}: {error['msg']}" for error in exc.errors()]
        raise HTTPException(status_code=400, detail="Invalid contract: " + "; ".join(errors[:8])) from exc
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Contract must contain valid, shallow JSON") from exc


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__, "storage": "none"}


@app.post("/api/profile")
async def profile_csv(file: Annotated[UploadFile, File()]):
    dataset = await read_dataset(file)
    return profile(dataset)


@app.post("/api/validate")
async def validate_csv(file: Annotated[UploadFile, File()], contract: Annotated[str, Form()]):
    rules = read_contract(contract)
    dataset = await read_dataset(file)
    return validate(dataset, rules, file.filename or "data.csv")


@app.post("/api/rejected.csv")
async def download_rejected(file: Annotated[UploadFile, File()], contract: Annotated[str, Form()]):
    rules = read_contract(contract)
    dataset = await read_dataset(file)
    report = validate(dataset, rules, file.filename or "data.csv")
    return Response(rejected_csv(dataset, report), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="dataproof-rejected.csv"'})


PUBLIC = Path(__file__).resolve().parents[2] / "public"
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
