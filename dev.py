"""
Local development server.

Starts uvicorn + an ngrok tunnel together, then prints the exact
webhook URL to paste into your Twilio console.

Usage:
  python dev.py

Requirements:
  - .env file with TWILIO_* values filled in
  - ngrok installed: https://ngrok.com/download
    (free account needed — sign up, then run: ngrok config add-authtoken <your-token>)
"""

import io
import os
import sys
import threading
import time
from pathlib import Path

# Force UTF-8 on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from pyngrok import ngrok
import uvicorn


def start_ngrok(port: int) -> str:
    tunnel = ngrok.connect(port, "http")
    return tunnel.public_url.replace("http://", "https://")


if __name__ == "__main__":
    PORT = 8000

    print("\n── Starting CrediSnap dev server ─────────────────")

    # Start ngrok tunnel
    try:
        public_url = start_ngrok(PORT)
    except Exception as e:
        print(f"\n❌ ngrok failed to start: {e}")
        print("   Make sure ngrok is installed and authenticated:")
        print("   1. Download from https://ngrok.com/download")
        print("   2. Run: ngrok config add-authtoken <your-token>")
        sys.exit(1)

    webhook_url = f"{public_url}/webhook/whatsapp"

    print(f"\n  Public URL  : {public_url}")
    print(f"\n  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  Paste this into Twilio console as webhook URL:     │")
    print(f"  │                                                     │")
    print(f"  │  {webhook_url:<51} │")
    print(f"  │                                                     │")
    print(f"  └─────────────────────────────────────────────────────┘")
    print(f"\n  Twilio console → Messaging → Try it out")
    print(f"                 → Send a WhatsApp message")
    print(f"                 → Sandbox settings → paste URL above\n")

    sys.stdout.flush()

    # Start uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
    )
