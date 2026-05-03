# Life Sandbox — Link / Source Inputs

**Status:** Approved 2026-05-03

## Context

Life Sandbox is a multi-agent career-path recommender (AG2 Beta). Today the user fills a 6-field form (`stage`, `field`, `location`, `risk_tolerance`, `ambition`, `notes`) and the pipeline (coordinator → 3 parallel evaluators → decision) recommends 3 ranked paths. The form is the only signal — the agents have no other view of who the user is.

This change lets the user paste **LinkedIn URL, GitHub URL, and other source URLs** so the system can ingest real profile data. The imported content does two things at once: it pre-fills the form (the user can review/edit before submitting) AND it travels with the request as raw context for every agent in the pipeline.

The form stays. The two sliders (`risk_tolerance`, `ambition`) cannot be inferred from any profile, and even the inferable fields benefit from a human review pass.

## Goal

A **"Bring your real profile"** panel above the existing form with:

- **LinkedIn URL** — best-effort fetch with paste-text fallback when blocked.
- **GitHub URL** — REST-API fetch (clean and reliable, no auth needed for public profiles).
- **One "other" URL** — personal site / portfolio / blog (generic HTTP fetch).
- **Paste-text textarea** — always available; primary signal whenever fetches fail.

Click **Import** → backend fetches, summarizes via a new ingest agent, returns:
1. A typed summary (`field`, `stage`, `notes_seed`) that pre-fills the form.
2. A list of `ProfileExtract` entries that gets stashed and resubmitted with the form.

## Architecture

```
Browser
  ┌── User pastes URLs + optional text, clicks Import ──┐
  ▼                                                      │
POST /ingest  {linkedin_url?, github_url?, other_url?, pasted_text?}
  │
  ├─ ingest.fetch_github(url)  → text or None
  ├─ ingest.fetch_linkedin(url) → text or None  (best-effort; None on block)
  ├─ ingest.fetch_generic(url) → text or None
  ├─ pasted_text                → passed through
  │
  └─ ingest_agent.ask(<extracts>) → IngestSummary {field, stage, notes_seed}
  │
  ▼
{summary: IngestSummary, extracts: [ProfileExtract...]}
  │
  ▼
Browser writes summary into form; stashes extracts on form
  │
  ▼ User reviews, hits Explore my paths
POST /simulate/stream  {…form fields…, extracts: [ProfileExtract...]}
  │
  ▼
_profile_block(profile) appends "Source extracts:" section
  │
  ▼ existing 5-agent pipeline runs unchanged
```

The single insertion point in the agent layer is `_profile_block(profile)` in [backend.py:89](../../../backend.py#L89). Every agent already reads the same profile block — extending it once feeds extracts to the coordinator + 3 evaluators + decision agent in one stroke.

## Components

### `schemas.py` (additions)

```python
class ProfileExtract(BaseModel):
    source: Literal["linkedin", "github", "site", "paste"]
    url: str | None = None
    text: str                        # already truncated server-side
    fetched: bool = True             # False when fetch was attempted but blocked

class IngestRequest(BaseModel):
    linkedin_url: str | None = None
    github_url: str | None = None
    other_url: str | None = None
    pasted_text: str | None = None

class IngestSummary(BaseModel):
    field: str
    stage: Literal["high_school", "undergrad", "new_grad"]
    notes_seed: str

class IngestResponse(BaseModel):
    summary: IngestSummary
    extracts: list[ProfileExtract]

# UserProfile gains:
extracts: list[ProfileExtract] = Field(default_factory=list)
```

The `extracts` default is empty, so existing clients of `/simulate` and `/simulate/stream` keep working unchanged.

### `ingest.py` (new)

Pure async functions, all returning `str | None` (None means "could not fetch"):

- `fetch_github(url)` — parses `https://github.com/<login>`, hits `https://api.github.com/users/<login>` (and optionally `/users/<login>/repos?sort=updated&per_page=10`), formats the response as a compact text block: bio, blog, company, top languages, repo names + descriptions.
- `fetch_linkedin(url)` — single `httpx.GET` with realistic User-Agent and 5s timeout. Block detection: status ≠ 200, or response contains `<title>LinkedIn Login</title>` / `authwall`. Returns `None` on block.
- `fetch_generic(url)` — `httpx.GET` + a minimal HTML-to-text strip (BeautifulSoup or stdlib `html.parser`).
- `MAX_EXTRACT_CHARS = 4000` per source — applied as a final truncation so prompts don't explode.

### `agents.py` (additions)

```python
INGEST_PROMPT = (
    "You are a profile-summarization agent. Given raw extracts from a user's "
    "online profiles (GitHub, LinkedIn, personal site, or pasted text), produce: "
    "field (current field of study or work), stage (high_school | undergrad | new_grad), "
    "and notes_seed (2-4 sentences capturing goals, projects, interests, and "
    "anything an evaluator should know). Be concise and concrete; quote specific "
    "projects or roles where the source mentions them."
)

def build_ingest_agent() -> Agent:
    return Agent(
        name="ingest",
        prompt=INGEST_PROMPT,
        config=build_config(),
        response_schema=IngestSummary,
    )
```

Reuses `build_config()` — same provider/model/env wiring as the other 5 agents. No new env vars.

### `backend.py` (changes)

1. **New endpoint `POST /ingest`** — accepts `IngestRequest`, calls each `ingest.fetch_*` in parallel via `asyncio.gather`, builds the extract list, runs the ingest agent, returns `IngestResponse`.

2. **Modify `_profile_block(profile)`** — when `profile.extracts` is non-empty, append:

   ```
   Source extracts:
     [github] https://github.com/foo
       <truncated text>
     [linkedin] https://linkedin.com/in/foo  (could not fetch — pasted)
       <truncated text>
     ...
   ```

   Truncation already happened at fetch time, so this is a pure formatting step.

3. **No change to `/simulate` or `/simulate/stream`** — they receive the enriched `UserProfile` automatically because `extracts` is just another field.

### `frontend.html` (changes)

1. **New "Bring your real profile" panel** above the existing `<form id="profile-form">` with:
   - Three URL `<input type="url">` (LinkedIn, GitHub, Other).
   - One `<textarea>` for paste-text.
   - An **Import** button.
   - A status row that renders per-source pills: ✓ fetched / ⚠ blocked / – skipped.

2. **On Import:** `POST /ingest`, then:
   - Set `#field.value`, `#stage.value`, append `notes_seed` to `#notes.value`.
   - Render pills based on each extract's `source` + `fetched`.
   - Stash the raw `extracts` array on the form (e.g. `form.dataset.extracts = JSON.stringify(...)`).

3. **On Submit:** include `extracts: JSON.parse(form.dataset.extracts || '[]')` in the body sent to `/simulate/stream`.

### `pyproject.toml`

Add `httpx>=0.27` explicitly (it's transitive via ag2 today, but ingest depends on it directly). No other new dependencies. Optional: `beautifulsoup4` for `fetch_generic`'s HTML-to-text — if we want to avoid adding it, use stdlib `html.parser`.

## LinkedIn handling

LinkedIn aggressively blocks bots — a plain `httpx.GET` of a public profile typically returns an HTTP-999 / login-wall page. The plan handles this without paid scrapers or browser automation:

1. **Try once** with a realistic User-Agent and a 5s timeout.
2. **Detect block**: status ≠ 200, or response body contains `<title>LinkedIn Login</title>` / `authwall`.
3. **On block:** return `(text="", fetched=False)` for that extract; the UI shows a ⚠ pill and the user is expected to paste their profile content into the textarea. The pasted text takes over as the LinkedIn signal in subsequent prompts.

## Non-goals

- PDF resume upload — needs `pdfplumber` or multimodal input; separate task.
- Multiple "other URL" slots — one for now; "+ add another" is a trivial follow-up.
- Twitter/X — auth wall, low signal-to-effort.
- OAuth-based LinkedIn — real API requires app review, not feasible for the hackathon timeline.
- Caching ingest results across sessions.

## Verification

```bash
cd life-sandbox
~/.local/bin/uv sync
~/.local/bin/uv run python backend.py
```

Then in a browser at http://localhost:8765:

1. **Empty submit (regression check):** leave the import panel empty, fill the form as before, hit Explore. Behaviour identical to today; `extracts=[]` default keeps the existing pipeline unchanged.
2. **GitHub-only import:** paste `https://github.com/<login>`, hit Import. Form `field` / `stage` / `notes` populates. UI shows ✓ for github, – for the other slots. Submit and confirm prompts (via server logs or `/docs`) include the GitHub extract.
3. **LinkedIn fetch path:** paste a public LinkedIn URL. UI shows ✓ or ⚠. If ⚠, paste profile text → re-Import → extracts now include a `paste` source.
4. **`POST /ingest` directly via `/docs`:** issue an `IngestRequest` with all four fields populated; assert the response has 1–4 extracts and a populated `IngestSummary`.
5. **`/healthz`** returns the original payload — no provider/env changes.
