# Life Sandbox — Design & Implementation Summary

A multi-agent career-path advisory app built on **AG2 Beta** (`autogen.beta`)
for the AG2 Hackathon. Takes a high-school / undergrad profile, returns the
**top 3 career paths** with quantitative trade-offs (expected income,
risk, growth) and an explanation tailored to the user's risk tolerance.

> Run instructions and the full API contract for frontend integration live in
> [`README.md`](./README.md). This document captures the design, the
> implementation as it stands, and what's deferred.

---

## 1. Problem framing

High-school and undergrad students get career advice that is either generic
("become a doctor / engineer") or anecdotal ("my friend got rich at a
startup"). What they need is a **personalized, quantitative comparison of
realistic paths** that surfaces:

- **Expected outcome** — not just average salary, but the full distribution.
- **Tail risk** — what does the bottom-5% scenario look like?
- **Trade-offs** — what does each path force you to give up?
- **Personalization** — the same path is "good" for one student and
  "terrible" for another based on their risk tolerance and ambition.

A single LLM with one prompt could produce *something* like this, but it
would conflate concerns. A multi-agent setup with a **specialist per
dimension** plus a **decision layer** produces sharper, more honest output
and makes the system's reasoning legible.

---

## 2. Agent roster

The original brainstorm identified seven specialist roles. The MVP
implements **five** of them. The remaining two are deferred to a follow-up
sprint and documented as such.

| # | Agent | Role | Output schema | Status |
|---|---|---|---|---|
| 1 | **Coordinator**         | Profile → 3 distinct candidate path archetypes spanning a meaningful range of trade-offs | `PathCandidates` (3× `PathCandidate`)            | ✅ Implemented |
| 2 | **Career evaluator**    | Quantitative trajectory analysis: milestones, growth rate, plateau probability         | `CareerOutput` (3× `CareerEval`)                 | ✅ Implemented |
| 3 | **Finance evaluator**   | Compensation modeling: 5-year mean curve + stddev band, EV, P95 tail upside            | `FinanceOutput` (3× `FinanceEval`)               | ✅ Implemented |
| 4 | **Risk evaluator**      | Layoff hazard rate, 5-year ruin probability, P5 downside                                | `RiskOutput` (3× `RiskEval`)                     | ✅ Implemented |
| 5 | **Decision agent**      | Utility-weighted ranking using user's `risk_tolerance` and `ambition`                  | `DecisionOutput` (3× `RankedPath`)               | ✅ Implemented |
| 6 | **Lifestyle agent**     | Hours, location, burnout decay → happiness curve                                       | (not yet)                                        | 🔜 Deferred    |
| 7 | **Personality agent**   | Trait → utility-function parameters (replaces the slider-based weights)                | (not yet)                                        | 🔜 Deferred    |
| 8 | **Critic agent**        | Adversarial: flips distributions, injects worst-case scenarios, re-runs the pipeline   | (not yet)                                        | 🔜 Deferred    |

Every agent has a Pydantic `response_schema`, so the API surface is **fully
typed end-to-end** — no string-parsing on either the backend or the frontend.

---

## 3. Orchestration

```
                ┌──────────────────────┐
                │   POST /simulate     │
                │   UserProfile        │
                └──────────┬───────────┘
                           ↓
                ┌──────────────────────┐
                │     Coordinator      │
                │ "propose 3 distinct  │
                │   archetypes"        │
                └──────────┬───────────┘
                           ↓
              PathCandidates  (id, title, archetype, summary)
                           │
        ┌──────────────────┼──────────────────┐
        ↓                  ↓                  ↓
 ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
 │  Career      │  │  Finance     │  │  Risk        │
 │  evaluator   │  │  evaluator   │  │  evaluator   │
 └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
        │                 │                 │
   CareerOutput      FinanceOutput       RiskOutput
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ↓
                ┌──────────────────────┐
                │   Decision agent     │
                │  utility = f(profile,│
                │  career, finance,    │
                │  risk)               │
                └──────────┬───────────┘
                           ↓
                  DecisionOutput  (top-3 ranked)
```

**Key choices:**

- The 3 evaluators **fan out in parallel** via `asyncio.gather()`. Each
  evaluator takes the *full set of 3 candidate paths in one prompt* and
  returns a list of 3 evals (one per path). This is **5 LLM calls total**
  per request (1 + 3 in parallel + 1), regardless of how many paths the
  coordinator proposes.
- Evaluators do **not** call subagents or tools — they are pure
  prompt-in / structured-out workers. Simpler control flow, easier to
  debug, lower latency.
- The decision agent receives the user's profile **and** all three
  evaluations in a single prompt, so it can compute coherent utility
  trade-offs rather than ranking each dimension in isolation.

**Why parallel evaluators instead of a single mega-prompt?** Three reasons:

1. **Specialist prompts produce sharper output** than a generalist prompt
   that has to balance career, finance, and risk reasoning at once.
2. **Failures are isolated** — if the finance agent flakes, career and
   risk still return valid evals (the front-end can degrade gracefully).
3. **Demonstrates AG2's strength** — parallel async dispatch with
   structured output is a flagship capability of the beta API.

---

## 4. Quant flavor (without doing real Monte Carlo)

The original plan called for actual Monte Carlo simulation, log-normal
salary models, hazard-rate fits, Pareto-frontier optimization, etc. Within
the 1.5-hour build window we **simulate the appearance of those models by
having the LLMs emit the right shapes**:

- `salary_curve_5y` — mean total comp by year 1..5
- `stddev_curve_5y` — yearly stddev (renderable as a confidence band)
- `ev_5y` — cumulative 5-year expected total comp
- `tail_upside` — P95 cumulative outcome (long-tail wins)
- `layoff_hazard_yr` — annual involuntary-exit probability
- `ruin_prob_5y` — probability of a "bad outcome" (no income, forced
  reset) within 5 years
- `downside_pctile_5y` — P5 cumulative outcome

The agent prompts anchor the LLM with concrete 2026 market levels (big-tech
IC bands, founder distributions, quant comp) so numbers come back grounded.
The frontend can render mean lines with stddev bands directly from these
fields without any backend simulation.

This is intentional MVP scope. Real distributional modeling
(`numpy.random` simulations parameterized by LLM-emitted means/stddevs) is
a one-evening upgrade — see §7.

---

## 5. Data shapes (excerpt)

Full definitions in [`schemas.py`](./schemas.py). Highlights:

```python
class UserProfile(BaseModel):
    stage: Literal["high_school", "undergrad", "new_grad"]
    field: str
    location: str
    risk_tolerance: float  # 0..1
    ambition: float        # 0..1
    notes: str = ""

class PathCandidate(BaseModel):
    id: str            # e.g. "big_tech_ic"
    title: str         # e.g. "Big Tech ML Engineer"
    archetype: str     # corporate_ic | founder | quant | consultant | researcher | freelance | other
    summary: str

class FinanceEval(BaseModel):
    path_id: str
    salary_curve_5y: list[float]   # length 5
    stddev_curve_5y: list[float]   # length 5
    ev_5y: float
    tail_upside: float             # P95 cumulative

class RankedPath(BaseModel):
    path_id: str
    title: str
    archetype: str
    summary: str
    utility_score: float
    why: str
    tradeoffs: str
    salary_curve_5y: list[float]
    stddev_curve_5y: list[float]
    ev_5y: float
    ruin_prob_5y: float
    growth_rate: float
```

Pydantic constraints (`min_length=3, max_length=3`, `ge=0.0, le=1.0`,
`min_length=5, max_length=5`) flow into the JSON schema sent to the LLM, so
both Gemini and OpenAI structured-output paths refuse to return malformed
shapes.

---

## 6. API contract (for the frontend teammate)

The full reference lives in [`README.md`](./README.md) and the live
OpenAPI UI at `GET /docs`. The minimum a frontend needs to know:

| Endpoint | Use case |
|---|---|
| `POST /simulate`         | One round-trip. Returns `DecisionOutput`. Render once. |
| `POST /simulate/stream`  | SSE. Emits `candidates`, `career`, `finance`, `risk`, `decision`, `done` events as each agent completes. Use for progressive rendering. |
| `GET /healthz`           | Liveness + provider/model info. |
| `GET /docs`              | FastAPI Swagger UI. |

CORS is wide open (`allow_origins=["*"]`) for hackathon dev.

---

## 7. What's next

In rough priority order, biggest demo-impact first:

1. **Critic agent** (adversarial). After the decision agent ranks, run a
   critic that flips assumptions ("what if AI hiring freezes?", "what if
   you fail twice before succeeding?") and re-runs the evaluators against
   shocked priors. Show before/after side-by-side. This is the
   single most impressive multi-agent upgrade.
2. **Real Monte Carlo on the finance dimension.** LLM emits mean + stddev
   per year; backend runs `numpy` simulations to produce 1000 sample paths.
   Frontend can render a fan plot. Same for `ruin_prob_5y` via
   discrete-time hazard simulation.
3. **Personality agent.** Replace the two sliders with a 5-question trait
   inventory; agent translates traits → utility weights for the decision
   stage. This is the differentiation card vs other hackathon teams.
4. **Lifestyle agent + happiness curve.** Hours-per-week × stress mapping
   → annual happiness score. Adds a fourth axis to the cards.
5. **Pareto-frontier visualization.** Once finance and risk are
   distributional, plot all candidate paths on a `(EV, ruin_prob)` plane
   with the Pareto frontier highlighted.
6. **Multi-round debate.** Critic vs domain agents, decision agent as
   referee, run until convergence or N rounds. This is "true multi-agent"
   in the deepest sense.

Each item is its own day. Item 1 is what to ship next if there is *any*
remaining hackathon time.

---

## 8. File map

```
life-sandbox/
├── pyproject.toml      # ag2[gemini,openai] + fastapi + uvicorn
├── .env.example        # GEMINI_API_KEY / OPENAI_API_KEY / LLM_PROVIDER / MODEL / PORT
├── schemas.py          # All Pydantic models
├── agents.py           # 5 agent factories + provider/config selection
├── backend.py          # FastAPI app + orchestration pipeline + SSE endpoint
├── frontend.html       # Self-contained entry-page form (served at GET /)
├── README.md           # Run instructions + full API contract
└── DESIGN.md           # ← this file
```

Spec record (frozen at design time):
[`docs/superpowers/specs/2026-05-03-life-sandbox-design.md`](../docs/superpowers/specs/2026-05-03-life-sandbox-design.md)

---

## 9. Build status & known gaps

- ✅ All 5 agents implemented with structured output.
- ✅ Both `/simulate` (sync) and `/simulate/stream` (SSE) endpoints.
- ✅ Provider switch — Gemini (default) or OpenAI via `LLM_PROVIDER`.
- ✅ Wide-open CORS for cross-origin frontend dev.
- ✅ FastAPI auto-generated OpenAPI UI at `/docs`.
- ⚠️ End-to-end LLM run not exercised in the build session (no API keys
  available at write time). First production smoke-test is the moment of
  truth for the structured-output schemas — particularly the
  `min_length=3, max_length=3` list constraints, which some providers
  enforce more strictly than others.
- 🔜 Lifestyle, Personality, Critic agents — not implemented.
- 🔜 No real distributional simulation — LLMs emit summary statistics
  directly.
- 🔜 No persistence, no auth, no multi-turn refinement.
