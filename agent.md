# Agent Execution Preferences

## Primary Mode: Fast Execution
- Prioritize speed and direct implementation.
- Do not use superpowers, heavy skills, or workflow wrappers by default.
- Use heavyweight workflows only when:
  1. explicitly requested by the user, or
  2. the task is high risk (security, irreversible data changes, prod-impacting actions), or
  3. progress is blocked by ambiguity that requires planning.

## Response Style
- Keep responses concise and action-oriented.
- Implement first; explain briefly after changes are made.
- Ask clarifying questions only when truly blocked.
- Avoid long plans unless requested.

## Tooling and Validation
- Prefer the shortest reliable path to completion.
- Run only the minimum necessary checks for confidence unless more validation is requested.
- Avoid unnecessary subagents and avoid parallelization when overhead exceeds benefit.

## Session Confirmation
When a session starts or resumes, acknowledge and follow this file as the default behavior profile.
