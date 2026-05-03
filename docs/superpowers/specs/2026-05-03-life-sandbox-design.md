# Life Sandbox — Hackathon MVP Design

**Date:** 2026-05-03
**Status:** Approved (1.5-hour build window)

## Goal
A multi-agent web app that takes a high-school / undergrad profile and returns the **top 3 career paths** with quantitative trade-offs (expected income, risk, growth) and a "why" explanation, demonstrating AG2 Beta's coordinator + parallel subagent + structured-output capabilities.

## Scope (this sprint)
- 4 agents: **Coordinator**, **Career**, **Finance**, **Risk**, **Decision** (Career/Finance/Risk run in parallel; Decision synthesizes).
- Single-POST `/simulate` endpoint. No live SSE streaming for v1 — frontend shows loading state.
- Quant-flavored output: each path has salary mean/stddev curve, EV, layoff hazard, ruin probability. Numbers come from the LLMs (no real Monte Carlo) but rendered with confidence bands.
- Single-page web frontend with form + 3 path cards (Chart.js sparklines).

## Out of scope (future)
- Lifestyle / Personality / Critic agents (mentioned in README "next steps").
- Adversarial debate loop, real Monte Carlo, Pareto frontier viz.
- Multi-turn refinement / chat.

## Architecture

```
[POST /simulate UserProfile]
        ↓
   Coordinator Agent → 3 PathCandidates  (1 LLM call)
        ↓
   asyncio.gather(
     Career.evaluate(paths)   ─┐
     Finance.evaluate(paths)  ─┼─ parallel, each returns evals for ALL 3 paths
     Risk.evaluate(paths)     ─┘
   )
        ↓
   Decision Agent merges {paths, career_evals, finance_evals, risk_evals, profile}
        ↓ (utility = w_money*EV - w_risk*ruin_prob + w_growth*growth_rate, weights from profile)
   { top3: list[RankedPath] }  → JSON to frontend
```

## Data shapes

```python
UserProfile     = {stage, field, location, risk_tolerance: 0..1, ambition: 0..1, notes}
PathCandidate   = {id, title, archetype, summary}
CareerEval      = {path_id, milestones: list[str], growth_rate: float, plateau_prob: float}
FinanceEval     = {path_id, salary_curve_5y: list[float], stddev_curve_5y: list[float], ev_5y: float, tail_upside: float}
RiskEval        = {path_id, layoff_hazard_yr: float, ruin_prob_5y: float, downside_pctile_5y: float}
RankedPath      = {path_id, title, archetype, summary, utility_score, why, tradeoffs, salary_curve_5y, stddev_curve_5y, ev_5y, ruin_prob_5y, growth_rate}
DecisionOutput  = {top3: list[RankedPath]}
```

All evaluator agents accept the **full list of 3 path candidates** and return a list of evals (one per path), so each domain costs 1 LLM call total.

## Files

```
life-sandbox/
├── pyproject.toml          # ag2[gemini,openai] + fastapi + uvicorn
├── .env.example
├── README.md
├── schemas.py              # Pydantic models above
├── agents.py               # 5 agent factories with response_schema
├── backend.py              # FastAPI app + orchestration
└── frontend.html           # Form + 3 cards + Chart.js (CDN)
```

## Frontend

- Left panel: form (stage select, field input, location, risk slider, ambition slider, "explore" button)
- Right panel: 3 stacked cards. Each:
  - Title + archetype badge + utility score
  - Salary sparkline (mean line, ±stddev band) — Chart.js line chart
  - Chips: `EV $X` `Ruin Y%` `Growth Z%`
  - "Why" + "Trade-offs" text
- Loading state while POST in flight (spinner + agent-by-agent text).

## Non-goals for spec
- No tests (hackathon).
- No auth / persistence.
- No streaming protocol.

## Risk / fallback
If structured-output validation flakes, retry up to 2x via `reply.content(retries=2)`. If a domain agent fails, the path still renders with that domain's fields blank. Agent failures should not crash the request.
