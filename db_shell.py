"""
Interactive Supabase shell.

Usage:
    python db_shell.py          — interactive SQL prompt
    python db_shell.py status   — print schema/enum summary and exit
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load .env (same logic as app/main.py)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

import asyncpg


async def get_conn() -> asyncpg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set — check your .env file")
    return await asyncpg.connect(url)


async def status(conn: asyncpg.Connection) -> None:
    """Print a quick summary of the live schema."""

    print("\n--- Tables -----------------------------------------")
    rows = await conn.fetch("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    for r in rows:
        print(f"  {r['table_name']}")

    print("\n--- conversation_state enum ------------------------")
    rows = await conn.fetch("""
        SELECT unnest(enum_range(NULL::conversation_state))::text AS state
        ORDER BY 1
    """)
    for r in rows:
        print(f"  {r['state']}")

    print("\n--- users columns ----------------------------------")
    rows = await conn.fetch("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'users' AND table_schema = 'public'
        ORDER BY ordinal_position
    """)
    for r in rows:
        print(f"  {r['column_name']:<30} {r['data_type']}")

    print("\n--- account_templates (revenue codes) --------------")
    rows = await conn.fetch("""
        SELECT code, name
        FROM account_templates
        WHERE account_type = 'REVENUE'
        ORDER BY code
    """)
    for r in rows:
        print(f"  {r['code']} - {r['name']}")

    print()


async def repl(conn: asyncpg.Connection) -> None:
    """Simple interactive SQL prompt."""
    print("Connected to Supabase. Type SQL and press Enter twice to run.")
    print("Commands: \\status  \\tables  \\q\n")

    while True:
        lines: list[str] = []
        try:
            while True:
                prompt = "sql> " if not lines else "   > "
                line = input(prompt)
                if line.strip() in ("\\q", "exit", "quit"):
                    print("Bye.")
                    return
                if line.strip() == "\\status":
                    await status(conn)
                    break
                if line.strip() == "\\tables":
                    line = """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'public' ORDER BY table_name
                    """
                    lines.append(line)
                    break
                lines.append(line)
                if line.strip() == "" and lines:
                    break
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return

        sql = "\n".join(lines).strip()
        if not sql:
            continue

        try:
            rows = await conn.fetch(sql)
            if not rows:
                print("(no rows)\n")
                continue
            # Print column headers
            cols = list(rows[0].keys())
            widths = {c: max(len(c), max(len(str(r[c])) for r in rows)) for c in cols}
            header = "  ".join(c.ljust(widths[c]) for c in cols)
            print(header)
            print("  ".join("-" * widths[c] for c in cols))
            for r in rows:
                print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
            print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})\n")
        except asyncpg.PostgresError as e:
            print(f"ERROR: {e}\n")


async def main() -> None:
    conn = await get_conn()
    print(f"Connected -> {os.environ['DATABASE_URL'].split('@')[-1]}")
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "status":
            await status(conn)
        else:
            await repl(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
