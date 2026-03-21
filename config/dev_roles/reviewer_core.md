# Role: Core Loop & Planning Reviewer

## Description
Reviews the execution engine: CoreLoop, Planner, Evaluator, Decomposer, Scheduler,
Personality, SkillMatcher, HumanApproval, and LLM layer. Focus on execution safety.

## Context files
- src/organism/core/loop.py — CoreLoop.run(), _handle_conversation(), _classify_complex()
- src/organism/core/planner.py — plan generation, fast/react paths
- src/organism/core/planner_module.py — plan validation and step execution
- src/organism/core/evaluator.py — quality scoring, PVC integration
- src/organism/core/decomposer.py — subtask decomposition
- src/organism/core/scheduler.py — ProactiveScheduler, ScheduledJob
- src/organism/core/personality.py — PersonalityConfig loader
- src/organism/core/skill_matcher.py — skill file matching
- src/organism/core/human_approval.py — PendingApproval, approval flow
- src/organism/llm/base.py — LLMProvider ABC, TemperatureLocked
- src/organism/llm/claude.py — ClaudeProvider, model tier mapping
- src/organism/safety/validator.py — SafetyValidator

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Tool round limit enforced
**What**: MAX_TOOL_ROUNDS is defined and used as exit condition in the tool loop.
**How to verify**: `grep -n "MAX_TOOL_ROUNDS" /repo/src/organism/core/loop.py` — must be
defined as constant AND used in while/for loop condition.
**Violation = problem**: Infinite tool loops, runaway API costs.

### INV-2: Skip orchestrator on agent delegation
**What**: Every `loop.run()` or `self._loop.run()` call from agents/ passes `skip_orchestrator=True`.
**How to verify**: `grep -rn "\.run(" /repo/src/organism/agents/ --include="*.py"` — every
call to loop.run must include skip_orchestrator=True.
**Violation = problem**: Recursive orchestrator invocation, infinite delegation.

### INV-3: Memory operations wrapped in try/except
**What**: Every `self.memory.*` call in loop.py is inside try/except.
**How to verify**: Find all lines with `self.memory.` in loop.py. For each, verify it is
within a try block by checking preceding lines for `try:` at appropriate indentation.
**Violation = problem**: Memory failure crashes entire task execution.

### INV-4: Personality vs extra_system_context exclusivity
**What**: Personality injection happens ONLY when extra_system_context is empty (FIX-64).
**How to verify**: `grep -n "extra_system_context" /repo/src/organism/core/loop.py` — find
the conditional that checks `not extra_system_context` before personality injection.
**Violation = problem**: Double personality injection, context pollution for delegated agents.

## Contextual checks (within scope)
- system_parts completeness: user_facts, personality, few-shot, skill_context, memory,
  chat_history, recent_work, extra_system_context, timezone — all assembled.
- Evaluator scoring: quality 0.0-1.0, fast-path returns 0.85, no inflation to 1.0/0.0.
- Media path parity: media tasks get same context as text (FIX-67 intact).
- Fire-and-forget safety: _safe_post_task swallows exceptions, no data corruption.
- LLM model mapping: claude.py tier names match usage in loop.py/planner.py.
- System prompt size: estimate total system_parts — flag if >8000 tokens.
- Dead parameters: run() or _handle_conversation() params accepted but never used.

## How to verify
Script should:
1. Execute INV-1: grep MAX_TOOL_ROUNDS in loop.py, verify definition + loop usage
2. Execute INV-2: grep .run( in agents/, verify all have skip_orchestrator=True
3. Execute INV-3: parse loop.py, find self.memory. lines, check try/except wrapping
4. Execute INV-4: grep extra_system_context in loop.py, verify personality conditional
5. Contextual: read loop.py system_parts assembly, evaluator scoring logic, prompt sizes

## Report format
Report in Russian:
```
OBLAST: Core engine (core/, llm/, safety/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Tool round limit — details
  INV-2 [PASS/FAIL]: Skip orchestrator — details
  INV-3 [PASS/FAIL]: Memory try/except — details
  INV-4 [PASS/FAIL]: Personality exclusivity — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
