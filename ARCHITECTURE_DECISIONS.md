# Architecture Decisions & Sprint History — Organism AI

> Reference document. Read when modifying specific components.
> For quick project context, see CLAUDE.md.
> For Sprint 1–9 (early) decisions and historical bugs, see ARCHITECTURE_DECISIONS_ARCHIVE.md.

## Active Architecture Principles

These principles are from earlier sprints but remain actively enforced:
- Unicode escapes for Russian strings in .py files (never Cyrillic literals)
- code_executor via tmpfile + volume mount (not -c argument)
- Memory operations always wrapped in try/except
- _handle_conversation is the primary execution path (Q-10.4)
- Two-tier LLM: Haiku for classification, Sonnet for execution
- SolutionCache and KnowledgeBase owned by MemoryManager (ARCH-1.3)
- Gateway is single source of truth for chat_history (FIX-65)

## Business Context

Target client: gold mining artel, ~100 people, Russia (remote settlement).
IT infrastructure: 1C (Accounting + Warehouse + Payroll), 2-3 in-house 1C developers.
Pain points: manual document workflow, duplicate nomenclature in 1C, manual fuel tracking, Rosnedra reporting.
Contact: owner's son, ~40 years old, technically literate.

### Pricing Model
- Monthly: 300,000 RUB/month (includes ~25K RUB for Claude API)
- Season bonus: tied to measurable results (fuel savings, production volume)
- Onboarding (first 2 months): included
- Server (one-time): 70-100K RUB (client purchases)

### Commercial Proposal
See KP_Organism_AI_Artel.md in project knowledge.

## Sprint 9+ Decisions

### Q-9.9: Subtask progress in Telegram
`progress_callback` passed through `IncomingMessage.metadata` →
`CoreLoop.run()`. During decomposition, Telegram shows "Часть X/Y: ..."
instead of the static ticker. The callback is fire-and-forget (try/except), so rate-limiting
or deleted messages do not crash the execution.

### Q-9.7: Docker production deployment
Production-ready containerization:
- `Dockerfile`: python:3.11-slim, system deps, pip install from pyproject.toml, HEALTHCHECK
- `docker-compose.yml`: bot + postgres (pgvector/pgvector:pg15), healthchecks, named volumes,
  DATABASE_URL injected, docker.sock mounted for sandbox
- `.env.production.example`: template with all required/optional env vars
- `scripts/deploy.sh`: git pull → docker-compose build → up -d --no-deps bot → health check

### Q-9.6: Multi-tenancy (artel_id isolation)
All DB queries in memory layer filtered by `settings.artel_id` (from ARTEL_ID env var).
Since `artel_id` column added via migration `_m002_artel_id` (not in ORM model), filtering
uses `text("artel_id = :artel_id")` with `.params()` for ORM queries and raw SQL conditions.
- `longterm.py`: save_task sets artel_id after INSERT; search_similar filters in vector,
  BM25, and fallback queries; get_stats filtered
- `solution_cache.py`: get() filters by artel_id; put() sets artel_id on INSERT;
  get_stats() uses raw SQL with artel_id filter
- `knowledge_base.py`: get_rules() filters by artel_id; add_rule() sets artel_id on INSERT

### Q-9.2–Q-9.5: Agent Factory (2026-03-11)
Role templates in `config/roles/*.md`, `AgentFactory` in `agents/factory.py`,
`MetaOrchestrator` in `agents/meta_orchestrator.py`. Commands: /agents, /create_agent, /assign.

### FIX-75b: First client personality separation: artel_zoloto.md (2026-03-15)
Problem: ARTEL_ID was "default" — the first client (gold mining artel) shared the universal
personality file with no client-specific settings (language, terminology, style).
Solution: Created `config/personality/artel_zoloto.md` with artel-specific personality:
hardcoded Russian language, mining terminology, structured reports in Russian.
Added `ARTEL_ID=artel_zoloto` to `.env` and `.env.example`. The `default.md` remains
universal ("respond in user's language") for future clients. PersonalityConfig already
supports artel-specific files via `settings.artel_id` — no code changes needed.
Files: `config/personality/artel_zoloto.md`, `.env.example`.

### FIX-83: Timezone support — store UTC, display local (2026-03-17)
Problem 1: text_writer generates documents with "2025" dates — its internal LLM call has no
current date context. Problem 2: memory_search task timestamps are 10 hours off — PostgreSQL
stores UTC, user is in UTC+10 (Vladivostok).

Root cause: no timezone configuration anywhere in the system.

Solution — one setting, one utility module:
1. **config/settings.py**: Added `timezone` field (default "Asia/Vladivostok", env TIMEZONE).
2. **src/organism/utils/timezone.py**: `now_local()` returns current time in client timezone,
   `to_local(dt)` converts UTC/naive datetime to local, `today_local()` returns "DD.MM.YYYY".
3. **loop.py**: Both `datetime.now().strftime()` calls replaced with `today_local()`. Removed
   unused `from datetime import datetime` import.
4. **text_writer.py**: Current date injected into system prompt for both RU and EN variants.
   Per-section calls inherit the date via the same `system` variable.
5. **longterm.py**: `_to_dict()` now calls `to_local(m.created_at)` before `.isoformat()`.
6. **logger.py**: Log filename uses `now_local()` instead of `datetime.now()`.
7. **.env.production.example**: Added `TIMEZONE=Asia/Vladivostok`.

Principle: store UTC in PostgreSQL (correct), display in local timezone for user-facing output.
Internal scheduling (scheduler.py) keeps `datetime.utcnow()` — unchanged.

Files: `config/settings.py`, `src/organism/utils/timezone.py`, `src/organism/core/loop.py`,
`src/organism/tools/text_writer.py`, `src/organism/memory/longterm.py`,
`src/organism/logging/logger.py`, `.env.production.example`.

### FIX-82: Robust outline parsing in text_writer sectional generation (2026-03-17)
Problem: FIX-81 sectional generation falls back to SINGLE mode because Haiku doesn't return clean
JSON. Common Haiku responses: JSON wrapped in ```json fences, preamble text before JSON array,
numbered list instead of JSON, trailing text after JSON, or alternative key names (section/name
instead of title, description/content instead of brief).

Solution — 3-level fallback parser in `_parse_outline()`:
1. **Level 1**: Strip markdown fences (```json...```), try `json.loads` on cleaned text.
2. **Level 2**: Regex `\[.*\]` (re.DOTALL) to extract JSON array from mixed text.
3. **Level 3**: Parse numbered/bulleted/heading lines into `{title, brief}` dicts — handles cases
   where Haiku returns a plain list instead of JSON.

Added `_normalize_sections()` to handle key name variations (title/section/name, brief/description/
content) and string-only arrays. Debug logging (`outline_raw`) added right after Haiku response
for future diagnostics.

Files: `src/organism/tools/text_writer.py`.

### FIX-81: Sectional generation in text_writer for long documents (2026-03-17)
Problem: text_writer calls Sonnet with max_tokens=8000, but model stops at ~5500 tokens (~14 pages).
Raising max_tokens doesn't help — the LLM decides when text is "done". Result: 14-page business plan
instead of promised 20+.

Root cause: a single LLM call cannot reliably generate documents >15 pages.

Solution — sectional generation inside text_writer (external interface unchanged):
1. **Mode detection**: heuristic `_is_long_document()` checks for keywords (business plan, report,
   detailed, etc.) and section count (>5 numbered items). This is an internal tool strategy choice,
   not an agent decision — analogous to code_executor choosing warm vs cold Docker.
2. **Phase 1 — Outline (Haiku, ~300 tokens)**: generates JSON array of 8-15 sections with title
   and brief description. Parsed via direct JSON + regex fallback. Failed parse → SINGLE fallback.
3. **Phase 2 — Per-section (Sonnet, ~2000 tokens each, temp=0.5)**: each section gets the full
   outline for structure awareness + previous sections summary (first 200 chars each) for coherence.
   Failed sections are skipped; if >50% fail → SINGLE fallback.
4. **Phase 3 — Merge**: simple concatenation. No polish LLM call (WriterAgent already has _polish).

Scale: 10 sections × 2000 tokens = 20K tokens ≈ 80K chars ≈ 40+ pages PDF. Linear scaling.

Files: `src/organism/tools/text_writer.py`.

### FIX-80: Two-step pipeline for long PDFs — text_writer + pdf_tool source_file (2026-03-17)
Problem: Long PDF documents (business plans, reports, 10-20 pages) generated via code_executor + pdf.md
skill. LLM puts all document text as Python string literals → ~6000+ tokens on content + ~1500 on fpdf2
code → overflows max_tokens=8192 → truncated 1-page PDF. FIX-79 compactness was a band-aid.

Root cause: content generation and PDF rendering conflated in one LLM output.

Solution — separate content generation from PDF rendering:
1. **pdf_tool.py**: Added `source_file` parameter to input_schema. When `source_file` is set and
   `content` is empty, reads markdown from `data/outputs/{source_file}` and renders it via the
   existing FIX-77 markdown parser. Backward-compatible: if `content` is passed, it takes priority.
2. **text_writer.py**: Raised `max_tokens` from 4000 to 8000 (~32K chars, 20+ pages markdown).
   This is a separate LLM call (model_tier="balanced"), doesn't affect _handle_conversation context.
3. **config/skills/pdf.md**: Rewritten. Routes by document length: short (1-3 pages) → pdf_tool
   directly; long (4+ pages) → two-step pipeline (text_writer → pdf_tool with source_file).
   code_executor + fpdf2 remains as fallback for charts/matplotlib edge cases.

Pipeline: Round 1: text_writer(prompt, filename.md) → ~100 tokens tool call. Round 2:
pdf_tool(action=create, source_file=filename.md, filename.pdf) → ~50 tokens tool call.
Total: ~150 tokens vs 6000+ before.

Files: `src/organism/tools/pdf_tool.py`, `src/organism/tools/text_writer.py`, `config/skills/pdf.md`.

### FIX-79: code_executor empty input guard + pdf.md compactness strategy (2026-03-16)
Problem 1: code_executor receives `{}` (no "code" key) → `input["code"]` raises KeyError →
UnboundLocalError on `result` → raw error shown to user.

Problem 2: Long documents (20+ pages) overflow token limits even with SKILL-2. LLM generates
~6000 tokens of string literals in add_text() calls. fpdf2 can't append to existing PDFs,
so multi-call sectional approach is impossible.

Solution:
1. **code_executor.py**: `input["code"]` → `input.get("code", "")` + early return with clear
   error message if empty/missing. Prevents UnboundLocalError cascade.
2. **pdf.md**: Added "CRITICAL: code compactness" section at top. Rules: concise text (2-3
   sentences per point), tables instead of text for financials, add_bullet for lists, target
   10-12 quality pages instead of empty 20-page PDF.

Files: `src/organism/tools/code_executor.py`, `config/skills/pdf.md`.

### SKILL-2: PDF skill for long documents via code_executor + fpdf2 (2026-03-16)
Problem: `pdf_tool` passes entire document content in one tool call input. For long documents
(20+ pages, ~40K chars) this exceeds token limits. Result: 1-page PDF with only the title,
agent hallucinating content that doesn't exist in the file. Excel and DOCX already solved this
via code_executor + skills (compact code ~3K tokens vs raw text ~20K tokens).

Solution — 3 changes:
1. **`config/skills/pdf.md`**: Skill file with fpdf2 template for code_executor. Helper functions
   (add_title, add_heading, add_text, add_bullet, add_hr, add_table) match pdf_tool styling
   (same colors, fonts, table formatting). Agent generates Python code that builds PDF
   programmatically — content lives in code, not in tool call input.
2. **`sandbox/Dockerfile`**: Added `fpdf2` to pip install. Copied DejaVuSans fonts to
   `/sandbox/fonts/` (COPY from `sandbox/fonts/` dir, since Docker can't COPY from `../`).
3. **`skill_matcher.py`**: Updated SKILL_SELECT_PROMPT to route PDF tasks to `pdf.md` instead
   of "use pdf_tool directly".

pdf_tool remains for quick short PDFs (1-2 pages) and PDF reading. Long documents route through
code_executor with the PDF skill, same pattern as Excel (excel.md) and DOCX (docx.md).

Files: `config/skills/pdf.md`, `sandbox/Dockerfile`, `sandbox/fonts/DejaVuSans*.ttf`,
`src/organism/core/skill_matcher.py`.

### FIX-78: Structural file delivery via TaskResult.created_files (2026-03-16)
Problem: `loop.py` appended text marker `"Saved files: {last_file}"` to answer — only the last file.
`gateway.py` parsed this with regex `r'Saved files:\s*(\S+)'` — fragile, delivered only the first match.
Multi-file tasks (e.g., Excel + PPTX) lost all but one file.

Solution — structural `created_files` channel from ToolResult to Telegram:
1. **TaskResult.created_files**: new `list[str]` field (dataclass default `[]`)
2. **_handle_conversation()**: passes `created_files=created_files` to TaskResult, removes text
   marker append (`answer + "\nSaved files: ..."`)
3. **gateway.handle_message()**: reads `result.created_files`, resolves paths via `os.path.exists`,
   passes `files` list in metadata
4. **gateway._prepare_response()**: regex `r'Saved files:\s*(\S+)'` removed entirely. Uses
   `metadata["files"]` instead. Multi-file: first file as primary `OutgoingMessage`, rest in
   `metadata["extra_files"]`. Cleans "Saved files:" from caption text via `re.sub`.
5. **telegram.py**: all 3 handlers (handle_task, handle_voice, handle_media) send `extra_files`
   after primary file — each via `answer_document()` + `os.unlink()`.

What remains unchanged: "Saved files: ..." in tool output (code_executor, pdf_tool, pptx_creator) —
this is for LLM context, not for gateway. `ToolResult.created_files` (FIX-74) — source of truth.

Files: `src/organism/core/loop.py`, `src/organism/channels/gateway.py`,
`src/organism/channels/telegram.py`.

### FIX-77: pdf_tool full markdown rendering (2026-03-16)
Problem: `_create_pdf_sync()` only handled `# H1`, `## H2`, and `- bullet`. LLM generates full
markdown: `### H3`, `**bold**`, `*italic*`, `| table |`, `---` (HR), `1. numbered`. All rendered
as raw text with visible asterisks, pipes, and dashes.

Solution — replaced line-by-line parser with block-aware parser + 5 helper functions:
1. **`_clean_markdown(text)`**: strips `**bold**`, `*italic*`, `__bold__`, `_italic_` to plain text
2. **`_draw_hr(pdf)`**: `---`/`***`/`___` → thin gray horizontal line
3. **`_draw_heading(pdf, text, font, size, color)`**: H1 (15pt, #1E3A5F), H2 (13pt, #1E3A5F),
   H3 (12pt, #333333) — all bold with color reset after
4. **`_draw_table(pdf, lines, font)`**: detects `|` blocks, parses cells, skips separator rows
   (`---`), renders header row (bold white on #1E3A5F) + data rows (alternating #F5F5F5/white),
   equal column widths, cell borders. Graceful fallback to plain text on parse error.
5. **`_draw_text(pdf, text, font)`**: standard multi_cell for body text

Parser uses `while i < len(lines)` loop (not `for`) to handle multi-line table blocks.
Numbered lists (`1. text`) pass through `_clean_markdown()` and render correctly.

No changes to fpdf2, DejaVuSans fonts, `_read_pdf`, `execute`, or `input_schema`.

Files: `src/organism/tools/pdf_tool.py`.

### FIX-76: Gateway chat_history truncation loses follow-up context (2026-03-16)
Problem: `gateway.py` saved assistant messages to chat_history with `[:2000]` truncation, while
`ChatHistory.save_message()` accepts up to 5000 chars and `ChatMessage.content` is TEXT (unlimited).
When agent produces a long response ending with a follow-up proposal ("Export to PDF?"), the proposal
gets truncated. User replies "yes" — agent has no context for what "yes" refers to, repeats the task.

Solution — 3 changes:
1. **gateway.py line 65**: `/assign` handler `result_text[:2000]` → `result_text[:5000]`
2. **gateway.py line 115**: Main handler `response_text[:2000]` → `response_text[:5000]`
3. **loop.py HIST-1 block**: Last 2 messages in chat history injection get `[:3000]` instead of
   `[:1000]`, ensuring the most recent assistant response (which the user is replying to) preserves
   more context including follow-up proposals.

Files: `src/organism/channels/gateway.py`, `src/organism/core/loop.py`.

### DOCKER-PROD: Production hardening Docker Compose (2026-03-16)
Problem: Docker config (Q-9.7) was functional but not production-ready: dummy healthcheck
(`python -c "import sys; sys.exit(0)"`), PostgreSQL port exposed externally, no backups,
no resource limits, no .dockerignore.

Solution — 8 changes:
1. **Real healthcheck**: `scripts/health_check.py` — sync script checks DB connectivity
   (psycopg2 SELECT 1) + heartbeat file freshness (< 120s). Background asyncio task in
   `run_telegram()` writes unix timestamp to `data/heartbeat` every 30s.
2. **Sandbox in docker-compose**: `sandbox` service builds the image, `bot` depends on it
   via `service_completed_successfully`. Guarantees sandbox image exists before bot starts.
3. **PostgreSQL hardening**: Removed `ports: "5433:5432"` (external access). Added
   `expose: "5432"` (internal Docker network only).
4. **Backup strategy**: `scripts/backup.sh` — pg_dump | gzip, 30-day retention.
   `scripts/restore.sh` — gunzip | psql. Deploy script runs pre-deploy backup automatically.
5. **Deploy script**: `.env` validation (no default passwords, no placeholders), pre-deploy
   backup, git pull + build + restart, 90s health check polling.
6. **Resource limits**: bot 1G/256M memory, 1 CPU. postgres 512M/128M memory.
   Uses `deploy.resources` (Compose v3.x).
7. **.env.production.example**: Critical security warning about POSTGRES_PASSWORD.
8. **.dockerignore**: Excludes .git, .env, data/, backups/, __pycache__, tests/, *.md
   (except config/**/*.md). Faster builds, no secrets in image.

Files: `docker-compose.yml`, `main.py`, `scripts/health_check.py`, `scripts/backup.sh`,
`scripts/restore.sh`, `scripts/deploy.sh`, `.env.production.example`, `.dockerignore`.

### SCHED-1a: DB Persistence for Scheduled Jobs

**Problem**: `ProactiveScheduler` stored jobs only in memory (`self.jobs` dict). On bot restart,
all user-created jobs were lost. Only `DEFAULT_ARTEL_JOBS` survived (hardcoded).

**Solution**: New `ScheduledJobRecord` ORM model in `database.py` + migration #11 (`scheduled_jobs`
table with UNIQUE index on `(artel_id, name)`). `ProactiveScheduler` gains persistence layer:
- `load_from_db()`: loads user-created jobs at startup (after DEFAULT_ARTEL_JOBS, overwrites if same name)
- `_save_job()`: upsert job to DB (UPDATE first, INSERT if not found)
- `_delete_job_from_db()`: remove job from DB
- `_update_last_run()`: persist last_run after each execution in `_loop()`
- `create_job()`: public async method — add to self.jobs + save to DB
- `delete_user_job()`: public async method — refuses to delete system jobs

`time_of_day` stored as `"HH:MM"` string (simpler than PostgreSQL TIME type).
`is_system` column distinguishes DEFAULT_ARTEL_JOBS from user-created.
Notify output limit increased: `output[:500]` → `output[:4000]`.

Startup order in `run_telegram()`: add DEFAULT_ARTEL_JOBS → `load_from_db()` → start scheduler.

Files: `memory/database.py`, `core/scheduler.py`, `main.py`.

### SCHED-1b: ManageScheduleTool — Natural Language Schedule Management

New `ManageScheduleTool` in `tools/manage_schedule.py`, following `manage_agents.py` pattern:
- 5 actions: `list`, `create`, `delete`, `enable`, `disable`
- Setter injection: `set_scheduler(scheduler)` called in `run_telegram()` after scheduler creation
- `create` validates required fields per schedule_type (daily→time_utc, weekly→time_utc+weekday,
  interval→interval_minutes), creates `ScheduledJob` with `enabled=True`, calls `create_job()`
- `delete` delegates to `delete_user_job()` (refuses system jobs)
- `enable`/`disable` use new `set_job_enabled()` method (with DB persistence)
- `list` shows all jobs with schedule description, enabled status, last_run

New `set_job_enabled()` async method on `ProactiveScheduler` — updates in-memory + DB.
Registered in `main.py` and `benchmark.py` `build_registry()`. Benchmark task #30 added.
HELP_TEXT updated with hint about natural language schedule management.

Files: `tools/manage_schedule.py` (new), `core/scheduler.py`, `main.py`, `benchmark.py`,
`commands/handler.py`.

### FIX-88: Targeted channel publishing — channel_id on ScheduledJob
**Problem:** MEDIA-LAUNCH sent all scheduled job results to both personal messages and
the global `TELEGRAM_CHANNEL_ID`. Jobs like `morning_summary` (personal artel summary)
should not be published to a public channel — only media content jobs should.

**Solution:** Structural fix — added `channel_id: str = ""` field to `ScheduledJob` dataclass.
Each job explicitly declares its target channel. Empty = personal messages only.
The `_notify()` callback receives `channel_id` from the job and publishes to it only if non-empty.
No name-prefix checking or behavioral heuristics.

**Changes:**
1. `src/organism/core/scheduler.py`: `channel_id` field on ScheduledJob, media jobs get
   `channel_id=settings.telegram_channel_id`, notify call passes `job.channel_id`,
   `load_from_db`/`_save_job` persist channel_id
2. `main.py` → `_notify()`: signature gains `channel_id: str = ""`, publishes to channel
   only if `channel_id` is non-empty (replaces old `settings.telegram_channel_id` check)
3. `src/organism/tools/manage_schedule.py`: `channel_id` in input_schema, `_action_create`,
   and `_action_list` display
4. `src/organism/memory/database.py`: migration 12 — `ALTER TABLE scheduled_jobs ADD COLUMN
   IF NOT EXISTS channel_id TEXT DEFAULT ''`

Files: `scheduler.py`, `main.py`, `manage_schedule.py`, `database.py`.

### FIX-89: Scheduler — config instead of hardcode + personality_id + enable/disable persistence
**Problems (3 bugs):**
1. `DEFAULT_ARTEL_JOBS` was hardcoded in `scheduler.py` — business logic of a specific client
   (gold mining) embedded in the platform core. Media jobs had `artel_id="ai_media"` but bot runs
   with `ARTEL_ID=artel_zoloto` → `load_from_db()` filter `WHERE artel_id = 'artel_zoloto'` found
   nothing → every restart reset media jobs to `enabled=False`.
2. `enable_job()`/`disable_job()` were sync-only, did NOT write to DB → state lost on restart.
   Meanwhile `set_job_enabled()` (async, from manage_schedule tool) DID write to DB — two code
   paths with divergent behavior.
3. No per-job personality — `task_runner(job.task_text)` always used the default startup personality.
   Impossible to run `media_daily_news` with `ai_media` personality.

**Solution:**

**Part 1 — Config-based jobs:**
- New `config/jobs/artel_zoloto.json` (7 jobs), `config/jobs/default.json` (empty `[]`).
- `load_jobs_from_config(artel_id)` function: reads `config/jobs/{artel_id}.json`, falls back
  to `default.json`, then empty list. Parses JSON → list[ScheduledJob]. `artel_id` always from
  `settings.artel_id` (not from JSON). `channel_id` and `personality_id` taken from JSON as-is.
  `enabled` from `enabled_default` field. All wrapped in try/except.
- `DEFAULT_ARTEL_JOBS` list removed from `scheduler.py`.

**Part 2 — personality_id on ScheduledJob:**
- `ScheduledJob` gains `personality_id: str = ""` field.
- `_loop()` passes `personality_id=job.personality_id` to `task_runner`.
- `CoreLoop.run()` gains `personality_id: str = ""` parameter. If non-empty and different
  from current personality: creates temporary `PersonalityConfig`, loads it, uses as
  `active_personality` for this call only. `self.personality` is never mutated.
- `manage_schedule.py`: `_action_create` reads `personality_id` from input; `_action_list`
  shows `[personality_id]` if set.

**Part 3 — enable/disable persistence:**
- `enable_job()`/`disable_job()` now fire-and-forget write to DB via
  `asyncio.get_event_loop().create_task(self._save_job(...))`. Wrapped in try/except for
  benchmark mode (no running loop).

**Part 4 — Startup sync (config ↔ DB):**
- New `load_and_sync(artel_id)` method: loads config → loads DB states → merges (DB wins
  for `enabled`/`last_run`) → saves upserts → loads user-defined jobs.
- New `_load_states_from_db()`: `SELECT name, enabled, last_run FROM scheduled_jobs WHERE artel_id`.
- New `_load_user_jobs_from_db()`: loads only `is_system=false` jobs (user-created via tool).
- Old `load_from_db()` removed.

**Part 5-6 — main.py / benchmark.py:**
- `main.py` calls `await scheduler.load_and_sync(settings.artel_id)` (replaces add_job loop + load_from_db).
- `benchmark.py` imports `load_jobs_from_config`, calls it directly (no DB sync in benchmark mode).

**Part 7-8 — DB migration #13:**
- `_m013_scheduled_jobs_personality_id`: `ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS personality_id TEXT DEFAULT ''`.
- `_save_job()` and `_load_user_jobs_from_db()` updated to include `personality_id`.

Files: `scheduler.py`, `core/loop.py`, `manage_schedule.py`, `database.py`, `main.py`,
`benchmark.py`, `config/jobs/artel_zoloto.json`, `config/jobs/default.json`.

### FIX-90: Review перед публикацией в канал — requires_approval на ScheduledJob
**Problem:** Посты публиковались в Telegram-канал автоматически без проверки человеком.
Для контента от имени компании нужен ручной review.

**Existing HumanApproval (Q-6.3) не подходит** — он использует `asyncio.Event` с 300s
таймаутом для in-task confirmation. Review постов требует другой механизм: пост может
ждать часы, таймаута нет.

**Solution:**

1. `ScheduledJob.requires_approval: bool = False` — per-job флаг. В `config/jobs/artel_zoloto.json`
   все медиа-задачи (`media_daily_news`, `media_weekly_digest`, `media_weekly_research`) получили
   `requires_approval: true`.

2. `ProactiveScheduler._pending_publications: dict[str, dict]` — in-memory хранилище
   постов на проверке. key = `short_id` (8 hex символов). Методы:
   `add_pending_publication`, `get_pending_publication`, `remove_pending_publication`,
   `list_pending_publications`. При рестарте теряются (намеренно — in-memory).

3. `_loop()` передаёт `job.requires_approval` в `notify()`.

4. `_notify()` в `main.py` получает `requires_approval: bool = False`. Если
   `channel_id and requires_approval` — создаёт `short_id`, кладёт в pending, отправляет
   в личку review-сообщение с `/publish <id>` и `/reject_post <id>`. Канал не трогает.
   Иначе — обычный режим (личка + канал).

5. `CommandHandler` получает 3 новые команды:
   - `/pending` — список постов на проверке
   - `/publish <id>` — отправить пост в канал + убрать из pending
   - `/reject_post <id>` — удалить из pending без публикации

6. `manage_schedule.py` — `requires_approval` в input_schema, `_action_create`, `_action_list`
   (показывает 📝 рядом с задачами с requires_approval=True).

7. `database.py` — миграция #14: `ALTER TABLE scheduled_jobs ADD COLUMN IF NOT EXISTS
   requires_approval BOOLEAN DEFAULT false`. `_save_job()` и `_load_user_jobs_from_db()` обновлены.

Files: `scheduler.py`, `main.py`, `commands/handler.py`, `tools/manage_schedule.py`,
`database.py`, `config/jobs/artel_zoloto.json`.

### FIX-91: ORM sync + startup ordering + docs sync
**Problem 1:** `ScheduledJobRecord` ORM class in `database.py` was missing 3 columns
(`channel_id`, `personality_id`, `requires_approval`) added by migrations #12-14. On a
fresh DB, `Base.metadata.create_all` would create the table without them.

**Problem 2:** `run_telegram()` called `scheduler.load_and_sync()` before DB tables were
guaranteed to exist. `memory.initialize()` (which calls `init_db()`) was only invoked on
first `CoreLoop.run()` (FIX-24). On a fresh DB, `load_and_sync()` would silently fail.

**Problem 3:** ARCHITECTURE_DECISIONS.md Testing History showed "29 tasks" and wrong quality
score, while CLAUDE.md and actual benchmark had 30 tasks.

**Solution:**
1. Added `channel_id`, `personality_id`, `requires_approval` columns to `ScheduledJobRecord`
   ORM class. Migrations remain idempotent (`IF NOT EXISTS`), no conflict.
2. Added `await loop.memory.initialize()` in `run_telegram()` before `scheduler.load_and_sync()`.
   `CoreLoop.run()` safety net preserved (FIX-24).
3. Synced docs: 30 tasks, quick 7/7 quality 0.89.

Files: `database.py`, `main.py`, `CLAUDE.md`, `ARCHITECTURE_DECISIONS.md`.

### FIX-92: Persist pending publications to DB
**Problem:** `_pending_publications` was an in-memory `dict` on `ProactiveScheduler`. On bot
restart (deploy, OOM, Docker restart) all pending review posts were lost silently.

**Solution:**
1. `PendingPublication` ORM model in `database.py` (short_id, text, channel_id, job_name,
   artel_id, created_at). Migration #15 creates `pending_publications` table.
2. Replaced 4 in-memory methods on `ProactiveScheduler` with async DB-backed versions:
   `add_pending_publication`, `get_pending_publication`, `remove_pending_publication`,
   `list_pending_publications`. All wrapped in try/except with structlog.
3. Added `await` at all call sites: `_notify()` in main.py, `_handle_publish()`,
   `_handle_reject_post()`, `_handle_pending()` in handler.py.
4. Removed `self._pending_publications` dict entirely.

Files: `database.py`, `scheduler.py`, `main.py`, `commands/handler.py`.

### FIX-93: BotSender + async enable/disable
**Problem 1:** `Bot(token=...) → send → bot.session.close()` duplicated in 3 places
(main.py `_send_approval`, main.py `_notify`, handler.py `_handle_publish`). Hard to add
retry/rate-limiting, risk of session leak.

**Problem 2:** `enable_job()` / `disable_job()` used deprecated
`asyncio.get_event_loop().create_task()` for fire-and-forget DB persistence. Neighboring
`set_job_enabled()` already had proper async/await.

**Solution:**
1. `BotSender` class in `channels/bot_sender.py`: `send(chat_id, text) → bool`,
   `send_many(chat_ids, text) → int`. One Bot() per call, always closes session.
2. `_send_approval()` and `_notify()` in main.py refactored to use `bot_sender`.
3. `_handle_publish()` in handler.py uses `self.bot_sender.send()` instead of direct Bot().
4. Gateway gains `bot_sender` param, passes to CommandHandler.
5. `enable_job()` / `disable_job()` → async with `await self._save_job()`.
6. `_handle_schedule_toggle()` → async with `await`.

Files: `channels/bot_sender.py` (new), `main.py`, `channels/gateway.py`,
`commands/handler.py`, `core/scheduler.py`.

### FIX-94: Code Review Round 5 — Cleanup
Three issues fixed:

1. **Dead file removed:** `core/context_budget.py` (~80 lines) was not imported anywhere
   since FIX-66. File deleted, stale comment in planner.py updated.

2. **Timezone context for schedule tool:** `manage_schedule` description said "All times must
   be in UTC", but user says "9 утра" meaning local time (Asia/Vladivostok = UTC+10).
   Added `## Timezone` section to system prompt in `_handle_conversation` with user timezone
   from settings, so LLM converts local→UTC before calling tools. Updated tool description
   to reference system context instead of demanding raw UTC.

3. **Atomic `/publish`:** `_handle_publish()` had a race condition — two admins could
   `/publish` the same post simultaneously (get → send → remove = two sends). Replaced
   `remove_pending_publication` with `DELETE...RETURNING` (atomic). `_handle_publish` now
   does remove-first: if send fails, re-adds the post via `add_pending_publication` for retry.

Files: `context_budget.py` (deleted), `loop.py`, `manage_schedule.py`, `scheduler.py`,
`handler.py`, `planner.py`.

## REVIEW-1: Dev-only code review infrastructure

DEV_MODE env var (settings.py) gates dev-only tools. Default false, true for development.

**Sandbox repo access**: `_repo_volumes()` helper in code_executor.py mounts /repo/src/,
/repo/config/, /repo/*.md as read-only inside Docker sandbox. Both warm and cold paths.

**scripts/code_health.py**: 7 deterministic checks (stdlib only, no src.organism imports):
1. File Structure Sync — .py files vs CLAUDE.md references
2. Tool Registry Sync — main.py vs benchmark.py build_registry()
3. Command Sync — HELP_TEXT vs CONVENTIONS.md
4. Orphan Files — .py files not imported anywhere
5. Dead Imports — unused imports from src.organism.*
6. Benchmark Count — TASKS count vs docs
7. Migration Order — sequential version numbers

**DevReviewTool** (tools/dev_review.py): runs code_health.py via subprocess, loads
role templates from config/dev_roles/{scope}.md, returns structured review instruction.
Gated on DEV_MODE. 10 scopes: memory, core, tools, channels, agents, infra, docs,
quality, self_improvement, all.

**config/dev_roles/**: 10 reviewer stub files + review_coordinator. Content in REVIEW-2.

Files: `config/settings.py`, `src/organism/tools/code_executor.py`,
`scripts/code_health.py` (new), `src/organism/tools/dev_review.py` (new),
`main.py`, `benchmark.py`, `.dockerignore`, `.env.example`, `.env.production.example`,
`config/dev_roles/` (new, 11 files).

## REVIEW-2: Code review role templates

9 specialized reviewer templates + 1 coordinator in config/dev_roles/:

| Template | Scope | Key checks |
|----------|-------|------------|
| reviewer_memory | memory/ (14 files) | artel isolation, ORM sync, migrations, connections, embeddings |
| reviewer_core | core/, llm/, safety/ (9 files) | context chain, media parity, evaluator, tool rounds |
| reviewer_self_improvement | self_improvement/ (10 files) | PVC integration, auto-improver cycle, dead code |
| reviewer_tools | tools/, config/skills/ (18 files) | registry sync, schema-execute match, created_files |
| reviewer_channels | channels/, commands/ (6 files) | command docs, chat history, file delivery, HTML escaping |
| reviewer_agents | agents/, planner, decomposer (10 files) | recursion guard, dead agents, factory singleton |
| reviewer_infra | scheduler, MCP, A2A, Docker (12 files) | scheduler persistence, atomic publish, settings completeness |
| reviewer_docs | all .md documentation (8 files) | file structure sync, benchmark metrics, convention drift |
| reviewer_quality | benchmark, pre_commit (4 files) | task coverage, score inflation, edge cases |
| review_coordinator | orchestrates all 9 | cross-module issues, prioritized action plan |

Each template: English instructions for LLM, Russian report output.
DevReviewTool loads templates by scope, runs code_health.py, returns structured instruction.

Files: `config/dev_roles/*.md` (10 files updated).

## Testing History

### Current Benchmark (March 2026)
- 30 tasks total (30/30 success with Docker+DB)
- Quick benchmark: 7/7, quality 0.89
- Sprint 9 tasks: Agent Factory, Universal Planner, MCP JSON-RPC — all passing
- For historical benchmark data, see ARCHITECTURE_DECISIONS_ARCHIVE.md
