import time
import uuid
import re
from dataclasses import dataclass, field

from src.organism.core.evaluator import Evaluator
from src.organism.core.planner import PlanStep, Planner
from src.organism.llm.base import LLMProvider
from src.organism.logging.logger import Logger
from src.organism.logging.error_handler import get_logger, log_exception
from src.organism.memory.manager import MemoryManager
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

    def __init__(self, llm: LLMProvider, registry: ToolRegistry, memory: MemoryManager | None = None) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm)
        self.evaluator = Evaluator(llm)
        self.validator = SafetyValidator()
        self.logger = Logger()
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

            for dep in step.depends_on:
                if dep not in step_ids:
                    return f"Step {step.id}: depends_on references non-existent step {dep}"
                if dep >= step.id:
                    return f"Step {step.id}: depends_on step {dep} which comes after (circular)"

        return None

    async def _run_writing_task(self, task_id: str, task: str, verbose: bool) -> "TaskResult | None":
        start = time.time()
        filename = _extract_filename(task) or "output.md"
        try:
            tool = self.registry.get("text_writer")
        except KeyError:
            return None

        if verbose:
            print("Writing task detected - using text_writer directly")
            print("Step 1: Generate and save text")

        result = await tool.execute({"prompt": task, "filename": filename})
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

    async def run(self, task: str, verbose: bool = True) -> "TaskResult":
        task_id = uuid.uuid4().hex[:8]
        start = time.time()
        _log.info(f"[{task_id}] Task started: {task[:100]}")
        self.logger.log_task_start(task_id, task)

        if verbose:
            print(f"\n{'='*50}\nTask [{task_id}]: {task}\n{'='*50}")

        memory_hits = 0
        memory_context = ""
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
                        lines.append(f"- [{tool_str}] {s.get('task', '')[:80]}")
                    memory_context = "\n".join(lines)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory lookup failed", e)

        # Fast path for writing tasks
        if _is_writing_task(task):
            try:
                result = await self._run_writing_task(task_id, task, verbose)
                if result is not None:
                    result.memory_hits = memory_hits
                    _log.info(f"[{task_id}] Writing task {'SUCCESS' if result.success else 'FAILED'} in {result.duration:.1f}s")
                    if self.memory and result.success:
                        try:
                            await self.memory.on_task_end(task, result.output, True, result.duration, 1, ["text_writer"], quality_score=0.8)
                        except Exception:
                            pass
                    return result
            except Exception as e:
                log_exception(_log, f"[{task_id}] Writing fast path failed", e)

        if verbose:
            print("Planning...")

        try:
            steps = await self.planner.plan(task, memory_context=memory_context)
            _log.info(f"[{task_id}] Plan created: {len(steps)} steps  {[s.tool for s in steps]}")
        except Exception as e:
            log_exception(_log, f"[{task_id}] Planning failed", e)
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
                steps = await self.planner._fast_plan(task)
                if not steps:
                    steps = await self.planner._react_plan(task)
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
                duration = time.time() - start
                _log.error(f"[{task_id}] Task FAILED at step {step.id}: {log.error}")
                if self.memory:
                    try:
                        await self.memory.on_task_end(task, last_output, False, duration, len(step_logs), tools_used, quality_score=0.2)
                    except Exception:
                        pass
                self.logger.log_task_end(task_id, False, duration, total_tokens)
                return TaskResult(task_id=task_id, task=task, success=False, output=last_output,
                                  steps=step_logs, duration=duration,
                                  error=f"Step {step.id} failed: {log.error}", memory_hits=memory_hits)

        duration = time.time() - start
        _log.info(f"[{task_id}] Task SUCCESS in {duration:.1f}s, tools: {tools_used}")

        # Calculate average quality score
        avg_quality = sum(s.quality_score for s in step_logs if s.success) / max(len([s for s in step_logs if s.success]), 1)

        if self.memory:
            try:
                await self.memory.on_task_end(task, last_output, True, duration, len(step_logs), tools_used, quality_score=avg_quality)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory save failed", e)

        self.logger.log_task_end(task_id, True, duration, total_tokens)

        if verbose:
            print(f"\n{'='*50}\nDone in {duration:.1f}s | Quality: {avg_quality:.2f} | Memory hits: {memory_hits}\n{'='*50}")

        return TaskResult(task_id=task_id, task=task, success=True, output=last_output, answer=last_output,
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

            try:
                result = await tool.execute(step_input)
            except Exception as e:
                error = log_exception(_log, f"[{task_id}] Step {step.id} crashed", e)
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
