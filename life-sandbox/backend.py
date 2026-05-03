"""Life Sandbox backend — FastAPI + AG2 Beta multi-agent pipeline.

Pipeline (all agents typed via response_schema):

  POST /simulate (UserProfile)
        ↓
  coordinator               → PathCandidates (3 paths)
        ↓
  asyncio.gather(
      career_eval.ask()     → CareerOutput
      finance_eval.ask()    → FinanceOutput
      risk_eval.ask()       → RiskOutput
  )
        ↓
  decision_agent            → DecisionOutput (top 3 ranked)
        ↓
  return DecisionOutput

Endpoints:
  GET  /healthz             → liveness + provider/model info
  GET  /docs                → FastAPI auto-generated OpenAPI UI (for frontend integration)
  POST /ingest              → fetch user-supplied URLs (LinkedIn, GitHub, etc.) and summarize via the ingest agent
  POST /simulate            → run pipeline, return final ranked top-3
  POST /simulate/stream     → run pipeline, stream progress events via SSE (text/event-stream)

The streaming endpoint emits these JSON-encoded SSE events in order:
  event: stage      data: {"stage": "candidates"}        # start
  event: candidates data: PathCandidates                  # 3 paths
  event: stage      data: {"stage": "evaluating"}
  event: career     data: CareerOutput
  event: finance    data: FinanceOutput
  event: risk       data: RiskOutput
  event: stage      data: {"stage": "deciding"}
  event: decision   data: DecisionOutput                  # final
  event: done       data: {"ok": true}
  event: error      data: {"error": "<message>"}         # on failure (terminal)
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from pydantic import BaseModel

from agents import (
    build_career_advice_agent,
    build_career_evaluator,
    build_config,
    build_coordinator,
    build_critic,
    build_decision_agent,
    build_finance_evaluator,
    build_ingest_agent,
    build_lifestyle_evaluator,
    build_path_expander,
    build_risk_evaluator,
)
import ingest
from schemas import (
    AnalyzeRequest,
    CareerAdvice,
    CareerOutput,
    CritiqueOutput,
    CustomPathRequest,
    DecisionOutput,
    FinanceOutput,
    IngestRequest,
    IngestResponse,
    IngestSummary,
    LifestyleOutput,
    PathCandidate,
    PathCandidates,
    ProfileExtract,
    RankedPath,
    RiskOutput,
    UserProfile,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Agents — built once at module import. Each `ask()` is independent / stateless,
# so we can reuse the same agent across requests.
# ---------------------------------------------------------------------------


coordinator = build_coordinator()
career_eval = build_career_evaluator()
finance_eval = build_finance_evaluator()
risk_eval = build_risk_evaluator()
lifestyle_eval = build_lifestyle_evaluator()
decision_agent = build_decision_agent()
critic_agent = build_critic()
path_expander = build_path_expander()
ingest_agent = build_ingest_agent()
career_advice_agent = build_career_advice_agent()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


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


def _paths_json(paths: list[PathCandidate]) -> str:
    return json.dumps([p.model_dump() for p in paths], indent=2)


async def _generate_candidates(profile: UserProfile) -> PathCandidates:
    prompt = (
        f"{_profile_block(profile)}\n"
        "Propose exactly 5 distinct, realistic career path archetypes for this user. "
        "Span a meaningful range of trade-offs (stable corporate IC, founder, specialist, "
        "creative/freelance, plus one wildcard). The user will pick 1-3 of these to "
        "evaluate in depth."
    )
    reply = await coordinator.ask(prompt)
    return await reply.content(retries=2)


async def _expand_custom_path(profile: UserProfile, description: str) -> PathCandidate:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"User-described career path:\n  {description!r}\n\n"
        "Return a single PathCandidate normalized for downstream evaluators."
    )
    reply = await path_expander.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_career(profile: UserProfile, paths: list[PathCandidate]) -> CareerOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL provided, in order, by id):\n"
        f"{_paths_json(paths)}\n\n"
        "Return one CareerEval per path. Use the provided `id` as path_id."
    )
    reply = await career_eval.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_finance(profile: UserProfile, paths: list[PathCandidate]) -> FinanceOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL provided, in order, by id):\n"
        f"{_paths_json(paths)}\n\n"
        "Return one FinanceEval per path. Use the provided `id` as path_id."
    )
    reply = await finance_eval.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_risk(profile: UserProfile, paths: list[PathCandidate]) -> RiskOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL provided, in order, by id):\n"
        f"{_paths_json(paths)}\n\n"
        "Return one RiskEval per path. Use the provided `id` as path_id."
    )
    reply = await risk_eval.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_lifestyle(profile: UserProfile, paths: list[PathCandidate]) -> LifestyleOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL provided, in order, by id):\n"
        f"{_paths_json(paths)}\n\n"
        "Return one LifestyleEval per path. Use the provided `id` as path_id."
    )
    reply = await lifestyle_eval.ask(prompt)
    return await reply.content(retries=2)


async def _decide(
    profile: UserProfile,
    paths: list[PathCandidate],
    career: CareerOutput,
    finance: FinanceOutput,
    risk: RiskOutput,
    lifestyle: LifestyleOutput,
) -> DecisionOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Selected paths ({len(paths)}):\n{_paths_json(paths)}\n\n"
        f"Career evaluations:\n{career.model_dump_json(indent=2)}\n\n"
        f"Finance evaluations:\n{finance.model_dump_json(indent=2)}\n\n"
        f"Risk evaluations:\n{risk.model_dump_json(indent=2)}\n\n"
        f"Lifestyle evaluations:\n{lifestyle.model_dump_json(indent=2)}\n\n"
        "Score each path with a utility function tailored to THIS user's "
        f"risk_tolerance and ambition. Return ALL {len(paths)} paths sorted by utility, "
        "highest first. Surface salary curves, EV, ruin probability, growth rate, "
        "work hours, pressure level, wlb_score, and burnout probability from the "
        "evaluations into each RankedPath."
    )
    reply = await decision_agent.ask(prompt)
    return await reply.content(retries=2)


async def _revise_decision(
    profile: UserProfile,
    paths: list[PathCandidate],
    career: CareerOutput,
    finance: FinanceOutput,
    risk: RiskOutput,
    lifestyle: LifestyleOutput,
    initial: DecisionOutput,
    critique: CritiqueOutput,
) -> DecisionOutput:
    """Decision agent's second pass — re-scores and re-ranks given the critic's feedback.

    Same agent, same response_schema. The prompt explicitly includes the critic's
    challenges so the LLM can adjust utility scores, why-it-fits, and tradeoffs.
    """
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Selected paths ({len(paths)}):\n{_paths_json(paths)}\n\n"
        f"Career evaluations:\n{career.model_dump_json(indent=2)}\n\n"
        f"Finance evaluations:\n{finance.model_dump_json(indent=2)}\n\n"
        f"Risk evaluations:\n{risk.model_dump_json(indent=2)}\n\n"
        f"Lifestyle evaluations:\n{lifestyle.model_dump_json(indent=2)}\n\n"
        f"YOUR PRIOR RANKING:\n{initial.model_dump_json(indent=2)}\n\n"
        f"CRITIC'S REVIEW (challenges your ranking):\n{critique.model_dump_json(indent=2)}\n\n"
        "Re-score and re-rank with the critic's feedback in mind. Where the critic "
        "raises valid points (over-optimistic assumptions, underweighted risks, "
        "mismatch with user preferences), adjust utility scores, why-it-fits, and "
        "tradeoffs accordingly. Where the critic is wrong, defend your prior ranking "
        "by keeping the score and tightening the why/tradeoffs to address the "
        "challenge. Always return ALL "
        f"{len(paths)} paths sorted by utility, highest first."
    )
    reply = await decision_agent.ask(prompt)
    return await reply.content(retries=2)


async def _analyze(profile: UserProfile, selected: list[PathCandidate]) -> DecisionOutput:
    """Run the 4 evaluators in parallel on the user-selected paths, then decide."""
    career, finance, risk, lifestyle = await asyncio.gather(
        _evaluate_career(profile, selected),
        _evaluate_finance(profile, selected),
        _evaluate_risk(profile, selected),
        _evaluate_lifestyle(profile, selected),
    )
    return await _decide(profile, selected, career, finance, risk, lifestyle)


async def _critique(
    profile: UserProfile,
    paths: list[PathCandidate],
    career: CareerOutput,
    finance: FinanceOutput,
    risk: RiskOutput,
    lifestyle: LifestyleOutput,
    decision: DecisionOutput,
) -> CritiqueOutput:
    """Adversarial pass — challenges the decision agent's ranking."""
    prompt = (
        f"{_profile_block(profile)}\n"
        f"User-selected paths ({len(paths)}):\n{_paths_json(paths)}\n\n"
        f"Career evaluations:\n{career.model_dump_json(indent=2)}\n\n"
        f"Finance evaluations:\n{finance.model_dump_json(indent=2)}\n\n"
        f"Risk evaluations:\n{risk.model_dump_json(indent=2)}\n\n"
        f"Lifestyle evaluations:\n{lifestyle.model_dump_json(indent=2)}\n\n"
        f"Decision agent's ranking:\n{decision.model_dump_json(indent=2)}\n\n"
        "Challenge this ranking. Return one PathCritique per ranked path (in the same "
        "order as the decision's top3), plus overall_challenge and the most "
        "overrated/underrated path ids (or null if the ranking is sound)."
    )
    reply = await critic_agent.ask(prompt)
    return await reply.content(retries=2)


async def run_pipeline(profile: UserProfile) -> DecisionOutput:
    """Legacy single-shot pipeline — coordinator proposes 5, evaluators evaluate all 5."""
    paths = await _generate_candidates(profile)
    return await _analyze(profile, list(paths.paths))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(
    title="Life Sandbox API",
    version="0.1.0",
    description=(
        "Multi-agent career-path sandbox on AG2 Beta. POST a UserProfile to "
        "/simulate to get the top-3 ranked career paths. Use /simulate/stream "
        "for live progress events via SSE."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Hackathon: allow any frontend origin during dev.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def serve_frontend() -> FileResponse:
    """Entry-page form. Submits to /simulate/stream and renders 3 ranked path cards."""
    return FileResponse(Path(__file__).parent / "frontend.html")


@app.get("/healthz")
async def healthz() -> dict:
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    return {
        "ok": True,
        "provider": provider,
        "model": os.environ.get(
            "MODEL",
            "gemini-2.5-flash" if provider == "gemini" else "gpt-4o-mini",
        ),
    }


@app.post("/simulate", response_model=DecisionOutput)
async def simulate(profile: UserProfile) -> DecisionOutput:
    """Legacy single-shot pipeline. Coordinator proposes 5, all 5 evaluated + ranked."""
    try:
        return await run_pipeline(profile)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/candidates", response_model=PathCandidates)
async def candidates(profile: UserProfile) -> PathCandidates:
    """Phase 1 of the new flow: coordinator proposes 5 candidate paths."""
    try:
        return await _generate_candidates(profile)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/expand-custom", response_model=PathCandidate)
async def expand_custom(req: CustomPathRequest) -> PathCandidate:
    """Normalize a user's free-form career idea into a structured PathCandidate.

    Used when the user types their own path on the selection screen instead
    of (or in addition to) picking from the coordinator's 5 proposals.
    """
    try:
        return await _expand_custom_path(req.profile, req.description)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class CareerAdviceRequest(BaseModel):
    profile: UserProfile
    chosen: RankedPath


@app.post("/career-advice", response_model=CareerAdvice)
async def career_advice(req: CareerAdviceRequest) -> CareerAdvice:
    """Concrete courses + programs + personal projects for the user's chosen path."""
    prompt = (
        f"{_profile_block(req.profile)}\n"
        f"Chosen path:\n{req.chosen.model_dump_json(indent=2)}\n\n"
        "Return courses, programs, personal_projects, and a headline tailored to this "
        "user's stage / field / location and the demands of this specific path. "
        "Use the chosen path's path_id verbatim."
    )
    try:
        reply = await career_advice_agent.ask(prompt)
        return await reply.content(retries=2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze/stream")
async def analyze_stream(req: AnalyzeRequest) -> StreamingResponse:
    """Phase 2 of the new flow: SSE stream that runs the 4 evaluators in
    parallel on the user-selected paths, then the decision agent ranks them.

    Multi-agent debate flow — decision agent runs twice, with the critic
    in between. Only the REVISED ranking is sent to the client; the critique
    payload is intentionally not emitted (it's an internal feedback signal).

    Event sequence:
        event: stage      data: {"stage": "evaluating"}
        event: career     data: CareerOutput
        event: finance    data: FinanceOutput
        event: risk       data: RiskOutput
        event: lifestyle  data: LifestyleOutput
        event: stage      data: {"stage": "deciding"}    # decision pass 1 (internal)
        event: stage      data: {"stage": "critiquing"}  # critic challenges (internal)
        event: stage      data: {"stage": "revising"}    # decision pass 2
        event: decision   data: DecisionOutput           # the FINAL revised ranking
        event: done       data: {"ok": true}
    """

    async def event_stream() -> AsyncIterator[bytes]:
        def sse(event: str, payload: dict) -> bytes:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()

        try:
            yield sse("stage", {"stage": "evaluating"})

            selected = list(req.selected_paths)
            tasks = {
                "career":    asyncio.create_task(_evaluate_career(req.profile, selected)),
                "finance":   asyncio.create_task(_evaluate_finance(req.profile, selected)),
                "risk":      asyncio.create_task(_evaluate_risk(req.profile, selected)),
                "lifestyle": asyncio.create_task(_evaluate_lifestyle(req.profile, selected)),
            }

            results: dict[str, object] = {}
            pending = set(tasks.values())
            name_by_task = {task: name for name, task in tasks.items()}

            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    name = name_by_task[task]
                    result = task.result()
                    results[name] = result
                    yield sse(name, result.model_dump())

            yield sse("stage", {"stage": "deciding"})
            initial = await _decide(
                req.profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
            )

            yield sse("stage", {"stage": "critiquing"})
            critique = await _critique(
                req.profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
                initial,
            )

            yield sse("stage", {"stage": "revising"})
            revised = await _revise_decision(
                req.profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
                initial,
                critique,
            )
            yield sse("decision", revised.model_dump())
            yield sse("done", {"ok": True})
        except Exception as exc:
            yield sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/simulate/stream")
async def simulate_stream(profile: UserProfile) -> StreamingResponse:
    """Run the pipeline and stream stage events via SSE.

    Frontend usage (browser):
        const res = await fetch('/simulate/stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(profile),
        });
        const reader = res.body.getReader();  // parse SSE manually
        // OR open with EventSource if you switch the endpoint to GET.

    Each event has `event: <name>` and `data: <json>` lines. See module docstring
    for the full event sequence.
    """

    async def event_stream() -> AsyncIterator[bytes]:
        def sse(event: str, payload: dict) -> bytes:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()

        try:
            yield sse("stage", {"stage": "candidates"})
            paths = await _generate_candidates(profile)
            yield sse("candidates", paths.model_dump())

            yield sse("stage", {"stage": "evaluating"})

            # Run evaluators in parallel; emit each as it finishes.
            selected = list(paths.paths)
            tasks = {
                "career":    asyncio.create_task(_evaluate_career(profile, selected)),
                "finance":   asyncio.create_task(_evaluate_finance(profile, selected)),
                "risk":      asyncio.create_task(_evaluate_risk(profile, selected)),
                "lifestyle": asyncio.create_task(_evaluate_lifestyle(profile, selected)),
            }

            results: dict[str, object] = {}
            pending = set(tasks.values())
            name_by_task = {task: name for name, task in tasks.items()}

            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    name = name_by_task[task]
                    result = task.result()  # re-raises if the task raised
                    results[name] = result
                    yield sse(name, result.model_dump())

            yield sse("stage", {"stage": "deciding"})
            initial = await _decide(
                profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
            )

            yield sse("stage", {"stage": "critiquing"})
            critique = await _critique(
                profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
                initial,
            )

            yield sse("stage", {"stage": "revising"})
            revised = await _revise_decision(
                profile,
                selected,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
                results["lifestyle"],  # type: ignore[arg-type]
                initial,
                critique,
            )
            yield sse("decision", revised.model_dump())
            yield sse("done", {"ok": True})
        except Exception as exc:
            yield sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering
            "Connection": "keep-alive",
        },
    )


@app.post("/ingest", response_model=IngestResponse)
async def ingest_sources(req: IngestRequest) -> IngestResponse:
    """Fetch each provided source, summarize the bundle, return both the summary
    (for form pre-fill) and the raw extracts (to resubmit with /simulate)."""

    extracts: list[ProfileExtract] = []

    # Fan out the three URL fetchers in parallel; use None as a placeholder for
    # missing fields so we can pin the result tuple by position.
    github_task = ingest.fetch_github(req.github_url) if req.github_url else None
    linkedin_task = ingest.fetch_linkedin(req.linkedin_url) if req.linkedin_url else None
    other_task = ingest.fetch_generic(req.other_url) if req.other_url else None

    async def _none() -> None:
        return None

    github_text, linkedin_text, other_text = await asyncio.gather(
        github_task if github_task is not None else _none(),
        linkedin_task if linkedin_task is not None else _none(),
        other_task if other_task is not None else _none(),
    )

    if req.github_url:
        extracts.append(
            ProfileExtract(
                source="github",
                url=req.github_url,
                text=github_text or "",
                fetched=github_text is not None,
            )
        )

    if req.linkedin_url:
        extracts.append(
            ProfileExtract(
                source="linkedin",
                url=req.linkedin_url,
                text=linkedin_text or "",
                fetched=linkedin_text is not None,
            )
        )

    if req.other_url:
        extracts.append(
            ProfileExtract(
                source="site",
                url=req.other_url,
                text=other_text or "",
                fetched=other_text is not None,
            )
        )

    if req.pasted_text:
        extracts.append(
            ProfileExtract(
                source="paste",
                url=None,
                text=ingest.truncate(req.pasted_text),
                fetched=True,
            )
        )

    if not extracts:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: github_url, linkedin_url, other_url, pasted_text.",
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


if __name__ == "__main__":
    # Validate provider env wiring at startup; build_config raises SystemExit
    # if the required env vars are missing.
    build_config()

    import uvicorn

    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(app, host="0.0.0.0", port=port)
