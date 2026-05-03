# Life Sandbox

Multi-agent career-path sandbox on **AG2 Beta**. Takes a user profile and
returns the top 3 career paths with quantitative trade-offs (expected income,
risk, growth) and an explanation tailored to the user's risk tolerance.

Built for the [AG2 Hackathon](https://luma.com/42lzgbrz). Demonstrates:

- **Multi-agent orchestration** — 1 coordinator + 3 parallel domain evaluators + 1 decision agent.
- **Structured output everywhere** — every agent has a Pydantic `response_schema`, so the API is fully typed end-to-end.
- **Quant-flavored modeling** — each path has a 5-year salary mean curve + stddev band, expected value, layoff hazard, and 5-year ruin probability.

## Architecture

```
POST /simulate (UserProfile)
        ↓
   coordinator                    →  PathCandidates (3 archetypes)
        ↓
   asyncio.gather(
       career_eval,               →  CareerOutput   ┐
       finance_eval,              →  FinanceOutput  ├ all run in parallel
       risk_eval,                 →  RiskOutput     ┘
   )
        ↓
   decision_agent                 →  DecisionOutput (top 3 ranked)
```

5 LLM calls per request (1 coordinator + 3 evaluators in parallel + 1 decision).

## Local development setup

### Prerequisites

- **Python 3.11+** — `python3 --version` to check.
- **[uv](https://github.com/astral-sh/uv)** (recommended) — one-line install: `curl -LsSf https://astral.sh/uv/install.sh | sh`. Plain `pip` works too.
- **An LLM API key** for one of the supported providers:
  - **OpenAI-compatible** (the `.env.example` default) — wired through **OpenRouter** (`base_url=https://openrouter.ai/api/v1`), so the `OPENAI_API_KEY` slot wants an OpenRouter key from <https://openrouter.ai/keys>. The default model `google/gemini-2.5-pro` is OpenRouter's Gemini route. To use the **official OpenAI API** directly, edit `agents.py` to remove the `base_url=` argument and put a real `sk-...` key in `OPENAI_API_KEY`.
  - **Gemini direct** — set `LLM_PROVIDER=gemini` and supply `GEMINI_API_KEY` from <https://aistudio.google.com/apikey>. (The Python code's fallback default when no `.env` is present is also `gemini`.)

### 1. Install dependencies

From the repo root:

```bash
cd life-sandbox
uv sync                     # creates .venv and installs everything from pyproject.toml + uv.lock
# OR with pip:
# python -m venv .venv && source .venv/bin/activate && pip install -e .
```

`ag2` is installed from the GitHub `main` branch (see `pyproject.toml`), so the
first sync clones the repo — give it ~30 seconds.

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`. The template ships with the OpenRouter setup pre-selected:

```ini
LLM_PROVIDER=openai                 # uses OpenRouter (base_url set in agents.py)
MODEL=google/gemini-2.5-pro         # any OpenRouter model id works
OPENAI_API_KEY=sk-or-...            # OpenRouter key
# PORT=8765                         # optional
```

Or switch to Gemini direct:

```ini
LLM_PROVIDER=gemini
MODEL=gemini-2.5-flash              # optional override
GEMINI_API_KEY=AIza...
```

The server **fails fast** at startup if the key for the selected provider
is missing — there's no silent fallback.

### 3. Run the server

```bash
# Production-style (single worker, no reload)
uv run python backend.py

# Recommended for dev — auto-reload on code changes
uv run uvicorn backend:app --reload --port 8765
```

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:8765
INFO:     Started reloader process ...
```

### 4. Verify it's alive

| URL | What to expect |
|---|---|
| <http://localhost:8765/>           | The entry-page form |
| <http://localhost:8765/docs>       | FastAPI's interactive OpenAPI UI — try a request from the browser |
| <http://localhost:8765/healthz>    | `{"ok": true, "provider": "gemini", "model": "..."}` |

End-to-end smoke test from the terminal:

```bash
curl -s -X POST http://localhost:8765/simulate \
  -H 'Content-Type: application/json' \
  -d '{
        "stage": "undergrad",
        "field": "Computer Science",
        "location": "NYC",
        "risk_tolerance": 0.4,
        "ambition": 0.7,
        "notes": "Curious about AI but worried about hype cycles"
      }' | jq '.top3[].title'
```

Expected: 3 path titles, total request time roughly 8–20s depending on
provider and model. Most of that wall time is the parallel evaluator round.

### 5. Iterating

- **Tweak a prompt** → save `agents.py`, uvicorn reloads, hit the form again.
- **Tweak the schema** → save `schemas.py`, reload — the OpenAPI UI at `/docs` regenerates instantly so the frontend teammate sees the new shape immediately.
- **Tweak the pipeline / FastAPI route** → save `backend.py`, reload.
- **Tweak the entry page** → save `frontend.html`, hard-refresh the browser (no reload needed; the file is served on every request).

### 6. Common issues

| Symptom | Fix |
|---|---|
| `SystemExit: GEMINI_API_KEY is required ...` | Missing or misnamed env var. Check `.env` and that you launched from `life-sandbox/` so `python-dotenv` finds it. |
| `ImportError: ... requires optional dependencies. Install with pip install "ag2[gemini]"` | `uv sync` didn't run, or you're running outside the project venv. Re-run `uv sync` and use `uv run …`. |
| First request hangs ~30s, then errors | Provider rate-limit or model name typo. Check `/healthz` for the active model. |
| `pydantic.ValidationError` from a `reply.content()` call | The model returned a malformed structured response. The pipeline already retries twice; if it persists, look at the prompt for that agent or try a stronger model via `MODEL=...`. |
| CORS error in the browser console | Backend wide-opens CORS already — make sure the frontend dev server is hitting the same origin or set the `fetch` URL explicitly. |
| Port 8765 already in use | `PORT=9000 uv run python backend.py` or kill the other process. |

### 7. Project layout

```
life-sandbox/
├── pyproject.toml      # dependencies (ag2, fastapi, uvicorn)
├── uv.lock             # pinned dependency versions
├── .env.example        # env template — copy to .env
├── schemas.py          # Pydantic models (request, intermediate, response)
├── agents.py           # 5 agent factories + provider/config selection
├── backend.py          # FastAPI app + orchestration pipeline + SSE endpoint
├── frontend.html       # Self-contained entry-page form (served at GET /)
├── README.md           # ← this file
└── DESIGN.md           # design + implementation summary
```

## API

The backend exposes a typed JSON API plus a self-contained entry-page UI.
See `/docs` for the live OpenAPI UI.

### `GET /`

Serves `frontend.html` — a single-page entry form (stage, field, location,
risk-tolerance / ambition sliders, notes) that submits to
`POST /simulate/stream` and renders the 3 ranked path cards live with
Chart.js salary curves and stddev bands. Useful for demos and quick manual
testing; the dedicated frontend (separate repo / branch) can ignore this
endpoint.

### `POST /simulate`

**Request body** (`UserProfile`):

```json
{
  "stage": "undergrad",
  "field": "Computer Science",
  "location": "NYC",
  "risk_tolerance": 0.4,
  "ambition": 0.7,
  "notes": "Interested in AI but worried about hype cycles"
}
```

`stage` is one of `"high_school" | "undergrad" | "new_grad"`.
`risk_tolerance` and `ambition` are floats in `[0, 1]`.

**Response body** (`DecisionOutput`):

```json
{
  "top3": [
    {
      "path_id": "big_tech_ic",
      "title": "Big Tech ML Engineer",
      "archetype": "corporate_ic",
      "summary": "...",
      "utility_score": 0.78,
      "why": "...",
      "tradeoffs": "...",
      "salary_curve_5y": [120000, 145000, 175000, 215000, 260000],
      "stddev_curve_5y": [15000, 22000, 35000, 50000, 70000],
      "ev_5y": 915000,
      "ruin_prob_5y": 0.04,
      "growth_rate": 0.55
    },
    {"path_id": "..."},
    {"path_id": "..."}
  ]
}
```

### `POST /simulate/stream`

Same input, but returns a `text/event-stream` (SSE) with **live progress events**
as each agent completes. Useful if the frontend wants to render path cards
progressively as evaluators finish (career/finance/risk arrive in arbitrary order
since they run in parallel).

Event sequence:

| `event:` name | `data:` payload | When emitted |
|---|---|---|
| `stage`       | `{"stage": "candidates"}`       | pipeline starts |
| `candidates`  | `PathCandidates`                | coordinator returned 3 paths |
| `stage`       | `{"stage": "evaluating"}`       | parallel evaluators started |
| `career`      | `CareerOutput`                  | career evaluator finished |
| `finance`     | `FinanceOutput`                 | finance evaluator finished |
| `risk`        | `RiskOutput`                    | risk evaluator finished |
| `stage`       | `{"stage": "deciding"}`         | decision agent started |
| `decision`    | `DecisionOutput`                | final ranked top-3 |
| `done`        | `{"ok": true}`                  | terminal success |
| `error`       | `{"error": "<message>"}`        | terminal failure |

Frontend pseudocode (any JS framework, fetch + manual SSE parse):

```js
const res = await fetch('/simulate/stream', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(profile),
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, {stream: true});
  const events = buffer.split('\n\n');
  buffer = events.pop();
  for (const block of events) {
    const eventLine = block.match(/^event: (.+)$/m)?.[1];
    const dataLine  = block.match(/^data: (.+)$/m)?.[1];
    if (eventLine && dataLine) {
      handle(eventLine, JSON.parse(dataLine));
    }
  }
}
```

### `GET /healthz`

```json
{"ok": true, "provider": "gemini", "model": "gemini-2.5-flash"}
```

### `GET /docs`

FastAPI's auto-generated OpenAPI UI. Use this to inspect every schema field and
try requests interactively from the browser.

## Schemas (high level)

| Type | Purpose |
|---|---|
| `UserProfile`     | Input — what the user submits |
| `PathCandidate` × 3 (`PathCandidates`) | Coordinator output — 3 archetype proposals |
| `CareerEval` × 3 (`CareerOutput`)      | Milestones, growth_rate, plateau_prob |
| `FinanceEval` × 3 (`FinanceOutput`)    | salary_curve_5y, stddev_curve_5y, ev_5y, tail_upside |
| `RiskEval` × 3 (`RiskOutput`)          | layoff_hazard_yr, ruin_prob_5y, downside_pctile_5y |
| `RankedPath` × 3 (`DecisionOutput`)    | Final ranked output with utility + why + tradeoffs |

Full field-level docs in `schemas.py`. The OpenAPI schema at `/docs` is the
authoritative source for the frontend.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `LLM_PROVIDER`     | `gemini`             | `gemini` or `openai` |
| `GEMINI_API_KEY`   | —                    | required if provider=gemini |
| `OPENAI_API_KEY`   | —                    | required if provider=openai |
| `MODEL`            | provider default     | overrides per-provider default |
| `PORT`             | `8765`               | server port |

## What's next (not in MVP)

Future agents — already named in the broader design but **not implemented this sprint**:

- **Lifestyle agent** — work hours, location quality, burnout decay → happiness curve
- **Personality fit agent** — turns user traits into utility weights instead of relying on `risk_tolerance`/`ambition` sliders
- **Critic agent** — adversarial: injects worst-case scenarios ("what if AI hiring freezes?", "what if you fail twice before success?") and re-runs the pipeline against flipped distributions

A debate loop (Critic vs domain agents, Decision agent as referee) would also
make the multi-agent demo more impressive than the current linear pipeline.
