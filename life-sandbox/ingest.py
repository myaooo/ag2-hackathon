"""Profile-source ingestion helpers.

Pure helpers (`_format_github`, `_is_linkedin_blocked`, `_strip_html`,
`_truncate`, `_parse_github_login`) are unit-tested. The async fetchers
(`fetch_github`, `fetch_linkedin`, `fetch_generic`) are thin orchestrators
that combine HTTP I/O with the pure helpers.

Each fetcher returns `str | None`. `None` means the source could not be
read (network error, login wall, malformed URL).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

MAX_EXTRACT_CHARS = 4000

_GITHUB_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/?#]+)")

_LINKEDIN_BLOCK_MARKERS = ("authwall", "<title>LinkedIn Login")

_DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Pure helpers (tested)
# ---------------------------------------------------------------------------


def _truncate(s: str) -> str:
    if len(s) <= MAX_EXTRACT_CHARS:
        return s
    return s[:MAX_EXTRACT_CHARS] + "…"


def _parse_github_login(url: str) -> str | None:
    m = _GITHUB_RE.match(url.strip())
    if not m:
        return None
    return m.group(1).rstrip("/")


def _format_github(user: dict[str, Any], repos: list[dict[str, Any]]) -> str:
    parts = [
        f"GitHub user: {user.get('login')}",
        f"Name: {user.get('name') or '(none)'}",
        f"Bio: {user.get('bio') or '(none)'}",
        f"Company: {user.get('company') or '(none)'}",
        f"Blog: {user.get('blog') or '(none)'}",
        f"Followers: {user.get('followers')}",
        f"Public repos: {user.get('public_repos')}",
        "",
        "Recent repos:",
    ]
    for r in repos[:10]:
        lang = r.get("language") or "—"
        desc = r.get("description") or ""
        parts.append(f"  - {r.get('name', '(unknown)')} ({lang}): {desc}")
    return _truncate("\n".join(parts))


def _is_linkedin_blocked(status: int, body: str) -> bool:
    if status != 200:
        return True
    return any(marker in body for marker in _LINKEDIN_BLOCK_MARKERS)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip())


# ---------------------------------------------------------------------------
# Async fetchers (smoke-tested manually; orchestrate I/O + pure helpers)
# ---------------------------------------------------------------------------


async def fetch_github(url: str) -> str | None:
    login = _parse_github_login(url)
    if not login:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            headers={
                "User-Agent": _DESKTOP_UA,
                "Accept": "application/vnd.github+json",
            },
        ) as client:
            user_resp = await client.get(f"https://api.github.com/users/{login}")
            user_resp.raise_for_status()
            repos_resp = await client.get(
                f"https://api.github.com/users/{login}/repos",
                params={"sort": "updated", "per_page": 10},
            )
            repos_resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return _format_github(user_resp.json(), repos_resp.json())


async def fetch_linkedin(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            headers={"User-Agent": _DESKTOP_UA},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if _is_linkedin_blocked(resp.status_code, resp.text):
        return None
    return _truncate(_strip_html(resp.text))


async def fetch_generic(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            headers={"User-Agent": _DESKTOP_UA},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        return None
    return _truncate(_strip_html(resp.text))
