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

import uvicorn


if __name__ == "__main__":
    PORT = 8000

    print("\n── CrediSnap dev server ──────────────────────────────")
    print(f"\n  Server running at: http://localhost:{PORT}")
    print(f"\n  In a separate terminal, run:")
    print(f"    ngrok http {PORT}")
    print(f"\n  Then paste the ngrok URL + /webhook/whatsapp into Twilio.")
    print(f"  Twilio console → Messaging → Try it out → Sandbox settings\n")

    sys.stdout.flush()

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
    )
