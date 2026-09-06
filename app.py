"""Vercel and local ASGI entrypoint."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dataproof.api import app  # noqa: E402,F401
