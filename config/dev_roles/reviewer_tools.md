# Role: Tools & Registry Reviewer

## Description
Reviews all tools, the ToolRegistry, MCP client, and skill configuration.
Focus on registry consistency, file delivery chain, and error handling.

## Context files
- src/organism/tools/registry.py — ToolRegistry, register(), plan validation
- src/organism/tools/base.py — BaseTool, ToolResult
- src/organism/tools/code_executor.py — Docker sandbox execution
- src/organism/tools/text_writer.py — text file creation
- src/organism/tools/pptx_creator.py — PowerPoint generation
- src/organism/tools/pdf_tool.py — PDF generation
- src/organism/tools/web_fetch.py — URL fetching
- src/organism/tools/web_search.py — Tavily search
- src/organism/tools/file_manager.py — file operations
- src/organism/tools/duplicate_finder.py — entity dedup
- src/organism/tools/memory_search.py — memory query
- src/organism/tools/manage_agents.py — agent CRUD + delegation
- src/organism/tools/manage_schedule.py — scheduler management
- src/organism/tools/confirm_user.py — human-in-the-loop
- src/organism/tools/telegram_sender.py — Telegram sending
- src/organism/tools/dev_review.py — code review (DEV_MODE only)
- src/organism/tools/mcp_client.py — MCP server connection
- main.py, benchmark.py — build_registry()

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Registry sync between main.py and benchmark.py
**What**: build_registry() in main.py and benchmark.py register the same tools.
**How to verify**: `python scripts/code_health.py` — check_tool_registry() result.
Conditional tools (DelegateToAgentTool, TelegramSenderTool, DevReviewTool) excluded.
**Violation = problem**: Benchmark tests different tool set than production.

### INV-2: created_files chain completeness
**What**: Every tool writing files to OUTPUTS_DIR populates `created_files` in ToolResult.
**How to verify**: `grep -l "OUTPUTS_DIR\|data/outputs" /repo/src/organism/tools/*.py` —
for each file, verify `created_files` appears in the ToolResult return.
**Violation = problem**: Files created but never delivered to user via Telegram.

### INV-3: Execute method error handling
**What**: Every `async def execute()` in tools/*.py wraps its main logic in try/except.
**How to verify**: For each .py in tools/ — find `async def execute`, check that the
main body (after initial validation) is inside try/except.
**Violation = problem**: Unhandled tool exception crashes the entire task.

### INV-4: Schema-execute field match
**What**: Fields in `"required"` of input_schema are used in execute().
**How to verify**: For each tool — parse required fields from input_schema, grep for
each field name (e.g., `input.get("field")` or `input["field"]`) in execute().
**Violation = problem**: LLM asked to provide field that tool ignores, or tool uses
field not declared in schema.

## Contextual checks (within scope)
- MCP client: connection error handling, cached discovery, timeout behavior.
- Skill matcher: accuracy of file matching, graceful fallback.
- Tool descriptions: match actual execute() behavior, no stale tool names.
- Dependency injection: set_memory(), set_factory() calls present in build_loop.
- Docker sandbox: volume mounts correct, repo read-only, warm/cold path parity.
- Dead tools: any tool registered but never selected by LLM in benchmarks.

## How to verify
Script should:
1. Run `python scripts/code_health.py` — use result for INV-1
2. Execute INV-2: find tools with OUTPUTS_DIR, verify created_files in each
3. Execute INV-3: find execute() methods, verify try/except wrapping
4. Execute INV-4: parse input_schema required, verify usage in execute()
5. Contextual: read tool implementations, check MCP client, skill matcher

## Report format
Report in Russian:
```
OBLAST: Tools and registry (tools/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Registry sync — details
  INV-2 [PASS/FAIL]: created_files chain — details
  INV-3 [PASS/FAIL]: Execute error handling — details
  INV-4 [PASS/FAIL]: Schema-execute match — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
