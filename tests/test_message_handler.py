"""
Unit tests for the WhatsApp message-handler state machine.

All I/O (DB, Twilio, S3, Textract) is mocked — no live services needed.

Run:
    pytest tests/test_message_handler.py -v
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

USER_NUMBER = "+27821234567"


def _form(body: str = "", num_media: int = 0, **kwargs) -> dict:
    """Build a minimal Twilio form_data payload."""
    data = {
        "From": f"whatsapp:{USER_NUMBER}",
        "Body": body,
        "NumMedia": str(num_media),
        "MessageSid": "SMtest000",
    }
    data.update(kwargs)
    return data


def _make_conn(fetchrow=None, fetchval=None) -> AsyncMock:
    """
    Return a mock asyncpg connection.
    Pass a list to fetchrow/fetchval to consume values in order,
    or a single value to always return it.
    """
    conn = AsyncMock()
    if isinstance(fetchrow, list):
        conn.fetchrow.side_effect = fetchrow
    else:
        conn.fetchrow.return_value = fetchrow
    if isinstance(fetchval, list):
        conn.fetchval.side_effect = fetchval
    else:
        conn.fetchval.return_value = fetchval
    return conn


def _pool(conn: AsyncMock) -> AsyncMock:
    pool = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)  # sync call → context manager
    return pool


def _patch_pool(pool):
    return patch("app.whatsapp.message_handler.get_pool", AsyncMock(return_value=pool))


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------

def test_normalise_number_strips_prefix():
    from app.whatsapp.message_handler import _normalise_number
    assert _normalise_number("whatsapp:+27821234567") == "+27821234567"
    assert _normalise_number("+27821234567") == "+27821234567"


def test_parse_confirmation_yes_variants():
    from app.whatsapp.message_handler import _parse_confirmation
    for text in ("YES", "yes", "Y", "CONFIRM", "JA"):
        assert _parse_confirmation(text) == "YES", f"Expected YES for {text!r}"


def test_parse_confirmation_no_variants():
    from app.whatsapp.message_handler import _parse_confirmation
    for text in ("NO", "no", "N", "REJECT", "NEE"):
        assert _parse_confirmation(text) == "NO", f"Expected NO for {text!r}"


def test_parse_confirmation_unrecognised_returns_none():
    from app.whatsapp.message_handler import _parse_confirmation
    assert _parse_confirmation("Hello") is None
    assert _parse_confirmation("") is None
    assert _parse_confirmation("SKIP") is None


# ---------------------------------------------------------------------------
# State machine — POPIA consent gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_user_receives_consent_request():
    """First message from an unknown number → create user + send POPIA consent."""
    user_id = uuid4()
    conn = _make_conn(fetchrow=None, fetchval=user_id)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="Hello"))

    mock_twilio.send_whatsapp.assert_called_once()
    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "POPIA" in msg


@pytest.mark.asyncio
async def test_consent_yes_asks_for_business_name():
    """Replying YES to consent → grant + ask for business name."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": False, "onboarding_step": None}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="YES"))

    mock_twilio.send_whatsapp.assert_called_once()
    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "business name" in msg.lower()


@pytest.mark.asyncio
async def test_consent_no_deletes_user():
    """Replying NO to consent → delete skeleton user + send decline message."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": False, "onboarding_step": None}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="NO"))

    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert delete_calls, "Expected a DELETE call for declined user"

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "not been stored" in msg


@pytest.mark.asyncio
async def test_consent_other_resends_prompt():
    """Anything other than YES/NO from a non-consented user → resend consent."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": False, "onboarding_step": None}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="What is this?"))

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "POPIA" in msg


# ---------------------------------------------------------------------------
# State machine — onboarding flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_business_name_empty_body_prompts_again():
    """Empty body during BUSINESS_NAME step → prompt again, no DB write."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "BUSINESS_NAME"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body=""))

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "business name" in msg.lower()
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_business_name_saved_asks_tax_ref():
    """Non-empty body during BUSINESS_NAME step → save name + ask for SARS tax ref."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "BUSINESS_NAME"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="Nkosi Trading"))

    conn.execute.assert_called_once()
    update_sql = conn.execute.call_args[0][0]
    assert "business_name" in update_sql

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "SARS" in msg or "tax" in msg.lower()


@pytest.mark.asyncio
async def test_tax_ref_skip_stores_none_and_completes():
    """SKIP during TAX_REF step → save NULL + send onboarding-complete welcome."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "TAX_REF"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="SKIP"))

    update_args = conn.execute.call_args[0]
    assert update_args[2] is None  # $2 = income_tax_ref should be NULL

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "🎉" in msg


@pytest.mark.asyncio
async def test_tax_ref_saved_and_completes():
    """Valid tax ref during TAX_REF step → save value + send onboarding-complete welcome."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "TAX_REF"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="9876543210"))

    update_args = conn.execute.call_args[0]
    assert update_args[2] == "9876543210"

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "🎉" in msg


# ---------------------------------------------------------------------------
# State machine — fully-onboarded user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yes_with_draft_posts_entry():
    """YES from onboarded user with a DRAFT entry → post it and confirm."""
    user_id = uuid4()
    entry_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "DONE"}
    entry_details = {"description": "Fuel – Engen Sandton", "entry_date": date(2026, 3, 28)}

    conn = _make_conn(
        fetchrow=[user_row, entry_details],  # 1st call = user lookup, 2nd = entry details
        fetchval=entry_id,                   # _find_draft_entry
    )

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="YES"))

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "✅" in msg
    assert "Fuel" in msg or "Posted" in msg


@pytest.mark.asyncio
async def test_yes_with_no_draft_sends_nothing_pending():
    """YES from onboarded user with no DRAFT → tell them nothing is pending."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "DONE"}
    conn = _make_conn(fetchrow=user_row, fetchval=None)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="YES"))

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "nothing" in msg.lower() or "pending" in msg.lower()


@pytest.mark.asyncio
async def test_no_with_draft_discards_entry():
    """NO from onboarded user with a DRAFT → delete the journal entry."""
    user_id = uuid4()
    entry_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "DONE"}
    conn = _make_conn(fetchrow=user_row, fetchval=entry_id)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="NO"))

    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert delete_calls, "Expected DELETE for discarded journal entry"

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "🗑" in msg or "discard" in msg.lower() or "Discard" in msg


@pytest.mark.asyncio
async def test_unrecognised_text_sends_help():
    """Unrecognised text from onboarded user → send help."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "DONE"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="What can you do?"))

    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "REPORT" in msg


@pytest.mark.asyncio
async def test_media_message_dispatches_to_media_handler():
    """Media message from onboarded user → _handle_media_message is called."""
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": "DONE"}
    conn = _make_conn(fetchrow=user_row)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client"),
        patch(
            "app.whatsapp.message_handler._handle_media_message",
            new_callable=AsyncMock,
        ) as mock_media,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(
            body="",
            num_media=1,
            MediaUrl0="https://api.twilio.com/media/foo",
            MediaContentType0="image/jpeg",
        ))

    mock_media.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_user_none_onboarding_step_reaches_normal_flow():
    """
    Users created before migration 004 have onboarding_step=NULL.
    NULL must be treated as DONE — they should reach normal receipt flow.
    """
    user_id = uuid4()
    user_row = {"id": user_id, "popia_consent_given": True, "onboarding_step": None}
    conn = _make_conn(fetchrow=user_row, fetchval=None)

    with (
        _patch_pool(_pool(conn)),
        patch("app.whatsapp.message_handler.twilio_client") as mock_twilio,
    ):
        from app.whatsapp.message_handler import handle_message
        await handle_message(_form(body="YES"))

    # No draft → "nothing pending", NOT the business name prompt
    msg = mock_twilio.send_whatsapp.call_args[0][1]
    assert "nothing" in msg.lower() or "pending" in msg.lower()
