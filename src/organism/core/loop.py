import time
import uuid
from dataclasses import dataclass, field

from src.organism.core.evaluator import EvalResult, Evaluator
from src.organism.core.planner import PlanStep, Planner
from src.organism.llm.base import LLMProvider
from src.organism.logging.logger import Logger
from src.organism.safety.validator import SafetyValidator
from src.organism.tools.registry import ToolRegistry


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


class CoreLoop:

    MAX_RETRIES = 3

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.planner = Planner(llm)
        self.evaluator = Evaluator(llm)
        self.validator = SafetyValidator()
        self.logger = Logger()

    async def run(self, task: str, verbose: bool = True) -> TaskResult:
        task_id = uuid.uuid4().hex[:8]
        start = time.time()
        self.logger.log_task_start(task_id, task)

        if verbose:
            print(f"\n{'='*50}")
            print(f"Task [{task_id}]: {task}")
            print(f"{'='*50}")

        # Plan
        if verbose:
            print("Planning...")
        try:
            steps = await self.planner.plan(task)
        except Exception as e:
            return TaskResult(
                task_id=task_id, task=task, success=False,
                output="", error=f"Planning failed: {e}",
                duration=time.time() - start,
            )

        if verbose:
            print(f"Plan: {len(steps)} step(s)")
            for s in steps:
                print(f"  {s.id}. [{s.tool}] {s.description}")

        # Execute steps
        step_logs: list[StepLog] = []
        last_output = ""
        total_tokens = 0

        for step in steps:
            log = await self._execute_step(
                task_id, task, step, verbose
            )
            step_logs.append(log)

            if log.success:
                last_output = log.output
            else:
                # Step failed after all retries
                duration = time.time() - start
                self.logger.log_task_end(task_id, False, duration, total_tokens)
                return TaskResult(
                    task_id=task_id, task=task, success=False,
                    output=last_output, steps=step_logs,
                    total_tokens=total_tokens, duration=duration,
                    error=f"Step {step.id} failed: {log.error}",
                )

        duration = time.time() - start
        self.logger.log_task_end(task_id, True, duration, total_tokens)

        if verbose:
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s")
            print(f"{'='*50}")

        return TaskResult(
            task_id=task_id, task=task, success=True,
            output=last_output, steps=step_logs,
            total_tokens=total_tokens, duration=duration,
        )

    async def _execute_step(
        self,
        task_id: str,
        task: str,
        step: PlanStep,
        verbose: bool,
    ) -> StepLog:
        if verbose:
            print(f"\nStep {step.id}: {step.description}")

        # Safety check
        if step.tool == "code_executor":
            code = step.input.get("code", "")
            val = self.validator.validate_code(code)
            if not val.allowed:
                if verbose:
                    print(f"  Blocked by safety: {val.reason}")
                return StepLog(
                    step_id=step.id, tool=step.tool,
                    description=step.description,
                    output="", error=f"Safety block: {val.reason}",
                    success=False, duration=0.0, attempts=0,
                )

        tool = self.registry.get(step.tool)
        step_input = dict(step.input)

        for attempt in range(1, self.MAX_RETRIES + 1):
            step_start = time.time()

            if verbose and attempt > 1:
                print(f"  Retry {attempt}/{self.MAX_RETRIES}...")

            result = await tool.execute(step_input)
            duration = time.time() - step_start

            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {duration:.1f}s | output: {result.output[:80] if result.output else '(empty)'}")
                if result.error:
                    print(f"  Error: {result.error[:120]}")

            eval_result: EvalResult = await self.evaluator.evaluate(
                task=task,
                step_description=step.description,
                result=result,
            )

            self.logger.log_step(
                task_id, step.id, step.tool,
                eval_result.success, duration,
                error=result.error,
            )

            if eval_result.success:
                return StepLog(
                    step_id=step.id, tool=step.tool,
                    description=step.description,
                    output=result.output, error="",
                    success=True, duration=duration, attempts=attempt,
                )

            # Inject retry hint into next attempt
            if eval_result.retry_hint and step.tool == "code_executor":
                original_code = step_input.get("code", "")
                step_input["code"] = (
                    f"# Previous attempt failed: {eval_result.retry_hint}\n"
                    f"{original_code}"
                )

            if verbose:
                print(f"  Eval: {eval_result.reason}")

        return StepLog(
            step_id=step.id, tool=step.tool,
            description=step.description,
            output=result.output,
            error=eval_result.reason,
            success=False, duration=duration, attempts=self.MAX_RETRIES,
        )
