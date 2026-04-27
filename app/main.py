"""
CrediSnap FastAPI application entry point.

Run locally:
  uvicorn app.main:app --reload --port 8000

Required environment variables:
  DATABASE_URL            — asyncpg DSN e.g. postgresql://user:pass@host/credisnap
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_NUMBER  — E.164 format e.g. +14155238886
  S3_BUCKET               — AWS S3 bucket name
  AWS_REGION              — default: af-south-1 (Cape Town)
  ANTHROPIC_API_KEY       — for LLM categorisation
  SENTRY_DSN              — (optional) Sentry error monitoring DSN
  ENVIRONMENT             — development | production (default: development)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env in every process (including uvicorn reload workers)
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.db.connection import close_pool, get_pool
from app.whatsapp.router import router as whatsapp_router

# Sentry — no-op if SENTRY_DSN is not set
_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.environ.get("ENVIRONMENT", "development"),
        traces_sample_rate=0.1,   # 10% of requests traced — adjust in production
        send_default_pii=False,   # POPIA: never attach user identifiers to events
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(
    title="CrediSnap",
    description="WhatsApp-based financial statement generator for South African SMEs",
    version="0.4.0",
    lifespan=lifespan,
)

app.include_router(whatsapp_router)


@app.get("/health", include_in_schema=False)
async def health():
    """Health check endpoint — used by hosting platforms and load balancers."""
    return JSONResponse({"status": "ok"})
