"""
CrediSnap FastAPI application entry point.

This is intentionally minimal for Phase 3 — it wires the WhatsApp webhook.
Full application configuration (auth middleware, CORS, health checks,
structured logging, etc.) is covered in Step 6.

Run locally:
  uvicorn app.main:app --reload --port 8000

Expose to Twilio during development (requires a public URL):
  ngrok http 8000
  # Set the ngrok URL + /webhook/whatsapp as the Twilio webhook URL

Required environment variables:
  DATABASE_URL            — asyncpg DSN e.g. postgresql://user:pass@host/credisnap
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_NUMBER  — E.164 format e.g. +14155238886
  S3_BUCKET               — AWS S3 bucket name
  AWS_REGION              — default: af-south-1 (Cape Town)
  ANTHROPIC_API_KEY       — for LLM categorisation
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

from fastapi import FastAPI

from app.db.connection import close_pool, get_pool
from app.whatsapp.router import router as whatsapp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up the DB connection pool on startup
    await get_pool()
    yield
    # Drain connections on shutdown
    await close_pool()


app = FastAPI(
    title="CrediSnap",
    description="WhatsApp-based financial statement generator for South African SMEs",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(whatsapp_router)
