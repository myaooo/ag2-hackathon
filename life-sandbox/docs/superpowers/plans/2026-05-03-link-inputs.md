# Life Sandbox — Link Inputs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Bring your real profile" panel that ingests LinkedIn / GitHub / personal-site URLs (plus paste-text fallback) into the existing Life Sandbox career-recommender, both pre-filling the form and shipping raw extracts to the agent pipeline.

**Architecture:** New `POST /ingest` endpoint runs three async fetchers (`ingest.fetch_github`, `ingest.fetch_linkedin`, `ingest.fetch_generic`), summarizes the combined extracts via a new ingest agent (`Agent` with `response_schema=IngestSummary`), and returns both the summary (for form pre-fill) and the raw extracts (resubmitted with `/simulate/stream`). The existing 5-agent pipeline picks up extracts via a single insertion point: `_profile_block(profile)` in `backend.py`.

**Tech Stack:** Python 3.11+, AG2 Beta, FastAPI, httpx, BeautifulSoup4, pytest. Run-time: `uv` (already installed at `~/.local/bin/uv`).

**Spec:** [`life-sandbox/docs/superpowers/specs/2026-05-03-link-inputs-design.md`](../specs/2026-05-03-link-inputs-design.md)

**Working directory for all tasks:** `life-sandbox/`. All paths in this plan are relative to that directory unless absolute.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `schemas.py` | modify | Add `ProfileExtract`, `IngestRequest`, `IngestSummary`, `IngestResponse`; extend `UserProfile` with `extracts`. |
| `ingest.py` | new | Pure helpers (`_format_github`, `_is_linkedin_blocked`, `_strip_html`, `_truncate`) + async fetchers (`fetch_github`, `fetch_linkedin`, `fetch_generic`). |
| `agents.py` | modify | Add `INGEST_PROMPT` and `build_ingest_agent()`. |
| `backend.py` | modify | Add `POST /ingest`. Extend `_profile_block` to render `extracts`. |
| `frontend.html` | modify | Add "Bring your real profile" panel; wire Import → `/ingest`; stash extracts; include extracts in `/simulate/stream` body. |
| `pyproject.toml` | modify | Add `httpx`, `beautifulsoup4`; add `[project.optional-dependencies] dev = ["pytest"]`. |
| `tests/__init__.py` | new | Empty. |
| `tests/test_ingest.py` | new | Tests for ingest pure helpers. |
| `tests/test_profile_block.py` | new | Tests for `_profile_block` extracts rendering. |
| `tests/test_schemas.py` | new | Test backwards-compatible `UserProfile` shape (empty + populated extracts). |

---

## Task 1: Test infra + new dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`

- [ ] **Step 1: Add deps to `pyproject.toml`**

Modify the `[project]` table and add an optional-deps section. The full file should look like:

```toml
[project]
name = "life-sandbox"
version = "0.1.0"
description = "Multi-agent career-path sandbox on AG2 Beta — coordinator + parallel evaluators + decision agent"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "ag2[gemini,openai] @ git+https://github.com/ag2ai/ag2.git@main",
    "beautifulsoup4>=4.12",
    "fastapi>=0.115.0",
    "httpx>=0.27",
    "python-dotenv>=1.0.0",
    "uvicorn>=0.34.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

- [ ] **Step 2: Create empty `tests/__init__.py`**

```python
```

(Empty file — just creates the package marker.)

- [ ] **Step 3: Sync deps**

Run: `~/.local/bin/uv sync --extra dev`
Expected: completes without error; `uv.lock` updated; `pytest`, `beautifulsoup4`, `httpx` appear in the installed packages list.

- [ ] **Step 4: Verify pytest works**

Run: `~/.local/bin/uv run pytest --version`
Expected: prints a pytest version string (e.g. `pytest 8.x.x`).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py
git commit -m "chore(life-sandbox): add httpx, bs4, pytest dev deps"
```

---

## Task 2: Schemas

**Files:**
- Modify: `schemas.py`
- Create: `tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schemas.py`:

```python
"""Backwards-compat + new-shape tests for UserProfile and ingest schemas."""

from schemas import (
    IngestRequest,
    IngestResponse,
    IngestSummary,
    ProfileExtract,
    UserProfile,
)


def test_user_profile_default_extracts_empty():
    profile = UserProfile(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
    )
    assert profile.extracts == []


def test_user_profile_accepts_extracts():
    profile = UserProfile(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
        extracts=[
            ProfileExtract(source="github", url="https://github.com/x", text="bio"),
            ProfileExtract(source="paste", text="hand-typed"),
        ],
    )
    assert len(profile.extracts) == 2
    assert profile.extracts[0].source == "github"
    assert profile.extracts[1].url is None


def test_ingest_request_all_optional():
    req = IngestRequest()
    assert req.linkedin_url is None
    assert req.github_url is None
    assert req.other_url is None
    assert req.pasted_text is None


def test_ingest_summary_shape():
    summary = IngestSummary(field="CS", stage="undergrad", notes_seed="hello")
    assert summary.stage == "undergrad"


def test_ingest_response_shape():
    resp = IngestResponse(
        summary=IngestSummary(field="CS", stage="undergrad", notes_seed="x"),
        extracts=[ProfileExtract(source="github", url="u", text="t")],
    )
    assert resp.summary.field == "CS"
    assert len(resp.extracts) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `~/.local/bin/uv run pytest tests/test_schemas.py -v`
Expected: ImportError or "cannot import name `ProfileExtract`" — the new types don't exist yet.

- [ ] **Step 3: Add the schemas**

Modify `schemas.py`. Add these classes (place them at the bottom, after `DecisionOutput`):

```python
# ---------------------------------------------------------------------------
# Profile ingest — pasted URLs / text → enrich the UserProfile + agent prompts
# ---------------------------------------------------------------------------


class ProfileExtract(BaseModel):
    source: Annotated[Literal["linkedin", "github", "site", "paste"], Field(description="Origin of this extract")]
    url: Annotated[str | None, Field(default=None, description="The URL the text was fetched from, if any")]
    text: Annotated[str, Field(description="Already-truncated extract text (≤ ~4000 chars)")]
    fetched: Annotated[bool, Field(default=True, description="False when fetch was attempted but blocked (e.g. LinkedIn login wall)")]


class IngestRequest(BaseModel):
    linkedin_url: Annotated[str | None, Field(default=None)]
    github_url: Annotated[str | None, Field(default=None)]
    other_url: Annotated[str | None, Field(default=None, description="Personal site / portfolio / blog URL")]
    pasted_text: Annotated[str | None, Field(default=None, description="Free-form pasted profile text — used when fetches are blocked")]


class IngestSummary(BaseModel):
    field: Annotated[str, Field(description="Inferred field of study or work")]
    stage: Annotated[Literal["high_school", "undergrad", "new_grad"], Field(description="Inferred career stage")]
    notes_seed: Annotated[str, Field(description="2-4 sentence summary of goals, projects, interests")]


class IngestResponse(BaseModel):
    summary: IngestSummary
    extracts: list[ProfileExtract]
```

Then modify `UserProfile` to add the `extracts` field. The class becomes:

```python
class UserProfile(BaseModel):
    stage: Literal["high_school", "undergrad", "new_grad"]
    field: Annotated[str, Field(description="Current field of study or interest, e.g. 'Computer Science'")]
    location: Annotated[str, Field(description="Preferred location, e.g. 'NYC' or 'Remote'")]
    risk_tolerance: Annotated[float, Field(ge=0.0, le=1.0, description="0 = avoid all risk, 1 = embrace volatility")]
    ambition: Annotated[float, Field(ge=0.0, le=1.0, description="0 = stable plateau is fine, 1 = optimize for growth")]
    notes: Annotated[str, Field(default="", description="Free-form notes about goals, constraints, interests")]
    extracts: Annotated[list["ProfileExtract"], Field(default_factory=list, description="Source extracts attached during ingest, passed through to the agents")]
```

Note the forward reference `"ProfileExtract"` — `ProfileExtract` is defined below `UserProfile`. Pydantic resolves forward refs automatically with `from __future__ import annotations` already at the top of the file.

- [ ] **Step 4: Run the test to verify it passes**

Run: `~/.local/bin/uv run pytest tests/test_schemas.py -v`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add schemas.py tests/test_schemas.py
git commit -m "feat(life-sandbox): add ProfileExtract + Ingest schemas; UserProfile.extracts"
```

---

## Task 3: Ingest fetchers + pure helpers

**Files:**
- Create: `ingest.py`
- Create: `tests/test_ingest.py`

The async fetchers wrap `httpx`; the **pure helpers** (`_format_github`, `_is_linkedin_blocked`, `_strip_html`, `_truncate`) carry all the logic worth testing. We TDD the helpers and ship the async fetchers as thin orchestrators.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest.py`:

```python
"""Pure-helper tests for ingest. Fetchers themselves are smoke-tested manually."""

from ingest import (
    MAX_EXTRACT_CHARS,
    _format_github,
    _is_linkedin_blocked,
    _parse_github_login,
    _strip_html,
    _truncate,
)


def test_truncate_short_string_unchanged():
    assert _truncate("hello") == "hello"


def test_truncate_long_string_clipped():
    s = "x" * (MAX_EXTRACT_CHARS + 100)
    out = _truncate(s)
    assert len(out) == MAX_EXTRACT_CHARS + 1  # +1 for the ellipsis
    assert out.endswith("…")


def test_parse_github_login_basic():
    assert _parse_github_login("https://github.com/torvalds") == "torvalds"


def test_parse_github_login_with_trailing_slash():
    assert _parse_github_login("https://github.com/octocat/") == "octocat"


def test_parse_github_login_ignores_repo_path():
    assert _parse_github_login("https://github.com/torvalds/linux") == "torvalds"


def test_parse_github_login_returns_none_on_non_github():
    assert _parse_github_login("https://example.com/foo") is None


def test_format_github_includes_bio_and_repos():
    user = {
        "login": "octocat",
        "name": "The Octocat",
        "bio": "GitHub mascot",
        "company": "@github",
        "blog": "https://github.blog",
        "followers": 1000,
        "public_repos": 8,
    }
    repos = [
        {"name": "Hello-World", "language": "Ruby", "description": "First repo"},
        {"name": "Spoon-Knife", "language": None, "description": None},
    ]
    out = _format_github(user, repos)
    assert "octocat" in out
    assert "GitHub mascot" in out
    assert "Hello-World (Ruby)" in out
    assert "Spoon-Knife (—)" in out  # None language renders as em-dash


def test_is_linkedin_blocked_on_non_200():
    assert _is_linkedin_blocked(status=403, body="whatever") is True


def test_is_linkedin_blocked_on_authwall_marker():
    assert _is_linkedin_blocked(status=200, body="<html>... authwall ...</html>") is True


def test_is_linkedin_blocked_on_login_title():
    assert _is_linkedin_blocked(status=200, body="<title>LinkedIn Login</title>") is True


def test_is_linkedin_blocked_on_clean_profile():
    body = "<html><h1>Jane Doe</h1><p>Software engineer</p></html>"
    assert _is_linkedin_blocked(status=200, body=body) is False


def test_strip_html_removes_tags_scripts_and_blank_lines():
    html = """
    <html>
      <head><style>body{color:red}</style></head>
      <body>
        <h1>Title</h1>
        <script>alert(1)</script>
        <p>Hello   world</p>
        <p></p>
      </body>
    </html>
    """
    out = _strip_html(html)
    assert "Title" in out
    assert "Hello   world" in out
    assert "alert" not in out
    assert "color:red" not in out
    # No blank lines between content
    for line in out.splitlines():
        assert line.strip() != ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `~/.local/bin/uv run pytest tests/test_ingest.py -v`
Expected: `ModuleNotFoundError: No module named 'ingest'`.

- [ ] **Step 3: Create `ingest.py`**

```python
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
        parts.append(f"  - {r['name']} ({lang}): {desc}")
    return _truncate("\n".join(parts))


def _is_linkedin_blocked(status: int, body: str) -> bool:
    if status != 200:
        return True
    return any(marker in body for marker in _LINKEDIN_BLOCK_MARKERS)


def _strip_html(html: str) -> str:
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
        async with httpx.AsyncClient(timeout=5.0) as client:
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `~/.local/bin/uv run pytest tests/test_ingest.py -v`
Expected: 11 tests PASS.

- [ ] **Step 5: Smoke-test `fetch_github` against the real API**

Run: `~/.local/bin/uv run python -c "import asyncio; from ingest import fetch_github; print((asyncio.run(fetch_github('https://github.com/torvalds')) or '')[:500])"`
Expected: a 200-500 char block of GitHub profile text starting with `GitHub user: torvalds`. (Skip this step if you're offline; the unit tests already covered the formatter.)

- [ ] **Step 6: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "feat(life-sandbox): add profile-source ingest fetchers + pure helpers"
```

---

## Task 4: Ingest agent + extract-aware `_profile_block`

**Files:**
- Modify: `agents.py`
- Modify: `backend.py:89`
- Create: `tests/test_profile_block.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_block.py`:

```python
"""Tests for backend._profile_block — verifies extract rendering."""

from backend import _profile_block
from schemas import ProfileExtract, UserProfile


def _base_profile(**overrides) -> UserProfile:
    base = dict(
        stage="undergrad",
        field="CS",
        location="NYC",
        risk_tolerance=0.4,
        ambition=0.7,
    )
    base.update(overrides)
    return UserProfile(**base)


def test_profile_block_no_extracts_omits_section():
    block = _profile_block(_base_profile())
    assert "Source extracts" not in block
    # Sanity: the original fields still render
    assert "stage" in block
    assert "field" in block


def test_profile_block_with_extracts_renders_section():
    profile = _base_profile(
        extracts=[
            ProfileExtract(source="github", url="https://github.com/foo", text="GitHub user: foo\nBio: hacker"),
            ProfileExtract(source="linkedin", url="https://linkedin.com/in/foo", text="", fetched=False),
            ProfileExtract(source="paste", text="I want to work in AI"),
        ]
    )
    block = _profile_block(profile)
    assert "Source extracts:" in block
    assert "[github] https://github.com/foo" in block
    assert "GitHub user: foo" in block
    assert "[linkedin] https://linkedin.com/in/foo  (could not fetch — pasted)" in block
    assert "[paste]" in block
    assert "I want to work in AI" in block
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `~/.local/bin/uv run pytest tests/test_profile_block.py -v`
Expected: the no-extracts test PASSES (current behaviour). The with-extracts test FAILS — `Source extracts:` is not in the block.

- [ ] **Step 3: Extend `_profile_block` in `backend.py`**

Replace the existing function (currently at `backend.py:89`):

```python
def _profile_block(profile: UserProfile) -> str:
    block = (
        "User profile:\n"
        f"  stage          : {profile.stage}\n"
        f"  field          : {profile.field}\n"
        f"  location       : {profile.location}\n"
        f"  risk_tolerance : {profile.risk_tolerance:.2f}  (0=avoid risk, 1=embrace volatility)\n"
        f"  ambition       : {profile.ambition:.2f}  (0=stable, 1=optimize growth)\n"
        f"  notes          : {profile.notes or '(none)'}\n"
    )
    if profile.extracts:
        lines = ["", "Source extracts:"]
        for ex in profile.extracts:
            header = f"  [{ex.source}]"
            if ex.url:
                header += f" {ex.url}"
            if not ex.fetched:
                header += "  (could not fetch — pasted)"
            lines.append(header)
            for text_line in (ex.text or "").splitlines():
                lines.append(f"    {text_line}")
        block += "\n".join(lines) + "\n"
    return block
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/bin/uv run pytest tests/test_profile_block.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Add the ingest agent in `agents.py`**

Add the prompt and factory at the bottom of `agents.py`. Also import `IngestSummary`:

At the top, change the schemas import to include `IngestSummary`:

```python
from schemas import (
    CareerOutput,
    DecisionOutput,
    FinanceOutput,
    IngestSummary,
    PathCandidates,
    RiskOutput,
)
```

After `DECISION_PROMPT` (and before the factories block), add:

```python
INGEST_PROMPT = (
    "You are a profile-summarization agent. Given raw extracts from a user's "
    "online profiles (GitHub, LinkedIn, personal site, or pasted text), produce:\n"
    "  - field: the user's current field of study or work (e.g. 'Computer Science', 'Finance')\n"
    "  - stage: one of high_school | undergrad | new_grad — pick the closest fit\n"
    "  - notes_seed: 2-4 concrete sentences capturing goals, projects, roles, and interests. "
    "Quote specific projects or company names where the source mentions them. Avoid generic filler.\n"
    "If extracts are sparse or missing, make conservative best guesses and keep notes_seed short."
)
```

After `build_decision_agent()`, append:

```python
def build_ingest_agent() -> Agent:
    return Agent(
        name="ingest",
        prompt=INGEST_PROMPT,
        config=build_config(),
        response_schema=IngestSummary,
    )
```

- [ ] **Step 6: Smoke check — agent factory builds without errors**

Run: `~/.local/bin/uv run python -c "from agents import build_ingest_agent; print(build_ingest_agent().name)"`
Expected: prints `ingest`. (Requires `GEMINI_API_KEY` or `OPENAI_API_KEY` in `.env`; the factory validates env wiring.)

- [ ] **Step 7: Commit**

```bash
git add agents.py backend.py tests/test_profile_block.py
git commit -m "feat(life-sandbox): ingest agent + extract-aware _profile_block"
```

---

## Task 5: `POST /ingest` endpoint

**Files:**
- Modify: `backend.py`

- [ ] **Step 1: Add imports + endpoint to `backend.py`**

In the `from agents import (...)` block, add `build_ingest_agent`:

```python
from agents import (
    build_career_evaluator,
    build_config,
    build_coordinator,
    build_decision_agent,
    build_finance_evaluator,
    build_ingest_agent,
    build_risk_evaluator,
)
```

In the `from schemas import (...)` block, add the new ingest types:

```python
from schemas import (
    CareerOutput,
    DecisionOutput,
    FinanceOutput,
    IngestRequest,
    IngestResponse,
    IngestSummary,
    PathCandidates,
    ProfileExtract,
    RiskOutput,
    UserProfile,
)
```

Below the existing module-level agent builds, add:

```python
ingest_agent = build_ingest_agent()
```

Add the `import ingest` near the other top-level imports:

```python
import ingest
```

Add the endpoint just before `if __name__ == "__main__":`:

```python
@app.post("/ingest", response_model=IngestResponse)
async def ingest_sources(req: IngestRequest) -> IngestResponse:
    """Fetch each provided source, summarize the bundle, return both the summary
    (for form pre-fill) and the raw extracts (to resubmit with /simulate)."""

    extracts: list[ProfileExtract] = []

    if req.github_url:
        text = await ingest.fetch_github(req.github_url)
        extracts.append(
            ProfileExtract(
                source="github",
                url=req.github_url,
                text=text or "",
                fetched=text is not None,
            )
        )

    if req.linkedin_url:
        text = await ingest.fetch_linkedin(req.linkedin_url)
        extracts.append(
            ProfileExtract(
                source="linkedin",
                url=req.linkedin_url,
                text=text or "",
                fetched=text is not None,
            )
        )

    if req.other_url:
        text = await ingest.fetch_generic(req.other_url)
        extracts.append(
            ProfileExtract(
                source="site",
                url=req.other_url,
                text=text or "",
                fetched=text is not None,
            )
        )

    if req.pasted_text:
        extracts.append(
            ProfileExtract(
                source="paste",
                url=None,
                text=ingest._truncate(req.pasted_text),
                fetched=True,
            )
        )

    if not extracts:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: linkedin_url, github_url, other_url, pasted_text.",
        )

    prompt_parts = ["Source extracts:"]
    for ex in extracts:
        header = f"[{ex.source}]"
        if ex.url:
            header += f" {ex.url}"
        if not ex.fetched:
            header += "  (could not fetch)"
        prompt_parts.append(header)
        prompt_parts.append(ex.text or "(empty)")
        prompt_parts.append("")
    prompt = "\n".join(prompt_parts)

    try:
        reply = await ingest_agent.ask(prompt)
        summary: IngestSummary = await reply.content(retries=2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ingest agent failed: {exc}") from exc

    return IngestResponse(summary=summary, extracts=extracts)
```

- [ ] **Step 2: Smoke test the endpoint via FastAPI's interactive docs**

Run: `~/.local/bin/uv run python backend.py`
In a browser, open http://localhost:8765/docs.
Expand **POST /ingest**, click **Try it out**, send:

```json
{"github_url": "https://github.com/torvalds"}
```

Expected: 200 response with `summary.field`, `summary.stage`, `summary.notes_seed` populated (the agent's best guess) and one `extracts` entry with `source: "github"`, `fetched: true`, and a non-empty `text`.

Stop the server with Ctrl+C.

- [ ] **Step 3: Negative test — empty body**

Re-start the server, in `/docs` send `{}` to `/ingest`. Expected: 400 with detail `"Provide at least one of..."`.

- [ ] **Step 4: Commit**

```bash
git add backend.py
git commit -m "feat(life-sandbox): POST /ingest endpoint"
```

---

## Task 6: Frontend — "Bring your real profile" panel

**Files:**
- Modify: `frontend.html`

The panel goes **above** `<form id="profile-form">`. It has its own POST → `/ingest`, and on success it pre-fills the form fields and stashes the extracts on the form for the next submit.

- [ ] **Step 1: Add CSS for the import panel and pills**

Inside the existing `<style>` block (search for `.field { margin-bottom: 18px; }` and add the rules just below the existing form styles):

```css
.import-panel {
  margin-bottom: 24px;
  padding: 16px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: rgba(255,255,255,0.02);
}
.import-panel h3 {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-muted);
}
.import-panel .field { margin-bottom: 12px; }
.import-actions { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.import-actions button {
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font: inherit;
}
.import-actions button:hover:not(:disabled) { background: rgba(255,255,255,0.05); }
.import-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pill {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-muted);
}
.pill.ok { color: #6ee7b7; border-color: #6ee7b7; }
.pill.warn { color: #fbbf24; border-color: #fbbf24; }
.pill.skip { opacity: 0.5; }
```

- [ ] **Step 2: Add the panel HTML**

Find `<section class="panel form-panel">` and insert the import panel immediately before it (still inside `<main>`):

```html
<section class="panel form-panel import-panel">
  <h3>Bring your real profile (optional)</h3>
  <div class="field">
    <label for="linkedin_url">LinkedIn URL</label>
    <input type="url" id="linkedin_url" name="linkedin_url"
      placeholder="https://www.linkedin.com/in/your-handle" />
  </div>
  <div class="field">
    <label for="github_url">GitHub URL</label>
    <input type="url" id="github_url" name="github_url"
      placeholder="https://github.com/your-handle" />
  </div>
  <div class="field">
    <label for="other_url">Other URL (personal site, portfolio, blog)</label>
    <input type="url" id="other_url" name="other_url"
      placeholder="https://yourdomain.com" />
  </div>
  <div class="field">
    <label for="pasted_text">Or paste profile text</label>
    <textarea id="pasted_text" name="pasted_text" rows="4"
      placeholder="Paste your résumé, About section, or anything you'd want a career advisor to see."></textarea>
  </div>
  <div class="import-actions">
    <button type="button" id="import-btn">Import &amp; pre-fill</button>
    <div class="pills" id="import-pills"></div>
  </div>
</section>
```

- [ ] **Step 3: Add the Import handler JS**

Find the `<script>` block. Locate the existing form-submit listener (search for `$('#profile-form').addEventListener('submit'`). Just **above** that listener, add:

```javascript
// ---- Import panel: POST /ingest, pre-fill form, stash extracts ----
let stashedExtracts = [];

function renderPills(extracts) {
  const sources = ['linkedin', 'github', 'site', 'paste'];
  const found = Object.fromEntries(extracts.map(e => [e.source, e]));
  const labelByPanel = { linkedin: 'LinkedIn', github: 'GitHub', site: 'Other URL', paste: 'Paste' };
  const pills = sources.map(src => {
    const ex = found[src];
    if (!ex) return `<span class="pill skip">– ${labelByPanel[src]}</span>`;
    if (ex.fetched) return `<span class="pill ok">✓ ${labelByPanel[src]}</span>`;
    return `<span class="pill warn">⚠ ${labelByPanel[src]} blocked</span>`;
  });
  $('#import-pills').innerHTML = pills.join('');
}

$('#import-btn').addEventListener('click', async () => {
  const btn = $('#import-btn');
  const body = {
    linkedin_url: $('#linkedin_url').value.trim() || null,
    github_url:   $('#github_url').value.trim()   || null,
    other_url:    $('#other_url').value.trim()    || null,
    pasted_text:  $('#pasted_text').value.trim()  || null,
  };
  const anyProvided = Object.values(body).some(v => v);
  if (!anyProvided) {
    $('#import-pills').innerHTML = '<span class="pill warn">paste at least one source</span>';
    return;
  }
  btn.disabled = true;
  btn.textContent = 'Importing...';
  try {
    const res = await fetch('/ingest', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.text();
      $('#import-pills').innerHTML = `<span class="pill warn">${res.status}: ${err.slice(0, 80)}</span>`;
      return;
    }
    const data = await res.json();
    // Pre-fill
    $('#field').value = data.summary.field || $('#field').value;
    $('#stage').value = data.summary.stage || $('#stage').value;
    const seed = data.summary.notes_seed || '';
    const existing = $('#notes').value.trim();
    $('#notes').value = existing ? `${existing}\n\n${seed}` : seed;
    // Stash
    stashedExtracts = data.extracts || [];
    renderPills(stashedExtracts);
  } catch (e) {
    $('#import-pills').innerHTML = `<span class="pill warn">network error</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import & pre-fill';
  }
});
```

- [ ] **Step 4: Include `extracts` in the simulate POST body**

Find the existing form-submit listener. Locate the `body` object (look for `stage:`, `field:`, etc. — currently around line 517). Add `extracts` as the last field:

```javascript
const body = {
  stage:          $('#stage').value,
  field:          $('#field').value.trim(),
  location:       $('#location').value.trim(),
  risk_tolerance: parseFloat($('#risk_tolerance').value),
  ambition:       parseFloat($('#ambition').value),
  notes:          $('#notes').value.trim(),
  extracts:       stashedExtracts,
};
```

- [ ] **Step 5: Manual smoke test**

Run: `~/.local/bin/uv run python backend.py`
Open http://localhost:8765 in a browser.

Verify:
- The "Bring your real profile" panel renders above the form.
- Paste `https://github.com/torvalds` into GitHub URL, click **Import & pre-fill**. The field/stage/notes fields populate; pills show ✓ GitHub and – for the others.
- Click **Explore my paths**. The pipeline runs as before; results appear.
- Open the Network tab and inspect the `/simulate/stream` request body — confirm `extracts` is present and contains the GitHub entry.

Stop the server with Ctrl+C.

- [ ] **Step 6: Commit**

```bash
git add frontend.html
git commit -m "feat(life-sandbox): import panel — pre-fill form + stash extracts"
```

---

## Task 7: End-to-end verification

**Files:** none (manual run-through).

This task is the formal sign-off — run the full feature in the browser the way a user would.

- [ ] **Step 1: Start the server**

Run: `~/.local/bin/uv run python backend.py`
Open http://localhost:8765.

- [ ] **Step 2: Regression — empty import, normal submit**

Leave the import panel empty. Fill the form (e.g. undergrad / Computer Science / NYC / sliders 0.4 / 0.7 / blank notes). Hit **Explore my paths**. Confirm the original pipeline runs: SSE stages flip from `candidates` → `evaluating` → individual evaluator events → `deciding` → `decision`. Three ranked path cards render.

- [ ] **Step 3: GitHub-only import**

Paste a real GitHub URL into the GitHub field. Click **Import & pre-fill**. Confirm:
- field/stage/notes populate.
- Pills: ✓ GitHub, – LinkedIn, – Other URL, – Paste.

Hit **Explore my paths**. Confirm in the browser DevTools Network tab that the `/simulate/stream` request body contains an `extracts` array with one `github` entry.

- [ ] **Step 4: LinkedIn fetch path**

Paste a public LinkedIn profile URL (your own or any public profile). Click Import. Pills show ✓ or ⚠. If ⚠ (expected for most LinkedIn URLs), paste profile text into the textarea and click Import again. Pills now show ⚠ LinkedIn + ✓ Paste, and notes/field/stage are populated from the paste content.

- [ ] **Step 5: Direct API test of `/ingest`**

In a separate terminal:

```bash
curl -s -X POST http://localhost:8765/ingest \
  -H 'Content-Type: application/json' \
  -d '{"github_url":"https://github.com/torvalds","pasted_text":"I want to work on operating systems"}' \
  | python -m json.tool
```

Expected: a JSON object with `summary` (field, stage, notes_seed) and `extracts` (length 2 — github + paste).

- [ ] **Step 6: `/healthz` regression**

Run: `curl -s http://localhost:8765/healthz | python -m json.tool`
Expected: `{"ok": true, "provider": "...", "model": "..."}` — unchanged from before this feature.

- [ ] **Step 7: Run the full test suite once more**

Run: `~/.local/bin/uv run pytest -v`
Expected: all tests pass (3 test files, ~18 tests total).

- [ ] **Step 8: Stop the server and final commit**

```bash
git add -A   # picks up any in-tree fix-ups from the smoke run
git status   # should be clean if nothing new — fine either way
```

If `git status` shows nothing new, this task is just a sign-off (no commit needed). Otherwise commit the fix-up:

```bash
git commit -m "chore(life-sandbox): post-E2E fix-ups for link inputs"
```

---

## Self-Review

**Spec coverage:**
- LinkedIn URL with paste fallback → Tasks 3 (`fetch_linkedin` + `_is_linkedin_blocked`), 5 (paste extract), 6 (UI), 7 (E2E step 4). ✓
- GitHub URL via REST API → Tasks 3, 5, 6, 7. ✓
- Other URL (generic fetch) → Tasks 3 (`fetch_generic`), 5, 6. ✓
- Paste-text fallback always available → Tasks 5 (paste branch), 6 (textarea). ✓
- `IngestSummary` → form pre-fill → Tasks 5, 6 (Step 3 handler). ✓
- Extracts pass-through to all 5 existing agents via `_profile_block` → Task 4 (Steps 3–4). ✓
- Backwards compat: empty `extracts` default → Task 2 (test_user_profile_default_extracts_empty), Task 4 (test_profile_block_no_extracts_omits_section), Task 7 Step 2. ✓
- Non-goals (PDF, OAuth, multiple "other" slots) explicitly skipped — not in any task. ✓

**Placeholder scan:** no TBDs / TODOs / "implement later" / "similar to Task N". Each code step shows the actual code. ✓

**Type / name consistency:**
- `MAX_EXTRACT_CHARS`, `_truncate`, `_parse_github_login`, `_format_github`, `_is_linkedin_blocked`, `_strip_html` — defined Task 3, used identically in Tasks 4–6.
- `ProfileExtract.source` literal `"linkedin" | "github" | "site" | "paste"` — Task 2 schema matches Task 5 endpoint usage and Task 6 UI labels. ✓
- `IngestSummary.stage` literal matches `UserProfile.stage` literal. ✓
- `_profile_block` signature unchanged; only the body extends. ✓
