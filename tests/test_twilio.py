"""
Twilio connectivity test.

Checks three things in order:
  1. Credentials are loaded from .env
  2. The Twilio API accepts them (fetches your account info)
  3. Can send a WhatsApp message to a target number

Run:
  python tests/test_twilio.py +31648843351
  (replace with the number you want to receive the test message)
"""

import io
import os
import sys

# Force UTF-8 output on Windows so Unicode characters print correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Load .env before importing anything from app/
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException


def check_credentials() -> bool:
    sid   = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    wa    = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")

    print("\n── Credential check ──────────────────────────────")
    print(f"  TWILIO_ACCOUNT_SID      : {sid[:8]}{'*' * max(0, len(sid)-8) if sid else '  NOT SET ❌'}")
    print(f"  TWILIO_AUTH_TOKEN       : {'set ✅' if token else 'NOT SET ❌'}")
    print(f"  TWILIO_WHATSAPP_NUMBER  : {wa if wa else 'NOT SET ❌'}")

    missing = [k for k, v in [
        ("TWILIO_ACCOUNT_SID", sid),
        ("TWILIO_AUTH_TOKEN", token),
        ("TWILIO_WHATSAPP_NUMBER", wa),
    ] if not v]

    if missing:
        print(f"\n  ❌ Missing: {', '.join(missing)}")
        print("  Edit .env with your values from https://console.twilio.com")
        return False

    return True


def check_api_connection() -> bool:
    print("\n── API connection ────────────────────────────────")
    try:
        client = Client(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
        account = client.api.accounts(os.environ["TWILIO_ACCOUNT_SID"]).fetch()
        print(f"  Connected ✅  Account: {account.friendly_name}  Status: {account.status}")
        return True
    except TwilioRestException as e:
        print(f"  ❌ API error {e.code}: {e.msg}")
        if e.code == 20003:
            print("  → Check your TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        return False


def send_test_message(to_number: str) -> bool:
    print(f"\n── Send test message → {to_number} ──────────────")
    try:
        client = Client(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )
        msg = client.messages.create(
            from_=f"whatsapp:{os.environ['TWILIO_WHATSAPP_NUMBER']}",
            to=f"whatsapp:{to_number}",
            body="✅ CrediSnap Twilio test — if you see this, your webhook is ready!",
        )
        print(f"  Sent ✅  SID: {msg.sid}  Status: {msg.status}")
        print(f"\n  ⚠️  Sandbox note: the recipient must first send")
        print(f"  'join <your-sandbox-keyword>' to the Twilio sandbox number,")
        print(f"  OR you must be using a Twilio-approved WhatsApp sender.")
        return True
    except TwilioRestException as e:
        print(f"  ❌ Send failed {e.code}: {e.msg}")
        if e.code == 63016:
            print("  → Recipient has not joined the sandbox.")
            print("    Ask them to send: join <sandbox-keyword>")
            print("    to +14155238886 on WhatsApp first.")
        elif e.code == 21608:
            print("  → The 'from' number is not WhatsApp-enabled in your account.")
        return False


if __name__ == "__main__":
    to = sys.argv[1] if len(sys.argv) > 1 else None

    ok = check_credentials()
    if not ok:
        sys.exit(1)

    ok = check_api_connection()
    if not ok:
        sys.exit(1)

    if to:
        send_test_message(to)
    else:
        print("\n── Message test skipped ──────────────────────────")
        print("  Pass a phone number to also send a test message:")
        print("  python tests/test_twilio.py +31648843351")

    print()
