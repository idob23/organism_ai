# Role: Documentation Sync Reviewer

## Description
Reviews all system documentation for accuracy. Ensures CLAUDE.md, ARCHITECTURE_DECISIONS.md,
roadmap, and CONVENTIONS.md accurately reflect the current codebase state.

## Context files
- CLAUDE.md — core context (architecture, file structure, metrics, rules)
- ARCHITECTURE_DECISIONS.md — Sprint 9+ decisions
- ARCHITECTURE_DECISIONS_ARCHIVE.md — Sprint 1-8 history
- organism_ai_roadmap.md — completed/open tasks
- CONVENTIONS.md — code conventions, CLI, bot commands
- organism_architecture_principles.md — strategic architecture document
- config/personality/default.md, artel_zoloto.md, ai_media.md

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: File structure sync
**What**: CLAUDE.md file tree matches real .py files in src/organism/.
**How to verify**: `python scripts/code_health.py` — check_file_structure() result.
**Violation = problem**: Developer reads CLAUDE.md, looks for file that doesn't exist.

### INV-2: Benchmark metrics sync
**What**: Benchmark numbers in CLAUDE.md and roadmap match actual TASKS count in benchmark.py.
**How to verify**: `python scripts/code_health.py` — check_benchmark_count() result.
**Violation = problem**: Docs claim metrics that aren't real.

### INV-3: No stale file references
**What**: .md docs don't reference deleted/non-existent files.
**How to verify**: Extract all paths matching `src/organism/...` and `config/...` from .md
files. For each, check if the file exists on disk. Missing = stale reference.
**Violation = problem**: Documentation links to phantom files, confusing readers.

## Contextual checks (within scope)
- Convention drift: spot-check 5 random .py files for Cyrillic (should be unicode escapes).
- Architecture principles vs reality: organism_architecture_principles.md claims — are they
  implemented or aspirational? Flag principles described as current that aren't in code.
- Fix/feature completeness: FIX-XX entries in ARCHITECTURE_DECISIONS have corresponding code changes.
- Roadmap accuracy: "Completed" list matches reality, no completed items in "Open".
- Personality configs: do they reference tools/capabilities that actually exist?
- Version consistency: all docs agree on sprint number, fix count, benchmark results.

## How to verify
Script should:
1. Run `python scripts/code_health.py` — use results for INV-1, INV-2
2. Execute INV-3: extract file paths from .md files, verify existence on disk
3. Contextual: spot-check Cyrillic in .py, check architecture principles, verify roadmap

## Report format
Report in Russian:
```
OBLAST: Documentation (CLAUDE.md, CONVENTIONS.md, roadmap, ARCHITECTURE_DECISIONS)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: File structure sync — details
  INV-2 [PASS/FAIL]: Benchmark metrics sync — details
  INV-3 [PASS/FAIL]: No stale references — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
