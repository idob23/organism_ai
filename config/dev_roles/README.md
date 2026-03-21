# Dev Review Roles

Role templates for automated code review (REVIEW-1, REVIEW-3).

## Methodology: Invariant-First

Each reviewer template separates two types of checks:

1. **INVARIANTS** — deterministic, exhaustive grep/script checks across the ENTIRE codebase.
   Each invariant has a concrete "How to verify" command. Results are PASS/FAIL with evidence.

2. **Contextual checks** — semantic analysis of scope-specific files. Requires reading and
   understanding code, not automatable via simple grep.

Reviewers always execute invariants FIRST, then contextual checks.
`code_health.py` results are shared with ALL reviewers to avoid duplicate work.

## Usage

`dev_review` tool loads templates by scope:
- `memory` -> reviewer_memory.md
- `core` -> reviewer_core.md
- `tools` -> reviewer_tools.md
- `channels` -> reviewer_channels.md
- `agents` -> reviewer_agents.md
- `infra` -> reviewer_infra.md
- `docs` -> reviewer_docs.md
- `quality` -> reviewer_quality.md
- `self_improvement` -> reviewer_self_improvement.md
- `all` -> all 9 + review_coordinator.md

## Coordinator process

1. Step 0: Run `python scripts/code_health.py` — share with all reviewers
2. Step 1: Each reviewer runs INV-* checks (exhaustive grep)
3. Step 2: Each reviewer runs contextual checks (semantic analysis)
4. Step 3: Coordinator synthesizes, deduplicates, prioritizes
