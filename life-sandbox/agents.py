"""Agent factories for the life-sandbox pipeline.

Five agents:
  - coordinator   : profile → 3 candidate path archetypes
  - career_eval   : profile + paths → CareerOutput  (parallel)
  - finance_eval  : profile + paths → FinanceOutput (parallel)
  - risk_eval     : profile + paths → RiskOutput    (parallel)
  - decision      : profile + paths + all evals → top-3 ranked paths
"""

from __future__ import annotations

import os

from autogen.beta import Agent
from autogen.beta.config import GeminiConfig, OpenAIConfig
from autogen.beta.config.config import ModelConfig

from schemas import (
    CareerOutput,
    DecisionOutput,
    FinanceOutput,
    PathCandidates,
    RiskOutput,
)

_PROVIDER_DEFAULTS = {
    "gemini": {"model": "gemini-2.5-flash", "env": "GEMINI_API_KEY"},
    "openai": {"model": "gpt-4o-mini", "env": "OPENAI_API_KEY"},
}


def build_config() -> ModelConfig:
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if provider not in _PROVIDER_DEFAULTS:
        raise SystemExit(f"LLM_PROVIDER must be one of {list(_PROVIDER_DEFAULTS)}")

    model = os.environ.get("MODEL", _PROVIDER_DEFAULTS[provider]["model"])
    env = _PROVIDER_DEFAULTS[provider]["env"]
    if not os.environ.get(env):
        raise SystemExit(
            f"{env} is required for LLM_PROVIDER={provider}. "
            f"Copy .env.example to .env and add your key."
        )

    if provider == "openai":
        config = OpenAIConfig(
            model=model,
            streaming=True,
            base_url="https://openrouter.ai/api/v1",
            # max_completion_tokens=1024,
        )
        return config
    return GeminiConfig(model=model, streaming=False)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


COORDINATOR_PROMPT = (
    "You are the lead career strategist. Given a user's profile, propose EXACTLY 3 "
    "DISTINCT, REALISTIC career path archetypes that span a meaningful range of "
    "trade-offs (e.g. one stable+steady, one growth+volatile, one entrepreneurial). "
    "Avoid duplicates. Each path should be specific enough to evaluate financially "
    "and stylistically — name the role and typical employer type. Optimize for "
    "diversity of trade-off profile, not generic 'safe' choices."
)


CAREER_PROMPT = (
    "You are a quantitative career-trajectory analyst. For each candidate path, return:\n"
    "  - 3-5 year-by-year milestones (concrete titles / responsibilities)\n"
    "  - growth_rate (0..1) — annual rate of skill/title progression\n"
    "  - plateau_prob (0..1) — probability the user stalls within 5 years\n"
    "Be honest about plateau risk. A senior IC track plateaus more than a high-growth "
    "startup; a quant trading seat has narrow advancement; consulting has up-or-out. "
    "Reflect the user's stage and field — a high-schooler has more setup years."
)


FINANCE_PROMPT = (
    "You are a quantitative compensation modeler. For each candidate path, return:\n"
    "  - salary_curve_5y: list of 5 expected total-comp values (USD), one per year, year 1 first\n"
    "  - stddev_curve_5y: list of 5 stddev values (USD) — the spread around each year's mean\n"
    "  - ev_5y: expected cumulative 5-year total comp\n"
    "  - tail_upside: P95 cumulative 5y comp (long-tail outcome — IPO, equity exit, principal bonus)\n"
    "Use realistic 2026 market levels. Big-tech ICs ≈ $150k–$400k by Y5. Founders have "
    "wide stddev and large tail upside but low mean. Quant traders have high mean + medium "
    "tail. Consultants have low stddev. Reflect location: NYC > remote > LCOL."
)


RISK_PROMPT = (
    "You are a quantitative career-risk analyst. For each candidate path, return:\n"
    "  - layoff_hazard_yr (0..1) — annual probability of involuntary exit\n"
    "  - ruin_prob_5y (0..1) — probability of bad outcome within 5 years (no meaningful income, forced career reset, or financial ruin)\n"
    "  - downside_pctile_5y: P5 cumulative 5y total comp (USD) — what the bottom 5% of outcomes earn\n"
    "Founders have high ruin (≥0.5), big-tech ICs low (≤0.05). Layoff hazard rose in "
    "2023-2025 even at big tech (~0.10/yr). Be specific — this is the agent the user "
    "trusts to surface bad scenarios."
)


DECISION_PROMPT = (
    "You are the decision agent. You receive:\n"
    "  - the user's profile (risk_tolerance 0..1, ambition 0..1)\n"
    "  - 3 candidate paths\n"
    "  - career, finance, and risk evaluations for each\n\n"
    "Compute a utility score per path using:\n"
    "  utility = (ambition) * normalize(growth_rate)\n"
    "          + (1 - risk_tolerance_offset) * normalize(ev_5y)\n"
    "          - (1 - risk_tolerance) * normalize(ruin_prob_5y)\n"
    "          + risk_tolerance * normalize(tail_upside)\n"
    "where normalize() rescales values across the 3 candidates to [0, 1]. The exact "
    "formula matters less than ranking these paths sensibly for THIS user.\n\n"
    "For each path produce: utility_score, a 2-3 sentence 'why' tied to the user's "
    "profile, and a 2-3 sentence 'tradeoffs' on what the user gives up. Surface the "
    "salary curves, EV, ruin prob, and growth rate verbatim from the inputs.\n\n"
    "Return all 3 paths sorted by utility, highest first."
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def build_coordinator() -> Agent:
    return Agent(
        name="coordinator",
        prompt=COORDINATOR_PROMPT,
        config=build_config(),
        response_schema=PathCandidates,
    )


def build_career_evaluator() -> Agent:
    return Agent(
        name="career_eval",
        prompt=CAREER_PROMPT,
        config=build_config(),
        response_schema=CareerOutput,
    )


def build_finance_evaluator() -> Agent:
    return Agent(
        name="finance_eval",
        prompt=FINANCE_PROMPT,
        config=build_config(),
        response_schema=FinanceOutput,
    )


def build_risk_evaluator() -> Agent:
    return Agent(
        name="risk_eval",
        prompt=RISK_PROMPT,
        config=build_config(),
        response_schema=RiskOutput,
    )


def build_decision_agent() -> Agent:
    return Agent(
        name="decision",
        prompt=DECISION_PROMPT,
        config=build_config(),
        response_schema=DecisionOutput,
    )
