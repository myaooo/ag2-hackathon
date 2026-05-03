"""Deploy life-sandbox to a Daytona Cloud sandbox and print a public URL.

Usage:
    uv run python deploy_to_daytona.py

Reads from .env (or shell env):
    DAYTONA_API_KEY     — required (https://app.daytona.io → API Keys)
    LLM_PROVIDER        — passed through to the sandbox
    MODEL               — passed through
    OPENAI_API_KEY      — passed through (if LLM_PROVIDER=openai)
    GEMINI_API_KEY      — passed through (if LLM_PROVIDER=gemini)

What this does:
    1. Creates a Daytona sandbox (public, auto-stop disabled).
    2. Uploads the 5 project files.
    3. Installs ag2 + fastapi + uvicorn + python-dotenv directly via pip
       (skips the local pyproject.toml so the sandbox doesn't need uv).
    4. Starts uvicorn on :8765 in a background session.
    5. Generates a 24-hour signed preview URL and prints it.

Stop a deployment via the Daytona dashboard (https://app.daytona.io)
or programmatically with `Daytona(...).find(<id>).delete()`.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from daytona import (
    CreateSandboxFromSnapshotParams,
    Daytona,
    DaytonaConfig,
    SessionExecuteRequest,
)
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
PROJECT_FILES = [
    "pyproject.toml",
    "schemas.py",
    "agents.py",
    "backend.py",
    "frontend.html",
]
REMOTE_DIR = "/home/daytona/life-sandbox"
PORT = 8765
SERVER_SESSION = "life-sandbox-server"
# is.gd custom shortcode: [a-zA-Z0-9_], 5–30 chars, no hyphens. Override via env.
SHORT_URL_CODE = os.environ.get(
    "LIFE_SANDBOX_SHORTCODE", "life_sandbox_ag2_hackathon"
)


def make_short_url(target_url: str, custom_shortcode: str | None = None) -> str | None:
    """Create an is.gd short URL for `target_url`. Returns short URL or None on failure.

    is.gd custom shortcodes must be globally unique; if the requested code is
    taken, this returns None so callers can fall back to an auto-generated code.
    """
    params: dict[str, str] = {"format": "simple", "url": target_url}
    if custom_shortcode:
        params["shorturl"] = custom_shortcode
    api_url = f"https://is.gd/create.php?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(api_url, timeout=10) as resp:
            body = resp.read().decode().strip()
        if body.startswith("http"):
            return body
        return None  # is.gd returned "Error: ..." or unexpected content
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def main() -> None:
    load_dotenv()

    daytona_key = os.environ.get("DAYTONA_API_KEY")
    if not daytona_key:
        sys.exit("DAYTONA_API_KEY is required. Add it to .env or export it.")

    llm_env = {
        k: os.environ[k]
        for k in ("LLM_PROVIDER", "MODEL", "OPENAI_API_KEY", "GEMINI_API_KEY")
        if os.environ.get(k)
    }
    if not (llm_env.get("OPENAI_API_KEY") or llm_env.get("GEMINI_API_KEY")):
        sys.exit("Need OPENAI_API_KEY or GEMINI_API_KEY in env to deploy.")
    llm_env["PORT"] = str(PORT)

    daytona = Daytona(DaytonaConfig(api_key=daytona_key))

    print("→ Creating Daytona sandbox…")
    sandbox = daytona.create(
        CreateSandboxFromSnapshotParams(
            env_vars=llm_env,
            public=True,             # signed URL works without per-request token
            auto_stop_interval=0,    # don't auto-stop while we demo
            labels={"app": "life-sandbox"},
        )
    )
    print(f"  ✓ sandbox id = {sandbox.id}")

    print(f"→ Uploading {len(PROJECT_FILES)} files to {REMOTE_DIR}/")
    sandbox.process.exec(f"mkdir -p {REMOTE_DIR}")
    for fname in PROJECT_FILES:
        sandbox.fs.upload_file(str(PROJECT_DIR / fname), f"{REMOTE_DIR}/{fname}")
        print(f"  ✓ {fname}")

    print("→ Installing dependencies (~30–90s)…")
    install = sandbox.process.exec(
        "pip install --quiet --upgrade pip && "
        "pip install --quiet "
        "'ag2[gemini,openai] @ git+https://github.com/ag2ai/ag2.git@main' "
        "fastapi uvicorn python-dotenv pydantic",
        cwd=REMOTE_DIR,
        timeout=600,
    )
    if install.exit_code != 0:
        print("  ✗ install failed:")
        print(install.result)
        print("  Sandbox left running for inspection. Delete via Daytona dashboard.")
        sys.exit(1)
    print("  ✓ deps installed")

    print(f"→ Starting uvicorn on :{PORT} in a background session…")
    sandbox.process.create_session(SERVER_SESSION)
    sandbox.process.execute_session_command(
        SERVER_SESSION,
        SessionExecuteRequest(
            command=(
                f"cd {REMOTE_DIR} && "
                f"uvicorn backend:app --host 0.0.0.0 --port {PORT} "
                f"> /tmp/uvicorn.log 2>&1"
            ),
            run_async=True,
        ),
    )

    print("  waiting for /healthz to respond (up to 60s)…")
    deadline = time.time() + 60
    last_err = ""
    while time.time() < deadline:
        time.sleep(2)
        check = sandbox.process.exec(
            f"curl -fsS http://localhost:{PORT}/healthz", timeout=10
        )
        if check.exit_code == 0 and '"ok":true' in (check.result or ""):
            print(f"  ✓ server up: {check.result.strip()}")
            break
        last_err = (check.result or "").strip()[-200:]
    else:
        print("  ✗ server never came up; tail of last curl output:")
        print(f"    {last_err}")
        print("  Tail uvicorn.log to debug:")
        log_dump = sandbox.process.exec("tail -n 80 /tmp/uvicorn.log")
        print(log_dump.result)
        sys.exit(1)

    print("→ Generating signed preview URL (24h)…")
    signed = sandbox.create_signed_preview_url(PORT, expires_in_seconds=24 * 3600)

    print(f"→ Registering vanity short URL ({SHORT_URL_CODE})…")
    short = make_short_url(signed.url, custom_shortcode=SHORT_URL_CODE)
    if short is None:
        print("  ⚠ custom shortcode unavailable — trying auto-generated…")
        short = make_short_url(signed.url)
    if short:
        print(f"  ✓ {short}")
    else:
        print("  ⚠ is.gd unreachable — only the long URL will be shown.")

    print()
    print("=" * 68)
    print("  ✓ Deployed!")
    print()
    if short:
        print(f"  Vanity URL : {short}")
    print(f"  Public URL : {signed.url}")
    print(f"  Sandbox ID : {sandbox.id}")
    print()
    print("  Tail server logs (from your machine):")
    print(
        f'    uv run python -c "from daytona import Daytona, DaytonaConfig; '
        f"s = Daytona(DaytonaConfig(api_key='$DAYTONA_API_KEY')).find('{sandbox.id}'); "
        f'print(s.process.exec(\'tail -n 60 /tmp/uvicorn.log\').result)"'
    )
    print()
    print("  Stop the sandbox:")
    print("    Visit https://app.daytona.io and delete it, or run:")
    print(
        f'    uv run python -c "from daytona import Daytona, DaytonaConfig; '
        f"Daytona(DaytonaConfig(api_key='$DAYTONA_API_KEY')).find('{sandbox.id}').delete()\""
    )
    print("=" * 68)


if __name__ == "__main__":
    main()
