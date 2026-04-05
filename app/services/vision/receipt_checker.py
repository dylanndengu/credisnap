"""
Pre-flight vision check: does this image contain more than one receipt?

Uses Claude's vision capability to inspect the raw image bytes before
Textract is called. If multiple receipts are detected the pipeline
rejects the image immediately, avoiding a bad Textract result.

Only called for image MIME types (JPEG, PNG, WebP). PDFs are passed
through — multi-page PDFs are handled by Textract natively and a
multi-receipt PDF is unusual enough not to warrant a check here.
"""
from __future__ import annotations

import base64
import logging

import anthropic

log = logging.getLogger(__name__)

_VISION_PROMPT = (
    "Look at this image carefully. Does it contain MORE THAN ONE separate receipt, "
    "invoice, or till slip? Answer with exactly one word: YES or NO."
)

_IMAGE_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def contains_multiple_receipts(
    content: bytes,
    mime_type: str,
    anthropic_client: anthropic.Anthropic | None = None,
) -> bool:
    """
    Return True if the image appears to contain more than one receipt.

    For non-image MIME types (e.g. PDF) always returns False — skip the check.
    """
    if mime_type not in _IMAGE_MIME_TYPES:
        return False

    client = anthropic_client or anthropic.Anthropic()
    image_data = base64.standard_b64encode(content).decode("utf-8")

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # cheapest model — vision only
            max_tokens=5,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
        )
        answer = response.content[0].text.strip().upper()
        log.info("Multiple-receipt vision check: %r", answer)
        return answer.startswith("YES")
    except Exception:
        # If the check fails, let the pipeline continue — don't block the user
        log.warning("Multiple-receipt vision check failed; proceeding without check", exc_info=True)
        return False
