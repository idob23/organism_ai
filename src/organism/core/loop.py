import asyncio
import time
import uuid
import re
from datetime import datetime
from dataclasses import dataclass, field

from src.organism.core.evaluator import Evaluator
from src.organism.core.planner import PlanStep, Planner
from src.organism.llm.base import LLMProvider
from src.organism.logging.logger import Logger
from src.organism.logging.error_handler import get_logger, log_exception
from src.organism.core.context_budget import ContextBudget
from src.organism.memory.manager import MemoryManager
from src.organism.memory.knowledge_base import KnowledgeBase
from src.organism.memory.solution_cache import SolutionCache
from src.organism.memory.user_facts import format_for_prompt
from src.organism.memory.search_policy import SearchPolicy
from src.organism.self_improvement.prompt_versioning import PromptVersionControl
from src.organism.safety.validator import SafetyValidator
from src.organism.tools.registry import ToolRegistry

_log = get_logger("core.loop")

WRITE_KEYWORDS = [
    "napishi", "napihi",  # transliteration fallback
    "write", "draft", "compose",
    "\u043d\u0430\u043f\u0438\u0448\u0438",
    "\u043d\u0430\u043f\u0438\u0441\u0430\u0442\u044c",
    "\u0441\u043e\u0441\u0442\u0430\u0432\u044c",
    "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c",
    "\u043a\u043e\u043c\u043c\u0435\u0440\u0447\u0435\u0441\u043a\u043e\u0435 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u0435",
    "\u0448\u0430\u0431\u043b\u043e\u043d",
    "\u0441\u0442\u0430\u0442\u044c\u044e",
    "\u043f\u0438\u0441\u044c\u043c\u043e",
]

SEARCH_KEYWORDS = [
    "find", "search", "look up",
    "найди",           # найди
    "поищи",           # поищи
    "найти",           # найти
    "актуальные",  # актуальные
    "текущие",  # текущие
    "свежие",     # свежие
    "узнай",           # узнай
    "проверь",  # проверь
    "исследуй",  # исследуй
]

# FIX-1: Patterns for conversational (non-task) messages
CHAT_PATTERNS = [
    "\u043f\u0440\u0438\u0432\u0435\u0442",              # привет
    "\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439",  # здравствуй
    "\u0434\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e",  # доброе утро
    "\u0434\u043e\u0431\u0440\u044b\u0439 \u0434\u0435\u043d\u044c",  # добрый день
    "\u0434\u043e\u0431\u0440\u044b\u0439 \u0432\u0435\u0447\u0435\u0440",  # добрый вечер
    "\u043a\u0430\u043a \u0434\u0435\u043b\u0430",        # как дела
    "\u043a\u0442\u043e \u0442\u044b",                    # кто ты
    "\u0447\u0442\u043e \u0442\u044b \u0443\u043c\u0435\u0435\u0448\u044c",  # что ты умеешь
    "\u0447\u0442\u043e \u043c\u043e\u0436\u0435\u0448\u044c",  # что можешь
    "\u043f\u043e\u043c\u043e\u0433\u0438",              # помоги
    "\u0441\u043f\u0430\u0441\u0438\u0431\u043e",        # спасибо
    "\u043f\u043e\u043a\u0430",                          # пока
    "hello", "hi", "hey",
    "\u043f\u043e\u0447\u0435\u043c\u0443",              # почему
]

TASK_SIGNALS = [
    "\u043d\u0430\u043f\u0438\u0448\u0438",              # напиши
    "\u0441\u043e\u0441\u0442\u0430\u0432\u044c",        # составь
    "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439",  # рассчитай
    "\u043d\u0430\u0439\u0434\u0438",                    # найди
    "\u0441\u043e\u0437\u0434\u0430\u0439",              # создай
    "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c",  # подготовь
    "\u043f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439",  # проанализируй
    "\u0441\u0434\u0435\u043b\u0430\u0439",              # сделай
    "csv", "xlsx", "pptx",
]


@dataclass
class StepLog:
    step_id: int
    tool: str
    description: str
    output: str
    error: str
    success: bool
    duration: float
    attempts: int = 1
    quality_score: float = 0.0


@dataclass
class TaskResult:
    task_id: str
    task: str
    success: bool
    output: str
    answer: str = ""
    steps: list[StepLog] = field(default_factory=list)
    total_tokens: int = 0
    duration: float = 0.0
    error: str = ""
    memory_hits: int = 0
    quality_score: float = 0.0


def _is_writing_task(task: str) -> bool:
    t = task.lower()
    has_write = any(kw.lower() in t for kw in WRITE_KEYWORDS)
    if not has_write:
        return False
    # If task also contains search signals — let Planner handle it
    has_search = any(kw.lower() in t for kw in SEARCH_KEYWORDS)
    if has_search:
        return False
    return True


def _extract_filename(task: str) -> str | None:
    m = re.search(
        r"(\w[\w\-]+\.(?:md|txt|docx|html|csv|xlsx|json|pdf|pptx))",
        task,
        re.IGNORECASE
    )
    return m.group(1) if m else None


class CoreLoop:

    MAX_RETRIES = 3
    MAX_PLAN_STEPS = 5

    @staticmethod
    def _is_useful_output(output: str) -> bool:
        """Check if step output contains real content, not just an error/placeholder."""
        if not output or len(output.strip()) < 20:
            return False
        useless = ["domain blocked", "page not accessible", "http 403", "http 404",
                    "not found", "access denied", "timeout", "no results",
                    "use web_search instead"]
        lower = output.lower()
        return not any(u in lower for u in useless)

    @staticmethod
    def _is_raw_search_output(output: str) -> bool:
        """Detect if output looks like raw web_search results."""
        indicators = [
            "URL: http",
            "url: http",
            "\nAnswer:",
            "Answer: ",
        ]
        url_count = output.count("http://") + output.count("https://")
        has_indicators = any(ind in output for ind in indicators)
        return has_indicators and url_count >= 2

    async def _summarize_search_results(self, raw_output: str, task: str) -> str:
        """Summarize raw web_search output into a clean user-facing answer."""
        from src.organism.llm.base import Message

        today = datetime.now().strftime("%d.%m.%Y")

        prompt = (
            f"User task: {task}\n\n"
            f"Today's date: {today}\n\n"
            f"Raw search results:\n{raw_output[:3000]}\n\n"
            "Based on these search results, write a clear, structured answer in Russian. "
            "Include specific facts, numbers, dates, URLs where relevant. "
            "Format with markdown: use headers (##), bold (**), lists (-). "
            f"IMPORTANT: Today is {today}. If any deadlines or dates in the results have already passed, "
            "clearly mark them as expired (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: ~~\u0434\u043e 28 \u0438\u044e\u043b\u044f 2025~~ \u2014 \u0441\u0440\u043e\u043a \u0438\u0441\u0442\u0451\u043a). "
            "If the information might be outdated, add a note suggesting to check the official source for current dates. "
            "Do NOT present past deadlines as current opportunities. "
            "If information is incomplete or contradictory, note that. "
            "Do NOT include raw URLs as a list \u2014 weave them into the text naturally. "
            "Keep answer under 2000 characters."
        )

        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=prompt)],
                system="You are a research assistant. Summarize search results into clear, actionable answers in Russian.",
                model_tier="fast",  # Haiku — fast and cheap
                max_tokens=1000,
            )
            return resp.content.strip()
        except Exception:
            return raw_output  # fallback to raw if LLM fails

    @staticmethod
    def _humanize_error(output: str, task: str) -> str:
        """Convert raw error output to user-friendly message."""
        t = output.lower()
        if "403" in t or "not accessible" in t or "access denied" in t:
            return "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435 \u0441 \u0441\u0430\u0439\u0442\u0430 (\u0434\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043a\u0440\u044b\u0442). \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u0435\u0440\u0435\u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0437\u0430\u043f\u0440\u043e\u0441."
        if "404" in t or "not found" in t:
            return "\u0421\u0442\u0440\u0430\u043d\u0438\u0446\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0434\u0440\u0443\u0433\u043e\u0439 \u0437\u0430\u043f\u0440\u043e\u0441."
        if "timeout" in t:
            return "\u041f\u0440\u0435\u0432\u044b\u0448\u0435\u043d\u043e \u0432\u0440\u0435\u043c\u044f \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u044f. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0437\u0436\u0435."
        if "traceback" in t or "error:" in t:
            return "\u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430 \u043f\u0440\u0438 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0438. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u0435\u0440\u0435\u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0437\u0430\u043f\u0440\u043e\u0441."
        return output

    def __init__(self, llm: LLMProvider, registry: ToolRegistry, memory: MemoryManager | None = None, personality=None) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm)
        pvc = PromptVersionControl() if memory is not None else None
        self.evaluator = Evaluator(llm, pvc=pvc)
        self.validator = SafetyValidator()
        self.logger = Logger()
        self.cache = SolutionCache()
        self.knowledge_base = KnowledgeBase()
        self.context_budget = ContextBudget()
        self.personality = personality
        if memory is not None and memory.llm is None:
            memory.llm = llm
        self.memory = memory

    def _validate_plan(self, steps: list[PlanStep]) -> str | None:
        """Validate plan before execution. Returns error message or None if valid."""
        if not steps:
            return "Empty plan — no steps generated"

        if len(steps) > self.MAX_PLAN_STEPS:
            return f"Plan has {len(steps)} steps, maximum is {self.MAX_PLAN_STEPS}"

        available_tools = self.registry.list_all()
        step_ids = {s.id for s in steps}

        for step in steps:
            if step.tool not in available_tools:
                return f"Step {step.id}: tool '{step.tool}' not found. Available: {available_tools}"

            inp = step.input or {}

            if step.tool == "code_executor" and "code" not in inp:
                return f"Step {step.id}: code_executor requires 'code' in input"

            if step.tool == "web_search" and "query" not in inp:
                return f"Step {step.id}: web_search requires 'query' in input"

            if step.tool == "web_fetch" and "url" not in inp:
                return f"Step {step.id}: web_fetch requires 'url' in input"

            if step.tool == "text_writer" and "prompt" not in inp:
                return f"Step {step.id}: text_writer requires 'prompt' in input"

            if step.tool == "pptx_creator" and "topic" not in inp:
                return f"Step {step.id}: pptx_creator requires 'topic' in input"

            if step.tool == "duplicate_finder":
                # entities can be empty (will return error asking for data)
                continue

            if step.tool == "delegate_to_agent":
                if "peer_name" not in inp or "task" not in inp:
                    return f"Step {step.id}: delegate_to_agent requires 'peer_name' and 'task'"
                continue

            # MCP tools (mcp_*): input validation skipped — schema is dynamic.
            # Known-tool checks above won't match mcp_ prefix, so they pass through.

            for dep in step.depends_on:
                if dep not in step_ids:
                    return f"Step {step.id}: depends_on references non-existent step {dep}"
                if dep >= step.id:
                    return f"Step {step.id}: depends_on step {dep} which comes after (circular)"

        return None

    async def _run_writing_task(self, task_id: str, task: str, verbose: bool, user_context: str = "") -> "TaskResult | None":
        start = time.time()
        filename = _extract_filename(task) or "output.md"
        try:
            tool = self.registry.get("text_writer")
        except KeyError:
            return None

        if verbose:
            print("Writing task detected - using text_writer directly")
            print("Step 1: Generate and save text")

        tool_input: dict = {"prompt": task, "filename": filename}
        if user_context:
            tool_input["user_context"] = user_context
        result = await tool.execute(tool_input)
        duration = time.time() - start
        step_log = StepLog(step_id=1, tool="text_writer", description="Write and save text",
                           output=result.output, error=result.error,
                           success=result.exit_code == 0, duration=duration)
        if verbose:
            status = "OK" if result.exit_code == 0 else "FAIL"
            print(f"  [{status}] {duration:.1f}s | {result.output[:100]}")
            print(f"\n{'='*50}\nDone in {duration:.1f}s\n{'='*50}")

        return TaskResult(task_id=task_id, task=task, success=result.exit_code == 0,
                          output=result.output, answer=result.output,
                          steps=[step_log], duration=duration,
                          error=result.error if result.exit_code != 0 else "")

    def _is_conversational(self, task: str) -> bool:
        """Detect if message is conversational (not a task)."""
        t = task.lower().strip()

        # Messages with task signals are always tasks
        if any(s in t for s in TASK_SIGNALS):
            return False

        # Direct chat pattern match
        if any(t.startswith(p) or t == p for p in CHAT_PATTERNS):
            return True

        # Very short messages (< 50 chars) without task keywords are conversational
        if len(t) < 50 and not t.startswith("/"):
            return True

        return False

    async def _handle_conversation(self, task_id: str, task: str, user_context: str = "") -> "TaskResult":
        """Handle conversational messages with a direct LLM response (no planning, no files)."""
        start = time.time()

        today = datetime.now().strftime("%d.%m.%Y")
        system = (
            f"You are Organism AI \u2014 a smart personal assistant. Today is {today}. "
            "You communicate naturally, like a knowledgeable colleague. "
            "You adapt to the user: learn their context, remember preferences, and become more useful over time. "
            "Your capabilities: calculations, document generation, web search, data analysis, "
            "presentations, working with databases and external systems. "
            "When needed, you can coordinate multiple AI agents for complex tasks. "
            "You speak the same language as the user (Russian if they write in Russian). "
            "Be friendly, professional, and concise. Do NOT assume user's role, company, or industry "
            "unless they told you. Do NOT mention artels, mining, or specific companies unless asked. "
            "On first greeting, briefly introduce yourself and ask how you can help. "
            "Keep responses under 500 characters for greetings, under 1000 for explanations."
        )
        if user_context:
            system += f"\n\nUser context: {user_context}"

        try:
            from src.organism.llm.base import Message as LLMMessage
            resp = await self.llm.complete(
                messages=[LLMMessage(role="user", content=task)],
                system=system,
                model_tier="fast",
                max_tokens=500,
            )
            answer = resp.content.strip()
        except Exception as e:
            # FIX-10: Monitor conversation handler failures
            try:
                from src.organism.monitoring.error_notifier import capture_error
                asyncio.ensure_future(capture_error(
                    component="core.loop.conversation",
                    message=f"Conversation handler failed: {e}",
                    exception=e,
                    task_text=task[:500],
                ))
            except Exception:
                pass
            answer = "\u041f\u0440\u0438\u0432\u0435\u0442! \u042f Organism AI. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043c\u043d\u0435 \u0437\u0430\u0434\u0430\u0447\u0443 \u0438 \u044f \u043f\u043e\u043c\u043e\u0433\u0443."

        duration = time.time() - start
        _log.info(f"[{task_id}] Conversational response in {duration:.1f}s")
        return TaskResult(
            task_id=task_id, task=task, success=True,
            output=answer, answer=answer,
            duration=duration, quality_score=1.0,
        )

    async def run(self, task: str, verbose: bool = True, user_id: str = "default") -> "TaskResult":
        task_id = uuid.uuid4().hex[:8]
        start = time.time()
        _log.info(f"[{task_id}] Task started: {task[:100]}")
        self.logger.log_task_start(task_id, task)

        if verbose:
            print(f"\n{'='*50}\nTask [{task_id}]: {task}\n{'='*50}")

        memory_hits = 0
        memory_context = ""
        user_context = ""

        # FIX-1: Detect conversational messages (not tasks) — fast path, skip memory/planning
        if self._is_conversational(task):
            return await self._handle_conversation(task_id, task)

        if self.memory:
            try:
                await self.memory.initialize()
                similar = await self.memory.on_task_start(task)
                if similar:
                    memory_hits = len(similar)
                    if verbose:
                        print(f"Memory: found {memory_hits} similar past task(s)")
                    lines = []
                    for s in similar:
                        tools = s.get("tools_used") or []
                        tool_str = ", ".join(tools) if tools else "unknown"
                        task_str = s.get("task", "")[:70]
                        result_str = (s.get("result") or "")[:80].replace("\n", " ")
                        line = f"- [{tool_str}] {task_str}"
                        if result_str:
                            line += f" -> {result_str}"
                        lines.append(line)
                    memory_context = "\n".join(lines)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory lookup failed", e)
            try:
                user_facts = await self.memory.facts.get_all_facts(user_id=user_id)
                user_context = format_for_prompt(user_facts)
                if user_context and verbose:
                    print(f"User context: {user_context}")
            except Exception:
                pass

        if self.personality:
            personality_addition = self.personality.get_system_prompt_addition()
            if personality_addition:
                user_context = user_context + personality_addition

        # Q-7.3: Inject few-shot examples into planner context
        if self.memory:
            try:
                _fs_examples = await self.memory.few_shot.get_examples(task)
                _fs_section = self.memory.few_shot.format_for_prompt(_fs_examples)
                if _fs_section:
                    user_context = user_context + "\n" + _fs_section if user_context else _fs_section
                    if verbose:
                        print(f"Few-shot: {len(_fs_examples)} examples injected")
            except Exception:
                pass

        # L1 Solution Cache — check before planning/fast-path
        cache_hash: str | None = None
        canonical_task: str | None = None
        if self.memory:
            try:
                canonical_task = await self.cache.normalize_task(task, self.llm)
                cache_hash = self.cache.hash_task(canonical_task)
                cached = await self.cache.get(cache_hash)
                if cached:
                    if verbose:
                        print(f"Cache HIT (quality={cached['quality_score']:.2f}, hits={cached['hits']})")
                    _log.info(f"[{task_id}] Cache HIT hash={cache_hash[:8]} quality={cached['quality_score']:.2f}")
                    return TaskResult(
                        task_id=task_id, task=task, success=True,
                        output=cached["result"], answer=cached["result"],
                        duration=time.time() - start, memory_hits=memory_hits,
                        quality_score=cached["quality_score"],
                    )
            except Exception as e:
                log_exception(_log, f"[{task_id}] Cache check failed", e)

        # Fast path for writing tasks
        # If intent is retrieval-oriented (temporal/causal/entity) and we have
        # memory context, skip fast path so the planner can answer from memory
        # instead of generating new content via text_writer.
        _skip_fast_path = False
        if memory_context and self.memory:
            _intent = SearchPolicy().classify_intent(task)
            if _intent in ("temporal", "causal", "entity"):
                _skip_fast_path = True
                _log.info(f"[{task_id}] Intent={_intent} + memory -> skip writing fast path")
                if verbose:
                    print(f"Intent: {_intent} + memory context -> skip fast path, let planner decide")

        if not _skip_fast_path and _is_writing_task(task):
            try:
                result = await self._run_writing_task(task_id, task, verbose, user_context)
                if result is not None:
                    result.memory_hits = memory_hits
                    _log.info(f"[{task_id}] Writing task {'SUCCESS' if result.success else 'FAILED'} in {result.duration:.1f}s")
                    if self.memory and result.success:
                        try:
                            await self.memory.on_task_end(task, result.output, True, result.duration, 1, ["text_writer"], quality_score=0.8, user_id=user_id)
                        except Exception:
                            pass
                        # Q-7.3: Save as few-shot example
                        try:
                            await self.memory.few_shot.save_example(
                                task_text=task, task_type="writing",
                                plan_steps=[{"tool": "text_writer", "description": "Write and save text"}],
                                quality_score=0.8, tools_used=["text_writer"],
                            )
                        except Exception:
                            pass
                        if cache_hash and canonical_task:
                            try:
                                await self.cache.put(cache_hash, canonical_task, task, result.output, 0.8)
                            except Exception:
                                pass
                    return result
            except Exception as e:
                log_exception(_log, f"[{task_id}] Writing fast path failed", e)
                # MON-1: Capture to ErrorLog for Telegram monitoring
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    asyncio.ensure_future(capture_error(
                        component="core.loop.writing", message=f"Writing fast path failed: {e}",
                        exception=e, task_id=task_id, task_text=task[:500],
                    ))
                except Exception:
                    pass

        if verbose:
            print("Planning...")

        knowledge_rules: list[str] = []
        if self.memory:
            try:
                knowledge_rules = await self.knowledge_base.get_rules()
            except Exception:
                pass

        # Q-5.4: Template hint — look up matching procedural template before planning
        template_hint = ""
        if self.memory:
            try:
                tmpl = await self.memory.templates.find_template(task)
                if tmpl:
                    hint_parts = [
                        f"Reusable template '{tmpl['pattern_name']}'"
                        f" (quality={tmpl['avg_quality']:.2f}, used {tmpl['success_count']}x):",
                        f"  Tools: {tmpl['tools_sequence']}",
                    ]
                    if tmpl.get("code_template"):
                        hint_parts.append(f"  Code skeleton: {tmpl['code_template'][:300]}")
                    template_hint = "\n".join(hint_parts)
                    if verbose:
                        print(f"Template hint: {tmpl['pattern_name']}")
            except Exception:
                pass

        # Build context-budgeted prompt (trims to ~3000 token sweet spot)
        _, task_context = self.context_budget.build_prompt(
            system="",  # system is chosen per task type inside Planner
            knowledge_rules=knowledge_rules,
            memory_context=memory_context,
            task=task,
        )
        if template_hint:
            task_context = f"{task_context}\n\n{template_hint}"
        if verbose:
            u = self.context_budget.last_usage
            _log.info(
                f"[{task_id}] Context budget: {u['total']}/{self.context_budget.budget_tokens}t "
                f"(task={u['task']} rules={u['rules']} memory={u['memory']})"
            )
            print(
                f"Context: {u['total']}/{self.context_budget.budget_tokens} tokens "
                f"| task={u['task']} rules={u['rules']} memory={u['memory']}"
            )

        # For temporal queries with memory hits, override task type to "writing" so
        # the planner uses text_writer to synthesize the answer from memory context
        # instead of going to web_search (which cannot answer internal-state questions).
        task_type_hint = None
        if memory_hits > 0:
            _mem_intent = SearchPolicy().classify_intent(task)
            if _mem_intent == "temporal":
                task_type_hint = "writing"
                _log.info(f"[{task_id}] Temporal query + memory hits -> task_type_hint=writing")

        try:
            steps = await self.planner.plan(task, task_context=task_context, user_context=user_context, task_type_hint=task_type_hint)
            _log.info(f"[{task_id}] Plan created: {len(steps)} steps  {[s.tool for s in steps]}")
        except Exception as e:
            log_exception(_log, f"[{task_id}] Planning failed", e)
            # MON-1: Capture to ErrorLog for Telegram monitoring
            try:
                from src.organism.monitoring.error_notifier import capture_error
                asyncio.ensure_future(capture_error(
                    component="core.loop", message=f"Planning failed: {e}",
                    exception=e, task_id=task_id, task_text=task[:500],
                ))
            except Exception:
                pass
            return TaskResult(task_id=task_id, task=task, success=False, output="",
                              error=f"Planning failed: {e}", duration=time.time() - start, memory_hits=memory_hits)

        # Validate plan before execution
        validation_error = self._validate_plan(steps)
        if validation_error:
            _log.warning(f"[{task_id}] Plan validation failed: {validation_error}, re-planning...")
            if verbose:
                print(f"  Plan invalid: {validation_error}")
                print("  Re-planning with generic prompt...")
            try:
                avail = self.registry.list_all()
                replan_hint = f"\nIMPORTANT: Only use these tools: {avail}. Do NOT use any other tools."
                steps = await self.planner._fast_plan(task + replan_hint)
                if not steps:
                    steps = await self.planner._react_plan(task + replan_hint)
                validation_error = self._validate_plan(steps)
                if validation_error:
                    return TaskResult(task_id=task_id, task=task, success=False, output="",
                                      error=f"Plan validation failed after retry: {validation_error}",
                                      duration=time.time() - start, memory_hits=memory_hits)
                _log.info(f"[{task_id}] Re-plan created: {len(steps)} steps  {[s.tool for s in steps]}")
            except Exception as e:
                log_exception(_log, f"[{task_id}] Re-planning failed", e)
                return TaskResult(task_id=task_id, task=task, success=False, output="",
                                  error=f"Re-planning failed: {e}", duration=time.time() - start, memory_hits=memory_hits)

        if verbose:
            print(f"Plan: {len(steps)} step(s)")
            for s in steps:
                print(f"  {s.id}. [{s.tool}] {s.description}")

        step_logs: list[StepLog] = []
        step_outputs: dict[int, str] = {}
        last_output = ""
        tools_used: list[str] = []
        total_tokens = 0

        for step in steps:
            resolved_input = {}
            for k, v in step.input.items():
                if isinstance(v, str):
                    v = re.sub(r"\{\{step_(\d+)_output\}\}",
                               lambda m: step_outputs.get(int(m.group(1)), ""), v)
                resolved_input[k] = v

            resolved_step = PlanStep(id=step.id, tool=step.tool, description=step.description,
                                     input=resolved_input, depends_on=step.depends_on)
            log = await self._execute_step(task_id, task, resolved_step, verbose)
            step_logs.append(log)

            if log.success:
                step_outputs[step.id] = log.output
                last_output = log.output
                if step.tool not in tools_used:
                    tools_used.append(step.tool)
            else:
                # Soft-fail continuation: web_fetch connection/block issues are non-fatal
                # when a previous step already produced useful output.
                # FIX-6: check both output and error (blocked/HTTP msgs now in error field)
                _soft_msg = log.output or log.error or ""
                _soft_webfetch = (
                    step.tool == "web_fetch"
                    and last_output
                    and _soft_msg
                    and any(kw in _soft_msg for kw in (
                        "Use web_search instead",
                        "not accessible",
                        "Domain blocked",
                        "Cannot connect",
                    ))
                )
                if _soft_webfetch:
                    _log.warning(f"[{task_id}] web_fetch soft-fail at step {step.id} -- continuing with previous output")
                    step_outputs[step.id] = last_output  # pass prior output downstream
                    continue

                duration = time.time() - start

                # FIX-5: If previous steps succeeded, return their results instead of error
                successful_outputs = [sl.output for sl in step_logs if sl.success and sl.output]
                if successful_outputs:
                    _final_output = max(successful_outputs, key=len)
                    # FIX-7: Summarize raw search results before returning
                    if self._is_raw_search_output(_final_output):
                        _final_output = await self._summarize_search_results(_final_output, task)
                    _log.info(f"[{task_id}] Step {step.id} failed but {len(successful_outputs)} previous step(s) succeeded — returning partial results")
                    if self.memory:
                        try:
                            await self.memory.on_task_end(task, _final_output, True, duration, len(step_logs), tools_used, quality_score=0.5, user_id=user_id)
                        except Exception:
                            pass
                    self.logger.log_task_end(task_id, True, duration, total_tokens)
                    return TaskResult(task_id=task_id, task=task, success=True, output=_final_output,
                                      answer=_final_output, steps=step_logs, duration=duration,
                                      memory_hits=memory_hits, quality_score=0.5)

                # All steps failed — return humanized error
                _log.error(f"[{task_id}] Task FAILED at step {step.id}: {log.error}")
                if self.memory:
                    try:
                        await self.memory.on_task_end(task, last_output, False, duration, len(step_logs), tools_used, quality_score=0.2, user_id=user_id)
                    except Exception:
                        pass
                self.logger.log_task_end(task_id, False, duration, total_tokens)
                _final_output = self._humanize_error(log.output or log.error, task)
                # FIX-10: Monitor failed task
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    asyncio.ensure_future(capture_error(
                        component="core.loop",
                        message=f"Task FAILED (quality: 0.00)\nOutput: {_final_output[:300]}",
                        task_id=task_id,
                        task_text=task[:500],
                        level="ERROR",
                    ))
                except Exception:
                    pass
                return TaskResult(task_id=task_id, task=task, success=False, output=_final_output,
                                  steps=step_logs, duration=duration,
                                  error=f"Step {step.id} failed: {log.error}", memory_hits=memory_hits)

        duration = time.time() - start
        _log.info(f"[{task_id}] Task SUCCESS in {duration:.1f}s, tools: {tools_used}")

        # Calculate average quality score
        avg_quality = sum(s.quality_score for s in step_logs if s.success) / max(len([s for s in step_logs if s.success]), 1)

        if self.memory:
            try:
                await self.memory.on_task_end(task, last_output, True, duration, len(step_logs), tools_used, quality_score=avg_quality, user_id=user_id)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory save failed", e)
            # Q-7.3: Save as few-shot example if high quality
            try:
                _plan_dicts = [{"tool": s.tool, "description": s.description} for s in steps]
                await self.memory.few_shot.save_example(
                    task_text=task,
                    task_type=task_type_hint or "mixed",
                    plan_steps=_plan_dicts,
                    quality_score=avg_quality,
                    tools_used=tools_used,
                )
            except Exception:
                pass
            if cache_hash and canonical_task and avg_quality >= self.cache.MIN_QUALITY:
                try:
                    await self.cache.put(cache_hash, canonical_task, task, last_output, avg_quality)
                    _log.info(f"[{task_id}] Cache stored hash={cache_hash[:8]} quality={avg_quality:.2f}")
                except Exception as e:
                    log_exception(_log, f"[{task_id}] Cache store failed", e)

        self.logger.log_task_end(task_id, True, duration, total_tokens)

        if verbose:
            print(f"\n{'='*50}\nDone in {duration:.1f}s | Quality: {avg_quality:.2f} | Memory hits: {memory_hits}\n{'='*50}")

        # FIX-6: Prefer useful output — skip placeholders/error stubs from last step
        if not self._is_useful_output(last_output):
            useful = [s.output for s in step_logs if s.success and self._is_useful_output(s.output)]
            if useful:
                last_output = max(useful, key=len)
        # FIX-7: Summarize raw search results into clean Russian answer
        if self._is_raw_search_output(last_output):
            last_output = await self._summarize_search_results(last_output, task)
        # FIX-4: Humanize output in case a "successful" step returned raw error text
        _final_output = self._humanize_error(last_output, task)
        # FIX-10: Monitor low-quality tasks
        if avg_quality < 0.5:
            try:
                from src.organism.monitoring.error_notifier import capture_error
                asyncio.ensure_future(capture_error(
                    component="core.loop",
                    message=f"Task LOW QUALITY (quality: {avg_quality:.2f})\nOutput: {_final_output[:300]}",
                    task_id=task_id,
                    task_text=task[:500],
                    level="WARNING",
                ))
            except Exception:
                pass
        return TaskResult(task_id=task_id, task=task, success=True, output=_final_output, answer=_final_output,
                          steps=step_logs, total_tokens=total_tokens, duration=duration, memory_hits=memory_hits,
                          quality_score=avg_quality)

    async def _execute_step(self, task_id: str, task: str, step: PlanStep, verbose: bool) -> StepLog:
        _log.info(f"[{task_id}] Step {step.id} start: [{step.tool}] {step.description[:80]}")
        if verbose:
            print(f"\nStep {step.id}: {step.description}")

        if step.tool == "code_executor":
            code = step.input.get("code", "")
            val = self.validator.validate_code(code)
            if not val.allowed:
                return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                               output="", error=f"Safety block: {val.reason}", success=False, duration=0.0)

        try:
            tool = self.registry.get(step.tool)
        except KeyError:
            error = f"Tool '{step.tool}' not found in registry. Available: {self.registry.list_all()}"
            _log.error(f"[{task_id}] {error}")
            return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                           output="", error=error, success=False, duration=0.0)

        step_input = dict(step.input)
        result = None
        eval_result = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            step_start = time.time()
            if verbose and attempt > 1:
                print(f"  Retry {attempt}/{self.MAX_RETRIES}...")

            # FIX-10: Monitor step retries
            if attempt > 1:
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    _prev_error = result.error[:200] if result and result.error else "unknown"
                    asyncio.ensure_future(capture_error(
                        component=f"core.loop.step.{step.tool}",
                        message=f"Step {step.id} retry {attempt}/{self.MAX_RETRIES}: {_prev_error}",
                        task_id=task_id,
                        task_text=task[:300],
                        level="WARNING",
                    ))
                except Exception:
                    pass

            try:
                result = await tool.execute(step_input)
            except Exception as e:
                error = log_exception(_log, f"[{task_id}] Step {step.id} crashed", e)
                # MON-1: Capture to ErrorLog for Telegram monitoring
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    asyncio.ensure_future(capture_error(
                        component=f"core.loop.{step.tool}", message=f"Step {step.id} crashed: {e}",
                        exception=e, task_id=task_id, task_text=task[:500],
                    ))
                except Exception:
                    pass
                return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                               output="", error=error, success=False,
                               duration=time.time() - step_start, attempts=attempt)

            duration = time.time() - step_start
            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {duration:.1f}s | output: {result.output[:80] if result.output else '(empty)'}")
                if result.error:
                    print(f"  Error: {result.error[:120]}")

            if not result.success:
                _log.warning(f"[{task_id}] Step {step.id} attempt {attempt} failed: {result.error[:200]}")

            try:
                eval_result = await self.evaluator.evaluate(task=task, step_description=step.description, result=result)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Evaluator crashed", e)
                from src.organism.core.evaluator import EvalResult
                eval_result = EvalResult(success=result.exit_code == 0, reason="Evaluator unavailable")

            self.logger.log_step(task_id, step.id, step.tool, eval_result.success, duration, error=result.error)

            if eval_result.success:
                 _log.info(f"[{task_id}] Step {step.id} SUCCESS on attempt {attempt} (quality: {eval_result.quality_score:.2f})")
                 return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                                output=result.output, error="", success=True,
                                duration=duration, attempts=attempt,
                                quality_score=eval_result.quality_score)

            if eval_result.retry_hint and step.tool == "code_executor":
                step_input["code"] = f"# Previous failed: {eval_result.retry_hint}\n{step_input.get('code', '')}"

            if verbose:
                print(f"  Eval: {eval_result.reason}")

        _log.error(f"[{task_id}] Step {step.id} FAILED after {self.MAX_RETRIES} attempts")
        return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                       output=result.output if result else "",
                       error=eval_result.reason if eval_result else "Max retries exceeded",
                       success=False, duration=duration, attempts=self.MAX_RETRIES)
