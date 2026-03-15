import asyncio
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field

from src.organism.core.evaluator import Evaluator
from src.organism.llm.base import LLMProvider
from src.organism.logging.logger import Logger
from src.organism.logging.error_handler import get_logger, log_exception
from src.organism.memory.manager import MemoryManager
from src.organism.memory.user_facts import format_for_prompt
from src.organism.self_improvement.prompt_versioning import PromptVersionControl
from src.organism.core.skill_matcher import SkillMatcher
from src.organism.safety.validator import SafetyValidator
from src.organism.tools.registry import ToolRegistry

_log = get_logger("core.loop")

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


class CoreLoop:

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

    def __init__(self, llm: LLMProvider, registry: ToolRegistry, memory: MemoryManager | None = None, personality=None, scheduler=None, orchestrator=None, factory=None) -> None:
        self.llm = llm
        self.registry = registry
        pvc = PromptVersionControl() if memory is not None else None
        self.evaluator = Evaluator(llm, pvc=pvc)
        self.validator = SafetyValidator()
        self.logger = Logger()
        self.skill_matcher = SkillMatcher(llm)
        self.personality = personality
        self.scheduler = scheduler
        self._orchestrator = orchestrator
        self.factory = factory
        if memory is not None and memory.llm is None:
            memory.llm = llm
        self.memory = memory
        # FIX-53: inject memory into memory_search tool if registered
        try:
            mem_tool = registry.get("memory_search")
            mem_tool.set_memory(memory)
        except KeyError:
            pass

    def _build_tool_definitions(self) -> list[dict]:
        """Build Anthropic-format tool definitions from registry."""
        try:
            return self.registry.to_json_schema()
        except Exception:
            return []

    async def _handle_conversation(
        self, task_id: str, task: str,
        user_context: str = "",
        memory_context: str = "",
        user_id: str = "default",
        media: list | None = None,
        extra_system_context: str = "",
    ) -> "TaskResult":
        """Q-10.4: Primary execution path — LLM with tools.

        LLM receives message + tools, decides itself whether to answer
        directly or execute tools. No mode switching, no routing.
        """
        from src.organism.llm.base import Message as LLMMessage

        start = time.time()
        today = datetime.now().strftime("%d.%m.%Y")

        # --- Build context ---
        # If memory_context was passed from run(), use it; otherwise fetch here (media path)
        longterm_context = ""
        if memory_context:
            longterm_context = (
                "\u041f\u0430\u043c\u044f\u0442\u044c: \u043d\u0430\u0448\u043b\u0438 "
                "\u043f\u0440\u043e\u0448\u043b\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438:\n"
                + memory_context
            )
        elif self.memory:
            try:
                mem_result = await self.memory.on_task_start(task)
                if mem_result and isinstance(mem_result, list) and len(mem_result) > 0:
                    snippets = []
                    for t_item in mem_result[:3]:
                        snippets.append(
                            f"- {t_item.get('task', '')[:100]}: {t_item.get('result', '')[:200]}"
                        )
                    if snippets:
                        longterm_context = (
                            "\u041f\u0430\u043c\u044f\u0442\u044c: \u043d\u0430\u0448\u043b\u0438 "
                            "\u043f\u0440\u043e\u0448\u043b\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438:\n"
                            + "\n".join(snippets)
                        )
            except Exception:
                pass

        # FIX-34: Recent work context — agent always knows what it just did
        recent_work_context = ""
        if self.memory:
            try:
                recent_tasks = await self.memory.get_recent_tasks(limit=3)
                if recent_tasks:
                    lines = []
                    for t in recent_tasks:
                        result_preview = (t.get("result") or "")[:300].replace("\n", " ")
                        lines.append(
                            f"- \u0417\u0430\u0434\u0430\u0447\u0430: {t.get('task', '')[:100]}\n"
                            f"  \u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442: {result_preview}"
                        )
                    recent_work_context = (
                        "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 "
                        "\u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043d\u044b\u0435 "
                        "\u0437\u0430\u0434\u0430\u0447\u0438:\n" + "\n".join(lines)
                    )
            except Exception:
                pass

        # SKILL-1: Technical skill context
        skill_context = ""
        try:
            skill_context = await self.skill_matcher.get_skill_context(task)
        except Exception:
            pass

        # Chat history
        chat_history_messages: list[LLMMessage] = []
        if self.memory:
            try:
                recent = await self.memory.chat_history.get_recent(user_id, limit=10)
                for msg in recent[-10:]:
                    chat_history_messages.append(
                        LLMMessage(role=msg["role"], content=msg["content"][:500])
                    )
            except Exception:
                pass

        # System prompt
        system_parts = [
            "You are Organism AI \u2014 an autonomous AI assistant with access to tools. "
            "You can answer questions directly OR use tools to take real actions. "
            f"Today: {today}. "
            "\u0422\u044b \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0448\u044c: \u0442\u0435\u043a\u0441\u0442, \u0433\u043e\u043b\u043e\u0441\u043e\u0432\u044b\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u044f (\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e\u0442\u0441\u044f), "
            "\u0444\u043e\u0442\u043e, PDF, \u0432\u0438\u0434\u0435\u043e (\u0447\u0435\u0440\u0435\u0437 Vision API).",
            "\n## How you communicate",
            "- Be direct and honest, match the user's tone",
            "- When you have relevant knowledge, share it fully",
            "- If a user asks you to do something and you have the right tool, use it",
            "- Respond in the same language as the user",
            "- FORMATTING: Never use Markdown. No ##, no ---, no |tables|, no **bold**, no ```code blocks``` in text responses. "
            "Use plain text only. Structure with line breaks and emoji if needed. "
            "Exception: when creating actual files (Excel, Word, PDF) \u2014 formatting inside files is fine.",
            "- FILE FORMATS: \u0414\u0435\u043b\u043e\u0432\u044b\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b (\u043f\u0438\u0441\u044c\u043c\u0430, \u041a\u041f, \u0430\u043a\u0442\u044b, \u043f\u0430\u043c\u044f\u0442\u043a\u0438, \u043e\u0442\u0447\u0451\u0442\u044b, \u0441\u043f\u0440\u0430\u0432\u043a\u0438) "
            "\u0441\u043e\u0437\u0434\u0430\u0432\u0430\u0439 \u0432 PDF. \u0422\u0430\u0431\u043b\u0438\u0446\u044b \u0441 \u0434\u0430\u043d\u043d\u044b\u043c\u0438 \u2014 \u0432 Excel. \u041a\u043e\u0434 \u2014 \u0432 .py. "
            "Markdown (.md) \u0442\u043e\u043b\u044c\u043a\u043e \u0435\u0441\u043b\u0438 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u044f\u0432\u043d\u043e \u043f\u0440\u043e\u0441\u0438\u0442.",
            "\n## Epistemic honesty",
            "\u0422\u044b \u0437\u043d\u0430\u0435\u0448\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0442\u043e, \u0447\u0442\u043e \u0440\u0435\u0430\u043b\u044c\u043d\u043e \u0432\u0438\u0434\u0435\u043b: \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b \u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u043e\u0432, \u0438\u0441\u0442\u043e\u0440\u0438\u044e \u0447\u0430\u0442\u0430, "
            "\u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f. \u0415\u0441\u043b\u0438 \u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442 \u0432\u0435\u0440\u043d\u0443\u043b \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u2014 \u043e\u043f\u0438\u0441\u044b\u0432\u0430\u0439 \u0438\u043c\u0435\u043d\u043d\u043e \u0435\u0433\u043e, "
            "\u0434\u0430\u0436\u0435 \u0435\u0441\u043b\u0438 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u043d\u0435\u043e\u0436\u0438\u0434\u0430\u043d\u043d\u044b\u0439. \u041d\u0438\u043a\u043e\u0433\u0434\u0430 \u043d\u0435 \u043e\u0431\u044a\u044f\u0441\u043d\u044f\u0439 \u043d\u0435\u0443\u0434\u0430\u0447\u0443 \u043f\u0440\u0438\u0447\u0438\u043d\u0430\u043c\u0438, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 "
            "\u0442\u044b \u043d\u0435 \u043d\u0430\u0431\u043b\u044e\u0434\u0430\u043b \u0432 \u044d\u0442\u043e\u043c \u0440\u0430\u0437\u0433\u043e\u0432\u043e\u0440\u0435. \u041f\u0440\u0438\u043c\u0435\u0440 \u0447\u0435\u0441\u0442\u043d\u043e\u0433\u043e \u043e\u0442\u0432\u0435\u0442\u0430: '\u041e\u0442\u043a\u0440\u044b\u043b \u0444\u0430\u0439\u043b \u2014 \u044d\u0442\u043e "
            "HTML-\u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0430 \u0431\u0435\u0437 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f, \u043f\u0440\u0438\u0448\u043b\u0438 JPG \u0438\u043b\u0438 PNG.' \u041f\u0440\u0438\u043c\u0435\u0440 \u043d\u0435\u0447\u0435\u0441\u0442\u043d\u043e\u0433\u043e: "
            "'\u0424\u0430\u0439\u043b \u043d\u0435 \u043f\u0440\u0438\u043a\u0440\u0435\u043f\u0438\u043b\u0441\u044f' \u2014 \u0435\u0441\u043b\u0438 \u0442\u044b \u0435\u0433\u043e \u0440\u0435\u0430\u043b\u044c\u043d\u043e \u043f\u043e\u043b\u0443\u0447\u0438\u043b \u0438 \u0447\u0438\u0442\u0430\u043b.",
        ]
        if skill_context:
            system_parts.append(f"\n## How to create this file\n{skill_context}")
        if user_context:
            system_parts.append(f"\n{user_context}")
        if recent_work_context:
            system_parts.append(f"\n{recent_work_context}")
        if longterm_context:
            system_parts.append(f"\n{longterm_context}")
        # FIX-63: Extra system context from MetaOrchestrator (agent personality)
        if extra_system_context:
            system_parts.append(f"\n{extra_system_context}")
        system = "\n".join(system_parts)

        # --- Build messages ---
        if media:
            content_blocks = []
            for m in media:
                if m.get("type") == "image" or m.get("data"):
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": m.get("media_type", "image/jpeg"),
                            "data": m["data"],
                        }
                    })
            if task:
                content_blocks.append({"type": "text", "text": task})
            current_message = LLMMessage(role="user", content=content_blocks)
        else:
            current_message = LLMMessage(role="user", content=task)

        messages = chat_history_messages + [current_message]

        # --- Tool definitions ---
        tool_defs = self._build_tool_definitions()

        # --- First LLM call ---
        try:
            if tool_defs:
                response = await self.llm.complete_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    system=system,
                    model_tier="balanced",
                    max_tokens=8192,
                )
            else:
                response = await self.llm.complete(
                    messages=messages,
                    system=system,
                    model_tier="balanced",
                    max_tokens=8192,
                )
        except Exception as e:
            log_exception(_log, f"[{task_id}] Conversation LLM call failed", e)
            answer = "\u041f\u0440\u043e\u0438\u0437\u043e\u0448\u043b\u0430 \u043e\u0448\u0438\u0431\u043a\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437."
            return TaskResult(task_id=task_id, task=task, success=False,
                              output=answer, answer=answer,
                              duration=time.time() - start)

        # --- Handle tool calls (max 7 rounds) ---
        MAX_TOOL_ROUNDS = 10
        round_count = 0
        all_tool_calls: list[dict] = []
        created_files: list[str] = []  # FIX-36: track files for gateway delivery

        while response.has_tool_calls and round_count < MAX_TOOL_ROUNDS:
            round_count += 1

            # Execute each tool call
            tool_results_content = []
            assistant_content = []

            if response.content:
                assistant_content.append({"type": "text", "text": response.content})

            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_input = tc.get("input", {})
                tool_use_id = tc.get("id", "")

                all_tool_calls.append(tc)

                assistant_content.append({
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": tool_name,
                    "input": tool_input,
                })

                _log.info(f"[{task_id}] Tool call: {tool_name}({str(tool_input)[:80]})")

                tool_output = ""
                try:
                    tool = self.registry.get(tool_name)
                    result = await tool.execute(tool_input)
                    tool_output = result.output if result.exit_code == 0 else f"Error: {result.error}"
                    # FIX-52: Log tool result at WARNING to ensure visibility
                    _log.warning("[%s] Tool result: %s exit=%s out=%s err=%s",
                        task_id, tool_name,
                        getattr(result, "exit_code", "?"),
                        (result.output or "")[:200],
                        (result.error or "")[:200],
                    )
                except Exception as e:
                    tool_output = f"Tool error: {e}"

                # FIX-36: Track created files for gateway delivery
                import re as _re
                _saved_match = _re.search(r'Saved files:\s*(\S+)', tool_output)
                if _saved_match:
                    created_files.append(_saved_match.group(1).strip())

                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": tool_output[:15000],
                })

            # Continue conversation with tool results
            messages = messages + [
                LLMMessage(role="assistant", content=assistant_content),
                LLMMessage(role="user", content=tool_results_content),
            ]

            try:
                response = await self.llm.complete_with_tools(
                    messages=messages,
                    tools=tool_defs,
                    system=system,
                    model_tier="balanced",
                    max_tokens=8192,
                )
            except Exception as e:
                log_exception(_log, f"[{task_id}] Tool round {round_count} failed", e)
                break

        # FIX-55: Detect exhausted tool rounds with no result
        exhausted = round_count >= MAX_TOOL_ROUNDS and not created_files

        answer = response.content.strip() if response.content else \
            "\u0413\u043e\u0442\u043e\u0432\u043e."

        if exhausted:
            answer = (
                f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c "
                f"\u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c "
                f"\u0437\u0430\u0434\u0430\u0447\u0443 \u0437\u0430 "
                f"{MAX_TOOL_ROUNDS} "
                f"\u043f\u043e\u043f\u044b\u0442\u043e\u043a. "
                f"\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 "
                f"\u0435\u0449\u0451 \u0440\u0430\u0437 \u0438\u043b\u0438 "
                f"\u0443\u0442\u043e\u0447\u043d\u0438\u0442\u0435 "
                f"\u0437\u0430\u0434\u0430\u0447\u0443."
            )

        # FIX-36: Append file marker so gateway can detect and send the file
        if created_files:
            answer = answer + f"\nSaved files: {created_files[-1]}"

        duration = time.time() - start
        success = not exhausted
        _log.info(f"[{task_id}] Handler: {round_count} tool rounds, {duration:.1f}s, success={success}")

        # ARCH-1.1: Evaluate quality via Evaluator (not binary)
        quality_score = 0.8 if success else 0.2  # fallback default
        try:
            from src.organism.tools.base import ToolResult as _TR
            _eval_tr = _TR(
                output=answer,
                exit_code=0 if success else 1,
                error="" if success else "exhausted tool rounds",
            )
            eval_result = await self.evaluator.evaluate(
                task=task,
                step_description=f"\u0412\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438: {task[:100]}",
                result=_eval_tr,
            )
            quality_score = eval_result.quality_score
            _log.info(f"[{task_id}] quality_score={eval_result.quality_score:.2f}")
        except Exception as e:
            log_exception(_log, f"[{task_id}] Evaluator failed, using fallback", e)

        # Save to memory
        if self.memory:
            try:
                tools_used = list({tc.get("name") for tc in all_tool_calls}) or []
                await self.memory.on_task_end(
                    task, answer, success, duration,
                    steps_count=round_count,
                    tools_used=tools_used,
                    quality_score=quality_score,
                    user_id=user_id,
                )
            except Exception:
                pass

        return TaskResult(
            task_id=task_id, task=task, success=success,
            output=answer, answer=answer,
            duration=duration, quality_score=quality_score,
        )

    async def _classify_complex(self, task: str) -> bool:
        """ARCH-1.4: Haiku classifier — does this task need multiple agents?"""
        from src.organism.llm.base import Message as _CMsg
        resp = await self.llm.complete(
            messages=[_CMsg(role="user", content=task[:300])],
            system=(
                "Does this task require multiple specialized agents working together "
                "(e.g., research + write + calculate in one request)? "
                "A task that can be solved by a single tool call or a single text answer is NOT complex. "
                "Reply: yes or no."
            ),
            model_tier="fast",
            max_tokens=5,
        )
        return "yes" in resp.content.strip().lower()

    async def run(self, task: str, verbose: bool = True, user_id: str = "default", media: list | None = None, progress_callback=None, user_context: str = "", skip_orchestrator: bool = False, extra_system_context: str = "") -> "TaskResult":
        task_id = uuid.uuid4().hex[:8]
        start = time.time()
        _log.info(f"[{task_id}] Task started: {task[:100]}")
        self.logger.log_task_start(task_id, task)

        if verbose:
            print(f"\n{'='*50}\nTask [{task_id}]: {task}\n{'='*50}")

        memory_hits = 0
        memory_context = ""

        # FIX-24: Initialize memory BEFORE intent classification — needed for chat history in both paths
        if self.memory:
            try:
                await self.memory.initialize()
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory init failed", e)

        # --- Build user_context (needed for ALL paths including media) ---

        if self.memory:
            if not user_context:
                try:
                    user_facts = await self.memory.facts.get_all_facts(user_id=user_id)
                    user_context = format_for_prompt(user_facts)
                    if user_context and verbose:
                        print(f"User context: {user_context}")
                except Exception:
                    pass

        # FIX-64: Skip artel personality when agent personality is provided
        if self.personality and not extra_system_context:
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

        # FIX-67: Media path now receives full user_context (personality, user_facts, few-shot)
        if media:
            return await self._handle_conversation(
                task_id, task, user_context=user_context,
                user_id=user_id, media=media,
                extra_system_context=extra_system_context,
            )

        # --- Memory search (text-only path) ---
        if self.memory:
            try:
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
                        result_str = (s.get("result") or "")[:1000].replace("\n", " ")
                        line = f"- [{tool_str}] {task_str}"
                        if result_str:
                            line += f" -> {result_str}"
                        lines.append(line)
                    memory_context = "\n".join(lines)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory lookup failed", e)

        # HIST-1: Load recent chat history for task context
        if self.memory and user_id != "default":
            try:
                recent = await self.memory.chat_history.get_recent(user_id, limit=10)
                if recent:
                    lines = []
                    for msg in recent[-20:]:
                        prefix = "User" if msg["role"] == "user" else "Assistant"
                        lines.append(f"{prefix}: {msg['content'][:1000]}")
                    chat_context = "\n".join(lines)
                    user_context += f"\n\nRecent conversation:\n{chat_context}"
            except Exception:
                pass

        # L1 Solution Cache — check before planning/fast-path
        # FIX-48: LLM-based time-sensitivity check (replaces keyword heuristic)
        _time_sensitive = True  # safe default — skip cache
        try:
            from src.organism.llm.base import Message as _TSMsg
            _ts_resp = await self.llm.complete(
                messages=[_TSMsg(role="user", content=task[:300])],
                system="Does this task require real-time or current data that would be wrong if cached? Reply only: yes or no.",
                model_tier="fast",
                max_tokens=5,
            )
            _time_sensitive = "yes" in _ts_resp.content.strip().lower()
        except Exception:
            _time_sensitive = True  # on error, skip cache (safer)
        cache_hash: str | None = None
        canonical_task: str | None = None
        if self.memory and not _time_sensitive:
            try:
                canonical_task = await self.memory.cache.normalize_task(task, self.llm)
                cache_hash = self.memory.cache.hash_task(canonical_task)
                cached = await self.memory.cache.get(cache_hash)
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

        # ARCH-1.4: Route complex multi-agent tasks to Orchestrator if available
        # FIX-62: skip_orchestrator prevents recursion when MetaOrchestrator calls loop.run()
        if self._orchestrator is not None and not skip_orchestrator:
            try:
                is_complex = await self._classify_complex(task)
            except Exception:
                is_complex = False
            if is_complex:
                _log.info(f"[{task_id}] Routing to Orchestrator (complex task)")
                try:
                    orch_result = await self._orchestrator.run(task, verbose=verbose)
                    # FIX-65: Evaluate orchestrator result via Evaluator
                    quality = 0.8 if orch_result.success else 0.2
                    try:
                        from src.organism.tools.base import ToolResult as _TR
                        _eval_tr = _TR(
                            output=orch_result.output or "",
                            exit_code=0 if orch_result.success else 1,
                            error=orch_result.error or "",
                        )
                        eval_result = await self.evaluator.evaluate(
                            task=task,
                            step_description=f"Orchestrator: {task[:100]}",
                            result=_eval_tr,
                        )
                        quality = eval_result.quality_score
                    except Exception:
                        pass
                    # FIX-68: Save orchestrator result to long-term memory
                    if self.memory:
                        try:
                            await self.memory.on_task_end(
                                task, orch_result.output or "", orch_result.success,
                                time.time() - start,
                                tools_used=["orchestrator"],
                                quality_score=quality,
                                user_id=user_id,
                            )
                        except Exception:
                            pass
                    tr = TaskResult(
                        task_id=task_id, task=task,
                        success=orch_result.success,
                        output=orch_result.output,
                        answer=orch_result.output,
                        duration=time.time() - start,
                        quality_score=quality,
                        error=orch_result.error,
                    )
                    if self.memory and cache_hash and canonical_task:
                        try:
                            await self.memory.cache.put(
                                cache_hash, canonical_task, task,
                                tr.answer, tr.quality_score,
                            )
                        except Exception:
                            pass
                    return tr
                except Exception as e:
                    log_exception(_log, f"[{task_id}] Orchestrator failed, falling back", e)

        # Q-10.4: All tasks go through _handle_conversation (primary execution path)
        conv_result = await self._handle_conversation(
            task_id, task,
            user_context=user_context,
            memory_context=memory_context,
            user_id=user_id,
            extra_system_context=extra_system_context,
        )

        # ARCH-1.1: Store to SolutionCache if quality >= 0.8
        if self.memory and cache_hash and canonical_task:
            try:
                await self.memory.cache.put(
                    cache_hash, canonical_task, task,
                    conv_result.answer, conv_result.quality_score,
                )
            except Exception as e:
                log_exception(_log, f"[{task_id}] Cache store failed", e)

        return conv_result
