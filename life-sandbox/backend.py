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
    build_decision_agent,
    build_finance_evaluator,
    build_ingest_agent,
    build_risk_evaluator,
)
import ingest
from schemas import (
    CareerAdvice,
    CareerOutput,
    DecisionOutput,
    FinanceOutput,
    IngestRequest,
    IngestResponse,
    IngestSummary,
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
decision_agent = build_decision_agent()
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


async def _generate_candidates(profile: UserProfile) -> PathCandidates:
    prompt = (
        f"{_profile_block(profile)}\n"
        "Propose exactly 3 distinct, realistic career path archetypes for this user. "
        "Make sure the 3 paths span a meaningful range of trade-offs."
    )
    reply = await coordinator.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_career(profile: UserProfile, paths: PathCandidates) -> CareerOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL three, in order, by id):\n"
        f"{paths.model_dump_json(indent=2)}\n\n"
        "Return one CareerEval per path. Use the provided `id` as path_id."
    )
    reply = await career_eval.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_finance(profile: UserProfile, paths: PathCandidates) -> FinanceOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL three, in order, by id):\n"
        f"{paths.model_dump_json(indent=2)}\n\n"
        "Return one FinanceEval per path. Use the provided `id` as path_id."
    )
    reply = await finance_eval.ask(prompt)
    return await reply.content(retries=2)


async def _evaluate_risk(profile: UserProfile, paths: PathCandidates) -> RiskOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths (evaluate ALL three, in order, by id):\n"
        f"{paths.model_dump_json(indent=2)}\n\n"
        "Return one RiskEval per path. Use the provided `id` as path_id."
    )
    reply = await risk_eval.ask(prompt)
    return await reply.content(retries=2)


async def _decide(
    profile: UserProfile,
    paths: PathCandidates,
    career: CareerOutput,
    finance: FinanceOutput,
    risk: RiskOutput,
) -> DecisionOutput:
    prompt = (
        f"{_profile_block(profile)}\n"
        f"Candidate paths:\n{paths.model_dump_json(indent=2)}\n\n"
        f"Career evaluations:\n{career.model_dump_json(indent=2)}\n\n"
        f"Finance evaluations:\n{finance.model_dump_json(indent=2)}\n\n"
        f"Risk evaluations:\n{risk.model_dump_json(indent=2)}\n\n"
        "Score each path with a utility function tailored to THIS user's "
        "risk_tolerance and ambition, and return all 3 sorted by utility. "
        "Surface the salary curves, EV, ruin probability, and growth rate "
        "from the evaluations into each RankedPath."
    )
    reply = await decision_agent.ask(prompt)
    return await reply.content(retries=2)


async def run_pipeline(profile: UserProfile) -> DecisionOutput:
    """Synchronous pipeline — runs the 3 evaluators in parallel."""
    paths = await _generate_candidates(profile)

    career, finance, risk = await asyncio.gather(
        _evaluate_career(profile, paths),
        _evaluate_finance(profile, paths),
        _evaluate_risk(profile, paths),
    )

    return await _decide(profile, paths, career, finance, risk)


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
    """Run the full pipeline synchronously. Returns the top-3 ranked paths."""
    try:
        return await run_pipeline(profile)
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
            tasks = {
                "career": asyncio.create_task(_evaluate_career(profile, paths)),
                "finance": asyncio.create_task(_evaluate_finance(profile, paths)),
                "risk": asyncio.create_task(_evaluate_risk(profile, paths)),
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
            decision = await _decide(
                profile,
                paths,
                results["career"],     # type: ignore[arg-type]
                results["finance"],    # type: ignore[arg-type]
                results["risk"],       # type: ignore[arg-type]
            )
            yield sse("decision", decision.model_dump())
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
