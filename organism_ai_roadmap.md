# Organism AI — Roadmap

## Статус: Март 2026

### Завершено (Спринты 1–9)

- Sprint 1–2: CoreLoop, Planner, Evaluator, Memory (pgvector + BM25 hybrid search), Solution Cache, Knowledge Base
- Sprint 3–4: Agent specialization (Coder/Researcher/Writer/Analyst), User Facts, Commands, Auto-improvement, Prompt Versioning
- Sprint 5: Memory Graph (temporal edges, CausalAnalyzer, ProceduralTemplates, SearchPolicy)
- Sprint 6: Orchestrator state machine, ProactiveScheduler, Human-in-the-loop approval, Personality, Gateway
- Sprint 7: Structured reflections, Benchmark-driven prompt optimization, Few-shot store, Evolutionary prompt search, Cross-agent knowledge sharing
- Sprint 8: MCP client/server, 1C MCP server (demo), Duplicate finder, Organism as MCP server, A2A protocol
- Sprint 9 (Universal Planner): Q-9.0, Q-9.1, Q-9.6, Q-9.7, Q-9.9, Q-10.1–Q-10.4 ✅
- Sprint 9 (Skills): SKILL-1 (technical skills system) ✅
- Sprint 9 (Agent Factory): Q-9.2 (role templates + AgentFactory), Q-9.3 (personality generation), Q-9.4 (MetaOrchestrator), Q-9.5 (commands) ✅
- Sprint 9 (Fixes): FIX-33 — FIX-94 ✅
- Sprint 9 (Tech debt): Q-9.8 (MCP JSON-RPC 2.0), Q-9.10 (/errors command) ✅
- AGENT-UX: manage_agents tool — natural language agent management ✅
- MEM-1: Temporal retrieval — date filtering in memory_search ✅ (+ MEM-1b limit=50, MEM-1c combined+compact+15K)
- Инфраструктура Claude Code: MCP-1 (Context7 + PostgreSQL) ✅
- Memory Verification Loop: INSIGHT-1 ✅
- MEDIA-LAUNCH: Telegram-канал публикация + ai_media personality + content scheduler jobs ✅
- TG-UX: Cancel/Retry + HTML output in Telegram ✅
- PERF-2: Embedding fast timeout + fire-and-forget memory save ✅
- SCHED-1a/1b: DB persistence for scheduled jobs + ManageScheduleTool ✅
- DOCKER-PROD: Production hardening — healthcheck, backup/restore, resource limits ✅
- BotSender: Centralized Telegram sender (FIX-93) ✅
- Benchmark: 30/30 задач, quality 0.87–0.89 ✅
- REVIEW-1: Dev-only code review infrastructure (DEV_MODE, code_health.py, DevReviewTool, dev_roles stubs) ✅
- REVIEW-2: Code review role templates — 9 reviewers + coordinator (memory, core, tools, channels, agents, infra, docs, quality, self_improvement) ✅
- FIX-95a: Artel isolation completion — chat_messages, few_shot_examples, memory_edges + text_writer created_files ✅
- FIX-95b: Recursion depth guard for delegate chains + dead code cleanup (ab_test.py) ✅
- FIX-96: Artel isolation final pass — manager.py, metrics.py, auto_improver.py + dead import cleanup ✅
- EMAIL-MCP: Gmail MCP server — OAuth2 auth, 5 tools (send, read_inbox, read_email, search, labels), port 8092 ✅
- EMAIL-MCP-2: Bot integration — planner confirm_with_user rule, env config, docs ✅

### Текущие расходы (тестирование, 3 человека)

| Статья | Стоимость | Период |
|--------|-----------|--------|
| Anthropic API (Claude) | ~$30–50/мес | при активном тестировании |
| OpenAI Embeddings | ~$2–5/мес | text-embedding-3-small |
| Tavily Search API | бесплатный tier | 1000 запросов/мес |
| PostgreSQL (Docker) | $0 | локально |
| Telegram Bot API | $0 | бесплатный |

---

## Открытые задачи

### Блок 1: Технический долг (приоритет 2)

- Node.js в sandbox/Dockerfile — для качественных .docx
- FORMATTER-1: предагрегация данных 1C (отложено до реальных данных, см. ARCHITECTURE_DECISIONS.md)

---

## Фаза 3: Интеграции

- ~~Email MCP сервер~~ ✅ EMAIL-MCP + EMAIL-MCP-2 (server + bot integration complete)
- Google Calendar MCP — управление расписанием артели
- Документооборот — шаблоны путевых листов, актов, табелей

---

## Фаза 4: Инвестиции

- РВФ Казань: 8–10 апреля 2026 — ближайший дедлайн
- Гранты (ФСИ, Сколково): подавать параллельно с первым клиентом
- Seed (Kama Flow, Malina VC): после первого клиента с измеримыми метриками

---

## Критерии перехода к первому клиенту

- Agent Factory готов (Q-9.2–Q-9.5) ✅
- Docker Compose production готов ✅ (DOCKER-PROD)
- 2+ недели стабильной работы у 3 тестеров без критических багов ✅
