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

WRITE_KEYWORDS = ["напиши", "написать", "составь", "составить", "подготовь", "создай текст",
                  "коммерческое предложение", "статью", "отчёт", "письмо", "write", "draft", "compose"]


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


def _is_writing_task(task: str) -> bool:
    t = task.lower()
    return any(kw in t for kw in WRITE_KEYWORDS)


def _extract_filename(task: str) -> str | None:
    m = re.search(r"сохрани\s+(?:в\s+)?(?:файл\s+)?(\S+\.(?:md|txt|docx|html))", task, re.IGNORECASE)
    if not m:
        m = re.search(r"save\s+(?:to\s+)?(?:file\s+)?(\S+\.(?:md|txt|docx|html))", task, re.IGNORECASE)
    return m.group(1) if m else None


class CoreLoop:

    MAX_RETRIES = 3

    def __init__(self, llm: LLMProvider, registry: ToolRegistry, memory: MemoryManager | None = None) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm)
        self.evaluator = Evaluator(llm)
        self.validator = SafetyValidator()
        self.logger = Logger()
        self.memory = memory

    async def _run_writing_task(self, task_id: str, task: str, verbose: bool) -> TaskResult:
        """Direct path for writing tasks  bypasses Planner entirely."""
        start = time.time()
        filename = _extract_filename(task)

        if verbose:
            print(f"Writing task detected  using text_writer directly")
            print(f"Step 1: Generate and save text")

        try:
            tool = self.registry.get("text_writer")
        except KeyError:
            # Fall back to normal planning if text_writer not registered
            return None

        result = await tool.execute({
            "prompt": task,
            "filename": filename or "output.md",
        })

        duration = time.time() - start
        step_log = StepLog(step_id=1, tool="text_writer", description="Write and save text",
                           output=result.output, error=result.error,
                           success=result.exit_code == 0, duration=duration)

        if verbose:
            status = "OK" if result.exit_code == 0 else "FAIL"
            print(f"  [{status}] {duration:.1f}s | {result.output[:100]}")
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s")
            print(f"{'='*50}")

        return TaskResult(
            task_id=task_id, task=task,
            success=result.exit_code == 0,
            output=result.output, answer=result.output,
            steps=[step_log], duration=duration,
            error=result.error if result.exit_code != 0 else "",
        )

    async def run(self, task: str, verbose: bool = True) -> TaskResult:
        task_id = uuid.uuid4().hex[:8]
        start = time.time()
        _log.info(f"[{task_id}] Task started: {task[:100]}")
        self.logger.log_task_start(task_id, task)

        if verbose:
            print(f"\n{'='*50}")
            print(f"Task [{task_id}]: {task}")
            print(f"{'='*50}")

        # Memory
        memory_hits = 0
        if self.memory:
            try:
                await self.memory.initialize()
                similar = await self.memory.on_task_start(task)
                if similar:
                    memory_hits = len(similar)
                    if verbose:
                        print(f"Memory: found {memory_hits} similar past task(s)")
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory lookup failed", e)

        # Fast path for writing tasks
        if _is_writing_task(task):
            try:
                result = await self._run_writing_task(task_id, task, verbose)
                if result is not None:
                    result.memory_hits = memory_hits
                    _log.info(f"[{task_id}] Writing task {'SUCCESS' if result.success else 'FAILED'} in {result.duration:.1f}s")
                    return result
            except Exception as e:
                log_exception(_log, f"[{task_id}] Writing fast path failed, falling back to planner", e)

        # Normal planning path
        if verbose:
            print("Planning...")

        try:
            steps = await self.planner.plan(task)
            _log.info(f"[{task_id}] Plan created: {len(steps)} steps  {[s.tool for s in steps]}")
        except Exception as e:
            error = log_exception(_log, f"[{task_id}] Planning failed", e)
            return TaskResult(task_id=task_id, task=task, success=False, output="",
                              error=f"Planning failed: {e}", duration=time.time() - start, memory_hits=memory_hits)

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
            # Resolve {{step_N_output}} placeholders
            resolved_input = {}
            for k, v in step.input.items():
                if isinstance(v, str):
                    v = re.sub(r"\{\{step_(\d+)_output\}\}", lambda m: step_outputs.get(int(m.group(1)), ""), v)
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
                _log.info(f"[{task_id}] Step {step.id} SUCCESS on attempt {log.attempts}")
            else:
                duration = time.time() - start
                _log.error(f"[{task_id}] Task FAILED at step {step.id}: {log.error}")
                if self.memory:
                    try:
                        await self.memory.on_task_end(task, last_output, False, duration, len(step_logs), tools_used)
                    except Exception:
                        pass
                self.logger.log_task_end(task_id, False, duration, total_tokens)
                return TaskResult(task_id=task_id, task=task, success=False, output=last_output,
                                  steps=step_logs, duration=duration, error=f"Step {step.id} failed: {log.error}",
                                  memory_hits=memory_hits)

        duration = time.time() - start
        _log.info(f"[{task_id}] Task SUCCESS in {duration:.1f}s, tools: {tools_used}")

        if self.memory:
            try:
                await self.memory.on_task_end(task, last_output, True, duration, len(step_logs), tools_used)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory save failed", e)

        self.logger.log_task_end(task_id, True, duration, total_tokens)

        if verbose:
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s | Memory hits: {memory_hits}")
            print(f"{'='*50}")

        return TaskResult(task_id=task_id, task=task, success=True, output=last_output, answer=last_output,
                          steps=step_logs, total_tokens=total_tokens, duration=duration, memory_hits=memory_hits)

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
                               output="", error=error, success=False, duration=time.time() - step_start, attempts=attempt)

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
                _log.info(f"[{task_id}] Step {step.id} SUCCESS on attempt {attempt}")
                return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                               output=result.output, error="", success=True, duration=duration, attempts=attempt)

            if eval_result.retry_hint and step.tool == "code_executor":
                step_input["code"] = f"# Previous failed: {eval_result.retry_hint}\n{step_input.get('code', '')}"

            if verbose:
                print(f"  Eval: {eval_result.reason}")

        _log.error(f"[{task_id}] Step {step.id} FAILED after {self.MAX_RETRIES} attempts")
        return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                       output=result.output if result else "",
                       error=eval_result.reason if eval_result else "Max retries exceeded",
                       success=False, duration=duration, attempts=self.MAX_RETRIES)
