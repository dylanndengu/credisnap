"""
Thin wrapper around the Twilio SDK.

Responsibilities:
  - Validate inbound webhook signatures (security gate — must pass before any processing)
  - Send outbound WhatsApp messages via the Twilio REST API

All Twilio credentials are read from environment variables:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_NUMBER  (e.g. +14155238886 — the Twilio sandbox or purchased number)
"""

from __future__ import annotations

import logging
import os

from twilio.request_validator import RequestValidator
from twilio.rest import Client

log = logging.getLogger(__name__)


def _client() -> Client:
    return Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


def validate_signature(request_url: str, params: dict, signature: str) -> bool:
    """
    Return True if the X-Twilio-Signature header is valid for this request.

    Must be called before any business logic. A failed validation means the
    request did not come from Twilio and should be rejected with HTTP 403.
    """
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    return validator.validate(request_url, params, signature)


def send_whatsapp(to: str, body: str) -> None:
    """
    Send a WhatsApp message to a user.

    Args:
        to:   E.164 phone number without the 'whatsapp:' prefix (e.g. '+27821234567').
        body: Message text (max 1600 chars for WhatsApp via Twilio).
    """
    try:
        _client().messages.create(
            from_=f"whatsapp:{os.environ['TWILIO_WHATSAPP_NUMBER']}",
            to=f"whatsapp:{to}",
            body=body,
        )
    except Exception:
        log.exception("Failed to send WhatsApp message to %s", to)
        raise
