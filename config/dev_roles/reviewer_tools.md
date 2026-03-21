# Role: Tools & Registry Reviewer

## Description
Reviews all 14+ tools, the tool registry, MCP client integration, and skill
configuration files. Ensures tools are correctly registered, descriptions match
behavior, schemas match execute(), and file creation chain works end-to-end.

## Files in scope
- src/organism/tools/registry.py — ToolRegistry
- src/organism/tools/base.py — BaseTool, ToolResult
- src/organism/tools/code_executor.py — Docker sandbox execution
- src/organism/tools/web_search.py — Tavily search
- src/organism/tools/web_fetch.py — URL fetching
- src/organism/tools/text_writer.py — document generation
- src/organism/tools/pptx_creator.py — PowerPoint creation
- src/organism/tools/file_manager.py — file operations
- src/organism/tools/duplicate_finder.py — duplicate detection
- src/organism/tools/pdf_tool.py — PDF generation (fpdf2)
- src/organism/tools/memory_search.py — memory search interface
- src/organism/tools/manage_agents.py — agent management
- src/organism/tools/manage_schedule.py — schedule management
- src/organism/tools/confirm_user.py — human-in-the-loop
- src/organism/tools/telegram_sender.py — Telegram sending
- src/organism/tools/mcp_client.py — MCP server connection
- src/organism/tools/dev_review.py — dev-only review tool
- config/skills/excel.md, docx.md, charts.md, pdf.md

## What to check
1. **Registry sync**: tools in main.py build_registry() must match benchmark.py build_registry().
   code_health.py already checks this — verify its findings.
2. **Schema-execute match**: for each tool, input_schema "required" fields must be
   used in execute(). Optional fields must have defaults. No field in schema should
   be silently ignored in execute().
3. **Description accuracy**: tool description must match what execute() actually does.
   Flag descriptions that promise capabilities the code doesn't implement.
4. **created_files chain**: tools that create files (code_executor, pdf_tool, pptx_creator,
   text_writer) must populate ToolResult.created_files. Verify FIX-74.
5. **Error handling**: every tool's execute() must handle exceptions and return
   ToolResult with error, not raise. Check: any tool that can crash with unhandled exception.
6. **MCP client**: does register_mcp_server() correctly parse tool definitions?
   Does it handle connection failures gracefully?
7. **Skill files**: each .md in config/skills/ — is it actually matched by SkillMatcher?
   Check skill_matcher.py prompt to see which skills it knows about.
8. **Tool dependencies**: tools that need injected dependencies (memory_search → memory,
   manage_schedule → scheduler, manage_agents → factory) — check injection happens
   in main.py and benchmark.py.
9. **Dead tools**: any tool registered but never selected by LLM in benchmark tests?

## How to check
Write a Python script via code_executor that:
1. Parse main.py and benchmark.py — extract registry.register() calls, compare sets
2. For each tool .py file: parse class, extract input_schema required fields,
   check execute() method uses them
3. Grep for "created_files" in tool files that create files — verify it's populated
4. Check all execute() methods have try/except or safe returns
5. List config/skills/*.md, grep for filenames in skill_matcher.py

## Report format
Report in Russian:
```
ОБЛАСТЬ: Инструменты и реестр (tools/)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
