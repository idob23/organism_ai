import time
import uuid
from dataclasses import dataclass, field

from src.organism.core.evaluator import EvalResult, Evaluator
from src.organism.core.planner import PlanStep, Planner
from src.organism.llm.base import LLMProvider
from src.organism.logging.logger import Logger
from src.organism.logging.error_handler import get_logger, log_exception
from src.organism.memory.manager import MemoryManager
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
    attempts: int


@dataclass
class TaskResult:
    task_id: str
    task: str
    success: bool
    output: str
    steps: list[StepLog] = field(default_factory=list)
    total_tokens: int = 0
    duration: float = 0.0
    error: str = ""
    memory_hits: int = 0


class CoreLoop:

    MAX_RETRIES = 3

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        memory: MemoryManager | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm)
        self.evaluator = Evaluator(llm)
        self.validator = SafetyValidator()
        self.logger = Logger()
        self.memory = memory

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
        memory_context = ""
        if self.memory:
            try:
                await self.memory.initialize()
                similar = await self.memory.on_task_start(task)
                if similar:
                    memory_hits = len(similar)
                    if verbose:
                        print(f"Memory: found {memory_hits} similar past task(s)")
                    lines = ["Similar tasks from memory:"]
                    for s in similar:
                        lines.append(f"- Task: {s['task'][:100]}")
                        lines.append(f"  Tools: {s['tools_used']}")
                        lines.append(f"  Result: {s['result'][:150]}")
                    memory_context = "\n".join(lines)
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory search failed", e)
                if verbose:
                    print(f"Memory unavailable: {e}")

        # Plan
        if verbose:
            print("Planning...")
        try:
            steps = await self.planner.plan(task, memory_context=memory_context)
            _log.info(f"[{task_id}] Plan created: {len(steps)} steps  {[s.tool for s in steps]}")
        except Exception as e:
            error = log_exception(_log, f"[{task_id}] Planning failed", e)
            return TaskResult(
                task_id=task_id, task=task, success=False,
                output="", error=f"Planning failed: {e}",
                duration=time.time() - start,
            )

        if verbose:
            print(f"Plan: {len(steps)} step(s)")
            for s in steps:
                print(f"  {s.id}. [{s.tool}] {s.description}")

        # Execute
        step_logs: list[StepLog] = []
        last_output = ""
        total_tokens = 0
        tools_used: list[str] = []

        for step in steps:
            log = await self._execute_step(task_id, task, step, verbose)
            step_logs.append(log)

            if log.success:
                last_output = log.output
                if step.tool not in tools_used:
                    tools_used.append(step.tool)
                if self.memory:
                    self.memory.working.add_step_result(
                        step.id, step.tool, log.output, True
                    )
            else:
                duration = time.time() - start
                _log.error(f"[{task_id}] Task FAILED at step {step.id}: {log.error}")
                if self.memory:
                    try:
                        await self.memory.on_task_end(
                            task, log.error, False, duration, len(step_logs), tools_used
                        )
                    except Exception as e:
                        log_exception(_log, f"[{task_id}] Memory save failed", e)
                self.logger.log_task_end(task_id, False, duration, total_tokens)
                return TaskResult(
                    task_id=task_id, task=task, success=False,
                    output=last_output, steps=step_logs,
                    total_tokens=total_tokens, duration=duration,
                    error=f"Step {step.id} failed: {log.error}",
                    memory_hits=memory_hits,
                )

        duration = time.time() - start
        _log.info(f"[{task_id}] Task SUCCESS in {duration:.1f}s, tools: {tools_used}")

        if self.memory:
            try:
                await self.memory.on_task_end(
                    task, last_output, True, duration, len(step_logs), tools_used
                )
            except Exception as e:
                log_exception(_log, f"[{task_id}] Memory save failed", e)

        self.logger.log_task_end(task_id, True, duration, total_tokens)

        if verbose:
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s | Memory hits: {memory_hits}")
            print(f"{'='*50}")

        return TaskResult(
            task_id=task_id, task=task, success=True,
            output=last_output, steps=step_logs,
            total_tokens=total_tokens, duration=duration,
            memory_hits=memory_hits,
        )

    async def _execute_step(
        self,
        task_id: str,
        task: str,
        step: PlanStep,
        verbose: bool,
    ) -> StepLog:
        _log.info(f"[{task_id}] Step {step.id} start: [{step.tool}] {step.description[:80]}")

        if verbose:
            print(f"\nStep {step.id}: {step.description}")

        if step.tool == "code_executor":
            code = step.input.get("code", "")
            val = self.validator.validate_code(code)
            if not val.allowed:
                _log.warning(f"[{task_id}] Step {step.id} blocked by safety: {val.reason}")
                if verbose:
                    print(f"  Blocked by safety: {val.reason}")
                return StepLog(
                    step_id=step.id, tool=step.tool,
                    description=step.description,
                    output="", error=f"Safety block: {val.reason}",
                    success=False, duration=0.0, attempts=0,
                )

        # Check tool exists
        try:
            tool = self.registry.get(step.tool)
        except KeyError:
            error = f"Tool '{step.tool}' not found in registry. Available: {self.registry.list_all()}"
            _log.error(f"[{task_id}] {error}")
            return StepLog(
                step_id=step.id, tool=step.tool,
                description=step.description,
                output="", error=error,
                success=False, duration=0.0, attempts=0,
            )

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
                error = log_exception(_log, f"[{task_id}] Step {step.id} tool execution crashed", e)
                result_duration = time.time() - step_start
                return StepLog(
                    step_id=step.id, tool=step.tool,
                    description=step.description,
                    output="", error=error,
                    success=False, duration=result_duration, attempts=attempt,
                )

            duration = time.time() - step_start

            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {duration:.1f}s | output: {result.output[:80] if result.output else '(empty)'}")
                if result.error:
                    print(f"  Error: {result.error[:120]}")

            if not result.success:
                _log.warning(f"[{task_id}] Step {step.id} attempt {attempt} failed: exit_code={result.exit_code} error={result.error[:200]}")

            try:
                eval_result = await self.evaluator.evaluate(
                    task=task,
                    step_description=step.description,
                    result=result,
                )
            except Exception as e:
                log_exception(_log, f"[{task_id}] Evaluator crashed", e)
                # If evaluator crashes, trust exit_code
                from src.organism.core.evaluator import EvalResult
                eval_result = EvalResult(
                    success=result.exit_code == 0,
                    reason="Evaluator unavailable, using exit_code",
                )

            self.logger.log_step(
                task_id, step.id, step.tool,
                eval_result.success, duration,
                error=result.error,
            )

            if eval_result.success:
                _log.info(f"[{task_id}] Step {step.id} SUCCESS on attempt {attempt}")
                return StepLog(
                    step_id=step.id, tool=step.tool,
                    description=step.description,
                    output=result.output, error="",
                    success=True, duration=duration, attempts=attempt,
                )

            if eval_result.retry_hint and step.tool == "code_executor":
                original_code = step_input.get("code", "")
                step_input["code"] = (
                    f"# Previous attempt failed: {eval_result.retry_hint}\n"
                    f"{original_code}"
                )

            if verbose:
                print(f"  Eval: {eval_result.reason}")

        _log.error(f"[{task_id}] Step {step.id} FAILED after {self.MAX_RETRIES} attempts: {eval_result.reason if eval_result else 'unknown'}")

        return StepLog(
            step_id=step.id, tool=step.tool,
            description=step.description,
            output=result.output if result else "",
            error=eval_result.reason if eval_result else "Max retries exceeded",
            success=False, duration=duration, attempts=self.MAX_RETRIES,
        )
