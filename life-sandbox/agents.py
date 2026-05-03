"""Agent factories for the life-sandbox pipeline.

Two-phase pipeline:
  Phase 1 (POST /candidates):
    - coordinator    : profile → 5 candidate path archetypes
  Phase 2 (POST /analyze/stream): the user-selected 1-3 paths flow through
    - career_eval    : profile + paths → CareerOutput     (parallel)
    - finance_eval   : profile + paths → FinanceOutput    (parallel)
    - risk_eval      : profile + paths → RiskOutput       (parallel)
    - lifestyle_eval : profile + paths → LifestyleOutput  (parallel — WLB, pressure, burnout)
    - decision       : profile + paths + all evals → ranked paths
    - critic         : challenges the decision's ranking → CritiqueOutput

Side-channel agents:
  - path_expander  : profile + free-form description → PathCandidate
                     (used when the user types a custom career path)
  - ingest         : raw profile extracts → IngestSummary (field, stage, notes_seed)
  - career_advice  : profile + chosen RankedPath → CareerAdvice (post-pick advice)
"""

from __future__ import annotations

import os

from autogen.beta import Agent
from autogen.beta.config import GeminiConfig, OpenAIConfig
from autogen.beta.config.config import ModelConfig

from schemas import (
    CareerAdvice,
    CareerOutput,
    CritiqueOutput,
    DecisionOutput,
    FinanceOutput,
    IngestSummary,
    LifestyleOutput,
    PathCandidate,
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
    "You are the lead career strategist. Given a user's profile, propose EXACTLY 5 "
    "DISTINCT, REALISTIC career path archetypes that span a meaningful range of "
    "trade-offs. Aim for at least one of each: stable corporate IC, high-growth "
    "startup / founder, specialist (quant, researcher, consultant), creative / "
    "freelance, and a wildcard the user might not have considered themselves. "
    "Avoid duplicates. Each path should be specific enough to evaluate financially "
    "and stylistically — name the role and typical employer type. Optimize for "
    "diversity of trade-off profile, not generic 'safe' choices. The user will "
    "pick 1-3 of these to evaluate in depth."
)


PATH_EXPANDER_PROMPT = (
    "You normalize a user's free-form career idea into a structured PathCandidate "
    "for the rest of the pipeline. Given the user's profile and a short description "
    "of a career they have in mind, return:\n"
    "  - id: snake_case slug (e.g. 'indie_game_studio_founder', 'fed_research_economist')\n"
    "  - title: short human-readable title (e.g. 'Indie game studio founder')\n"
    "  - archetype: ONE of corporate_ic, founder, quant, consultant, researcher, freelance, other "
    "    — pick the closest fit\n"
    "  - summary: 2-3 sentence pitch describing what this path looks like for THIS user "
    "    over the next 5 years\n"
    "If the description is vague, make conservative best guesses anchored in the user's "
    "field, location, and stage. Be specific enough that the downstream Career / Finance / "
    "Risk / Lifestyle evaluators can produce meaningful numbers."
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


LIFESTYLE_PROMPT = (
    "You are a quantitative lifestyle / work-life-balance analyst. For each candidate path, return:\n"
    "  - work_hours_per_week — sustained typical hours (not crunch peaks). Big-tech IC ~45-55, "
    "    investment banking 70-90, consulting 55-70, founder 60-80, quant trader 50-70, "
    "    public-sector / academia 35-45.\n"
    "  - pressure_level (0..1) — day-to-day stress / intensity. PM at hyper-growth startup ~0.85, "
    "    gov research role ~0.30, big-tech IC ~0.45.\n"
    "  - wlb_score (0..1) — OVERALL work-life balance, factoring hours + remote flexibility + "
    "    vacation use + on-call burden + boundaries. Remote SWE ~0.75, IB analyst ~0.20, "
    "    early founder ~0.25, tenured prof ~0.80.\n"
    "  - burnout_prob_5y (0..1) — probability of significant burnout within 5y. IB analyst ~0.5, "
    "    big-tech IC ~0.15, founder ~0.40, professor ~0.20.\n"
    "Be honest. The user trusts this agent to surface lifestyle costs the salary curve hides — "
    "don't soften brutal paths."
)


CAREER_ADVICE_PROMPT = (
    "You are a concrete career-coaching agent. You receive the user's profile and ONE "
    "career path they have committed to. Return three lists of suggestions to help them "
    "improve their profile toward that path:\n"
    "  - courses — named courses, MOOCs, books, or certifications (e.g. 'CS229 Stanford', "
    "    'Designing Data-Intensive Applications by Kleppmann', 'CFA Level I'). NOT generic "
    "    'take an online course'. 3-6 items.\n"
    "  - programs — internships, fellowships, summer programs to apply to (e.g. 'YC Summer "
    "    2026', 'Anthropic residency', 'Citadel summer analyst', 'NSF REU at Berkeley'). "
    "    Concrete program names, not categories. 3-6 items.\n"
    "  - personal_projects — 3-6 specific buildable portfolio projects, each 1-2 sentences, "
    "    concrete enough that the user could start tomorrow.\n"
    "  - headline — one sentence framing what to focus on FIRST.\n\n"
    "Anchor advice to the user's stage (high_school / undergrad / new_grad), field, and "
    "location. A high-schooler doesn't apply to YC — they target USACO, Regeneron STS, "
    "or summer-camp programs. A new-grad does apply to YC, residencies, full-time roles. "
    "Use path_id verbatim from the input."
)


CRITIQUE_PROMPT = (
    "You are an adversarial decision critic in a multi-agent career-advisory system. "
    "Your job is to find PRINCIPLED weaknesses in the decision agent's ranking — "
    "not contrarianism, but actual issues a thoughtful skeptic would raise.\n\n"
    "You receive:\n"
    "  - the user's profile (risk_tolerance, ambition, stage, field)\n"
    "  - the user-selected candidate paths\n"
    "  - the career, finance, risk, AND lifestyle evaluator outputs for each\n"
    "  - the decision agent's ranking with utility scores, why-it-fits, and tradeoffs\n\n"
    "Look for:\n"
    "  - Over-optimistic assumptions in evaluator data (salary curves assuming top-quartile "
    "    placement; ruin_prob ignoring recent layoff trends; happiness curves without "
    "    realistic dips during grind years).\n"
    "  - Underweighted risks or hidden downsides (geographic concentration, visa risk, "
    "    health/burnout in y3-4, partner/family constraints if hinted at in notes).\n"
    "  - Mismatch with the user's stated profile — does the ranking actually match "
    "    risk_tolerance and ambition, or is it pulling them somewhere else?\n"
    "  - Common biases: survivorship bias, hype-cycle bias, hindsight bias, narrative "
    "    fluency overriding evidence.\n\n"
    "For each ranked path, return:\n"
    "  - challenge: 2-3 sentences arguing why this rank/utility might be wrong, OR "
    "    'No major issues — the rank reflects the evidence' if it's sound.\n"
    "  - optimism_flags: 1-4 specific evaluator assumptions that should be questioned.\n\n"
    "Plus an overall_challenge (the strongest single dissent — one paragraph), and "
    "name the most_overrated_path_id and most_underrated_path_id from the user-selected "
    "set. Set those to null if the ranking is genuinely sound.\n\n"
    "BE PRINCIPLED. If the decision is solid, say so. Don't manufacture critiques to "
    "justify your existence — that's worse than nothing."
)


DECISION_PROMPT = (
    "You are the decision agent. You receive:\n"
    "  - the user's profile (risk_tolerance 0..1, ambition 0..1)\n"
    "  - 1-5 candidate paths the user picked (typically 1-3 in the new flow)\n"
    "  - career, finance, risk, AND lifestyle evaluations for each\n\n"
    "Compute a utility score per path using something like:\n"
    "  utility = ambition          * normalize(growth_rate)\n"
    "          + (1 - risk_tol)    * normalize(ev_5y)\n"
    "          - (1 - risk_tol)    * normalize(ruin_prob_5y)\n"
    "          + risk_tol          * normalize(tail_upside)\n"
    "          + 0.3               * normalize(wlb_score)\n"
    "          - 0.2               * normalize(burnout_prob_5y)\n"
    "where normalize() rescales each metric across the 3 candidates to [0, 1]. The exact "
    "formula matters less than ranking these paths sensibly for THIS user.\n\n"
    "For each path produce: utility_score, a 2-3 sentence 'why' tied to the user's "
    "profile, and a 2-3 sentence 'tradeoffs' that explicitly call out lifestyle costs "
    "(hours, pressure, burnout) when relevant. Surface salary curves, EV, ruin prob, "
    "growth rate, work hours, pressure level, wlb_score, and burnout prob into each "
    "RankedPath verbatim from the inputs.\n\n"
    "Return all of the user-picked paths sorted by utility, highest first."
)


INGEST_PROMPT = (
    "You are a profile-summarization agent. Given raw extracts from a user's "
    "online profiles (GitHub, LinkedIn, personal site, or pasted text), produce:\n"
    "  - field: the user's current field of study or work (e.g. 'Computer Science', 'Finance')\n"
    "  - stage: one of high_school | undergrad | new_grad — pick the closest fit\n"
    "  - notes_seed: 2-4 concrete sentences capturing goals, projects, roles, and interests. "
    "Quote specific projects or company names where the source mentions them. Avoid generic filler.\n"
    "If extracts are sparse or missing, make conservative best guesses and keep notes_seed short."
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


def build_path_expander() -> Agent:
    return Agent(
        name="path_expander",
        prompt=PATH_EXPANDER_PROMPT,
        config=build_config(),
        response_schema=PathCandidate,
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


def build_lifestyle_evaluator() -> Agent:
    return Agent(
        name="lifestyle_eval",
        prompt=LIFESTYLE_PROMPT,
        config=build_config(),
        response_schema=LifestyleOutput,
    )


def build_decision_agent() -> Agent:
    return Agent(
        name="decision",
        prompt=DECISION_PROMPT,
        config=build_config(),
        response_schema=DecisionOutput,
    )


def build_critic() -> Agent:
    return Agent(
        name="critic",
        prompt=CRITIQUE_PROMPT,
        config=build_config(),
        response_schema=CritiqueOutput,
    )


def build_ingest_agent() -> Agent:
    return Agent(
        name="ingest",
        prompt=INGEST_PROMPT,
        config=build_config(),
        response_schema=IngestSummary,
    )


def build_career_advice_agent() -> Agent:
    return Agent(
        name="career_advice",
        prompt=CAREER_ADVICE_PROMPT,
        config=build_config(),
        response_schema=CareerAdvice,
    )
