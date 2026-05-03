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

## Running

```bash
cd life-sandbox
cp .env.example .env
# Add GEMINI_API_KEY=... (or OPENAI_API_KEY=... and set LLM_PROVIDER=openai)
uv sync   # or: pip install -e .
python backend.py
# Server on http://localhost:8765
# Interactive docs at http://localhost:8765/docs
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
