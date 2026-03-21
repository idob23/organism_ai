# Role: Agent Factory & Orchestrator Reviewer

## Description
Reviews the multi-agent subsystem: AgentFactory, MetaOrchestrator, base Orchestrator,
specialized agents, and agent lifecycle. Focus on recursion safety and dead code.

## Context files
- src/organism/agents/factory.py — AgentFactory (create/delete/list)
- src/organism/agents/meta_orchestrator.py — MetaOrchestrator (routing, delegation)
- src/organism/agents/orchestrator.py — base Orchestrator (state machine)
- src/organism/agents/base.py — BaseAgent (_reflect, cross-agent insights)
- src/organism/agents/coder.py — CoderAgent
- src/organism/agents/researcher.py — ResearcherAgent
- src/organism/agents/writer.py — WriterAgent
- src/organism/agents/analyst.py — AnalystAgent
- src/organism/core/decomposer.py — TaskDecomposer (reserved, not in active path)
- config/roles/*.md — role templates
- config/agents/*.json — created agent configs

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Recursion depth guard
**What**: MetaOrchestrator.run_as_agent() has MAX_DELEGATE_DEPTH and _current_depth counter.
**How to verify**: `grep -n "MAX_DELEGATE_DEPTH\|_current_depth" /repo/src/organism/agents/meta_orchestrator.py`
— both must be present: constant defined and counter used in run_as_agent().
**Violation = problem**: Infinite delegate recursion, stack overflow.

### INV-2: Single factory instance
**What**: `AgentFactory()` constructor called in exactly one place (build_loop in main.py).
**How to verify**: `grep -rn "AgentFactory()" /repo/ --include="*.py"` — must appear
only in main.py (and optionally benchmark.py for testing).
**Violation = problem**: Multiple factory instances with divergent state.

### INV-3: All agent classes imported
**What**: Each specialized agent class (CoderAgent, ResearcherAgent, WriterAgent,
AnalystAgent) is imported from at least one other module.
**How to verify**: For each class: `grep -rn "CoderAgent\|ResearcherAgent\|WriterAgent\|AnalystAgent"
/repo/src/ --include="*.py"` — each must appear outside its own definition file.
**Violation = problem**: Dead agent class, code bloat.

## Contextual checks (within scope)
- Personality injection: run_as_agent() passes personality via extra_system_context,
  NOT via task text (FIX-63). Task to loop.run() must be clean user task.
- Role template completeness: each config/roles/*.md has ## Description section
  (used by _route_choice per FIX-62).
- Agent cleanup: delete_agent() removes JSON file from config/agents/.
- Orchestrator state machine: base Orchestrator — is it reachable in current flow?
- Decomposer: reserved module (FIX-44), verify not accidentally imported into active paths.

## How to verify
Script should:
1. Execute INV-1: grep MAX_DELEGATE_DEPTH and _current_depth in meta_orchestrator.py
2. Execute INV-2: grep AgentFactory() across entire codebase
3. Execute INV-3: grep each agent class across src/, verify external usage
4. Contextual: read meta_orchestrator.py for personality injection, check role templates

## Report format
Report in Russian:
```
OBLAST: Agents and orchestration (agents/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Recursion depth guard — details
  INV-2 [PASS/FAIL]: Single factory instance — details
  INV-3 [PASS/FAIL]: Agent classes imported — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
