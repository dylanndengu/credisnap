"""
FastAPI router for the Twilio WhatsApp webhook.

POST /webhook/whatsapp
  — Validates the Twilio signature
  — Returns 200 immediately (empty TwiML)
  — Dispatches message handling as a background task

Security: Every request is rejected with HTTP 403 unless the
X-Twilio-Signature header is valid. This prevents spoofed webhooks
from triggering document processing or DB writes.

Twilio webhook format: application/x-www-form-urlencoded
Key fields used:
  From              — sender's WhatsApp number (prefixed 'whatsapp:')
  Body              — text content (empty for media-only messages)
  NumMedia          — number of attached media files (0 or 1 for WhatsApp)
  MediaUrl0         — URL of the first attachment
  MediaContentType0 — MIME type of the first attachment
  MessageSid        — Twilio's unique message identifier
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

from app.whatsapp import twilio_client
from app.whatsapp.message_handler import handle_message

router = APIRouter(prefix="/webhook", tags=["whatsapp"])

# Twilio expects either an empty 200 or TwiML XML.
# We return an empty TwiML response and send replies via the REST API.
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Twilio inbound webhook endpoint.

    Validates the request signature, acknowledges immediately with an empty
    TwiML response, then processes the message in a background task.
    """
    # Read raw form data (Twilio sends application/x-www-form-urlencoded)
    form_data = dict(await request.form())

    # --- Security gate: validate Twilio signature before any processing ---
    signature = request.headers.get("X-Twilio-Signature", "")
    if not twilio_client.validate_signature(str(request.url), form_data, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Acknowledge immediately — Twilio will retry if we don't respond within 15s
    background_tasks.add_task(handle_message, form_data)

    return Response(
        content=_EMPTY_TWIML,
        media_type="application/xml",
        status_code=200,
    )
