"""Pydantic schemas for the life-sandbox multi-agent pipeline.

Two-phase flow:
  1. Coordinator proposes 5 candidate paths (POST /candidates).
  2. User picks 1-3 of them (with optional custom path expanded via the
     path-expander agent at POST /expand-custom).
  3. Domain evaluators (Career / Finance / Risk / Lifestyle) run on the
     user-selected paths and the decision agent ranks them
     (POST /analyze/stream).

A separate Career Advice agent (POST /career-advice) takes a chosen
RankedPath and returns concrete suggestions — courses, programs, projects —
for the user to improve their profile toward that path.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    stage: Literal["high_school", "undergrad", "new_grad"]
    field: Annotated[str, Field(description="Current field of study or interest, e.g. 'Computer Science'")]
    location: Annotated[str, Field(description="Preferred location, e.g. 'NYC' or 'Remote'")]
    risk_tolerance: Annotated[float, Field(ge=0.0, le=1.0, description="0 = avoid all risk, 1 = embrace volatility")]
    ambition: Annotated[float, Field(ge=0.0, le=1.0, description="0 = stable plateau is fine, 1 = optimize for growth")]
    notes: Annotated[str, Field(default="", description="Free-form notes about goals, constraints, interests")]
    extracts: Annotated[list["ProfileExtract"], Field(default_factory=list, description="Source extracts attached during ingest, passed through to the agents")]


# ---------------------------------------------------------------------------
# Coordinator output — 3 candidate archetypes
# ---------------------------------------------------------------------------


class PathCandidate(BaseModel):
    id: Annotated[str, Field(description="Short slug, e.g. 'big_tech_ic'")]
    title: Annotated[str, Field(description="Human-readable title, e.g. 'Big Tech Software Engineer'")]
    archetype: Annotated[str, Field(description="One of: corporate_ic, founder, quant, consultant, researcher, freelance, other")]
    summary: Annotated[str, Field(description="2-3 sentence pitch — what this path looks like for the next 5 years.")]


class PathCandidates(BaseModel):
    paths: Annotated[list[PathCandidate], Field(min_length=5, max_length=5, description="Exactly 5 distinct candidate paths")]


# ---------------------------------------------------------------------------
# Domain evaluations — each evaluator returns one entry per candidate path
# ---------------------------------------------------------------------------


class CareerEval(BaseModel):
    path_id: str
    milestones: Annotated[list[str], Field(min_length=3, max_length=5, description="Year-by-year milestones, year 1 first")]
    growth_rate: Annotated[float, Field(ge=0.0, le=1.0, description="Annual skill/title growth rate, 0..1")]
    plateau_prob: Annotated[float, Field(ge=0.0, le=1.0, description="Probability of stalling within 5 years")]


class CareerOutput(BaseModel):
    evals: Annotated[list[CareerEval], Field(min_length=1, max_length=5)]


class FinanceEval(BaseModel):
    path_id: str
    salary_curve_5y: Annotated[list[float], Field(min_length=5, max_length=5, description="Expected total comp by year 1..5, USD")]
    stddev_curve_5y: Annotated[list[float], Field(min_length=5, max_length=5, description="Stddev of total comp by year 1..5, USD")]
    ev_5y: Annotated[float, Field(description="Expected cumulative comp over 5 years, USD")]
    tail_upside: Annotated[float, Field(description="P95 cumulative 5y comp (long-tail outcome), USD")]


class FinanceOutput(BaseModel):
    evals: Annotated[list[FinanceEval], Field(min_length=1, max_length=5)]


class RiskEval(BaseModel):
    path_id: str
    layoff_hazard_yr: Annotated[float, Field(ge=0.0, le=1.0, description="Annual probability of involuntary exit")]
    ruin_prob_5y: Annotated[float, Field(ge=0.0, le=1.0, description="Probability of bad outcome within 5y (no income, forced restart)")]
    downside_pctile_5y: Annotated[float, Field(description="P5 cumulative 5y comp (worst-case income), USD")]


class RiskOutput(BaseModel):
    evals: Annotated[list[RiskEval], Field(min_length=1, max_length=5)]


class LifestyleEval(BaseModel):
    path_id: str
    work_hours_per_week: Annotated[float, Field(ge=0.0, le=120.0, description="Sustained typical hours/week (not crunch peaks)")]
    pressure_level: Annotated[float, Field(ge=0.0, le=1.0, description="Day-to-day stress / intensity, 0=calm, 1=relentless")]
    wlb_score: Annotated[float, Field(ge=0.0, le=1.0, description="Overall work-life balance: hours + flexibility + vacation + on-call. 0=brutal, 1=excellent")]
    burnout_prob_5y: Annotated[float, Field(ge=0.0, le=1.0, description="Probability of significant burnout within 5 years")]


class LifestyleOutput(BaseModel):
    evals: Annotated[list[LifestyleEval], Field(min_length=1, max_length=5)]


# ---------------------------------------------------------------------------
# Decision agent output
# ---------------------------------------------------------------------------


class RankedPath(BaseModel):
    path_id: str
    title: str
    archetype: str
    summary: str
    utility_score: Annotated[float, Field(description="Utility score given user's preferences. Higher is better.")]
    why: Annotated[str, Field(description="2-3 sentences on why this path scores well for THIS user.")]
    tradeoffs: Annotated[str, Field(description="2-3 sentences on what the user gives up by choosing this.")]
    # Surfaced metrics for the card
    salary_curve_5y: list[float]
    stddev_curve_5y: list[float]
    ev_5y: float
    ruin_prob_5y: float
    growth_rate: float
    # Lifestyle dimension — surfaced for the comparison view
    work_hours_per_week: float
    pressure_level: Annotated[float, Field(ge=0.0, le=1.0)]
    wlb_score: Annotated[float, Field(ge=0.0, le=1.0)]
    burnout_prob_5y: Annotated[float, Field(ge=0.0, le=1.0)]


class DecisionOutput(BaseModel):
    top3: Annotated[list[RankedPath], Field(min_length=1, max_length=5, description="User-selected paths sorted by utility, best first (1-3 from the new flow, up to 5 from legacy /simulate/stream)")]


# ---------------------------------------------------------------------------
# Critic agent — challenges the decision agent's ranking (multi-agent debate)
# ---------------------------------------------------------------------------


class PathCritique(BaseModel):
    path_id: str
    challenge: Annotated[str, Field(description="2-3 sentences arguing why this path's rank/utility might be wrong, OR 'No major issues' if the rank is sound.")]
    optimism_flags: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=4,
            description="Specific evaluator assumptions that look optimistic — e.g. "
            "'salary curve assumes top-quartile placement', 'ruin_prob ignores 2024 layoff trend'. "
            "1-4 items.",
        ),
    ]


class CritiqueOutput(BaseModel):
    overall_challenge: Annotated[str, Field(description="2-3 sentences framing the strongest dissent on the ranking as a whole — or 'Ranking is sound' if no real issue.")]
    most_overrated_path_id: Annotated[str | None, Field(default=None, description="Path id the critic thinks is ranked too high. None if no issue.")]
    most_underrated_path_id: Annotated[str | None, Field(default=None, description="Path id the critic thinks is ranked too low. None if no issue.")]
    per_path: Annotated[list[PathCritique], Field(min_length=1, max_length=5, description="One critique per ranked path, in the same order the decision agent ranked them.")]


# ---------------------------------------------------------------------------
# Two-phase pipeline request schemas
# ---------------------------------------------------------------------------


class CustomPathRequest(BaseModel):
    profile: UserProfile
    description: Annotated[str, Field(min_length=2, max_length=500, description="Free-form description of the path the user has in mind")]


class AnalyzeRequest(BaseModel):
    profile: UserProfile
    selected_paths: Annotated[list[PathCandidate], Field(min_length=1, max_length=3, description="The 1-3 paths the user selected (mix of coordinator-proposed + optional custom)")]


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


# ---------------------------------------------------------------------------
# Career advice agent — runs after the user picks a single path
# ---------------------------------------------------------------------------


class CareerAdvice(BaseModel):
    path_id: str
    headline: Annotated[str, Field(description="One sentence: what should the user focus on first?")]
    courses: Annotated[
        list[str],
        Field(
            min_length=3,
            max_length=6,
            description="Named courses, MOOCs, books, or certifications. E.g. 'CS229 Stanford', "
            "'Designing Data-Intensive Applications by Kleppmann', 'CFA Level I'. "
            "NOT generic 'take an online course'.",
        ),
    ]
    programs: Annotated[
        list[str],
        Field(
            min_length=3,
            max_length=6,
            description="Internships, fellowships, summer programs by name. E.g. 'YC Summer 2026', "
            "'Anthropic residency', 'Citadel summer analyst', 'NSF REU at Berkeley'.",
        ),
    ]
    personal_projects: Annotated[
        list[str],
        Field(
            min_length=3,
            max_length=6,
            description="Specific, buildable portfolio projects — each 1-2 sentences, concrete "
            "enough that the user could start tomorrow.",
        ),
    ]
