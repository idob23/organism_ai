"""PlannerModule — groups Planner and TaskDecomposer for Orchestrator use.

Extracted from CoreLoop (ARCH-1.2) since Q-10.4 made _handle_conversation
the primary execution path, making Planner/Decomposer dead code in CoreLoop.

FIX-66: Also hosts _validate_plan() and _execute_step() extracted from CoreLoop.
"""
import asyncio
import time

from src.organism.core.planner import Planner, PlanStep
from src.organism.core.decomposer import TaskDecomposer
from src.organism.core.evaluator import Evaluator
from src.organism.llm.base import LLMProvider
from src.organism.logging.error_handler import get_logger, log_exception
from src.organism.logging.logger import Logger
from src.organism.memory.manager import MemoryManager
from src.organism.safety.validator import SafetyValidator
from src.organism.tools.registry import ToolRegistry

_log = get_logger("planner_module")

MAX_RETRIES = 3
MAX_PLAN_STEPS = 10


class PlannerModule:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry | None = None,
        validator: SafetyValidator | None = None,
        evaluator: Evaluator | None = None,
        memory: MemoryManager | None = None,
    ) -> None:
        self.planner = Planner(llm)
        self.decomposer = TaskDecomposer(llm)
        self.registry = registry
        self.validator = validator or SafetyValidator()
        self.evaluator = evaluator
        self.memory = memory
        self.logger = Logger()

    def validate_plan(self, steps: list[PlanStep]) -> str | None:
        """Validate plan before execution. Returns error message or None if valid."""
        if not steps:
            return "Empty plan \u2014 no steps generated"

        if len(steps) > MAX_PLAN_STEPS:
            return f"Plan has {len(steps)} steps, maximum is {MAX_PLAN_STEPS}"

        if self.registry is None:
            return None

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
                continue

            if step.tool == "delegate_to_agent":
                if "peer_name" not in inp or "task" not in inp:
                    return f"Step {step.id}: delegate_to_agent requires 'peer_name' and 'task'"
                continue

            # MCP tools (mcp_*): input validation skipped — schema is dynamic.

            for dep in step.depends_on:
                if dep not in step_ids:
                    return f"Step {step.id}: depends_on references non-existent step {dep}"
                if dep >= step.id:
                    return f"Step {step.id}: depends_on step {dep} which comes after (circular)"

        return None

    async def execute_step(
        self, task_id: str, task: str, step: PlanStep, verbose: bool,
    ):
        """Execute a single plan step with retries and evaluation.

        Returns a dict with step results (compatible with StepLog fields).
        """
        from src.organism.core.loop import StepLog

        _log.info(f"[{task_id}] Step {step.id} start: [{step.tool}] {step.description[:80]}")
        if verbose:
            print(f"\nStep {step.id}: {step.description}")

        if step.tool == "code_executor":
            code = step.input.get("code", "")
            val = self.validator.validate_code(code)
            if not val.allowed:
                return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                               output="", error=f"Safety block: {val.reason}", success=False, duration=0.0)

        if self.registry is None:
            return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                           output="", error="No registry available", success=False, duration=0.0)

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

        for attempt in range(1, MAX_RETRIES + 1):
            step_start = time.time()
            if verbose and attempt > 1:
                print(f"  Retry {attempt}/{MAX_RETRIES}...")

            if attempt > 1:
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    _prev_error = result.error[:200] if result and result.error else "unknown"
                    asyncio.ensure_future(capture_error(
                        component=f"planner_module.step.{step.tool}",
                        message=f"Step {step.id} retry {attempt}/{MAX_RETRIES}: {_prev_error}",
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
                try:
                    from src.organism.monitoring.error_notifier import capture_error
                    asyncio.ensure_future(capture_error(
                        component=f"planner_module.{step.tool}", message=f"Step {step.id} crashed: {e}",
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

            if self.evaluator is None:
                from src.organism.core.evaluator import EvalResult
                eval_result = EvalResult(success=result.exit_code == 0, reason="No evaluator")
            else:
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
            elif eval_result.retry_hint and step.tool == "web_search" and "query" in step_input:
                step_input["query"] = f"{step_input['query']} {eval_result.retry_hint}"

            if verbose:
                print(f"  Eval: {eval_result.reason}")

        _log.error(f"[{task_id}] Step {step.id} FAILED after {MAX_RETRIES} attempts")
        return StepLog(step_id=step.id, tool=step.tool, description=step.description,
                       output=result.output if result else "",
                       error=eval_result.reason if eval_result else "Max retries exceeded",
                       success=False, duration=duration, attempts=MAX_RETRIES)
