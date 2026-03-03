"""Q-7.2: Benchmark-driven prompt optimization.

Pipeline: baseline benchmark -> Haiku generates N prompt variants ->
quick benchmark per variant -> select winner -> deploy via PVC.

Uses --quick benchmark (5 tasks) for variant evaluation to save API costs.
Only the evaluator prompt is optimized in this version (already PVC-integrated).
"""
import time
from dataclasses import dataclass
from pathlib import Path

from src.organism.llm.base import LLMProvider, Message
from src.organism.self_improvement.prompt_versioning import PromptVersionControl
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("self_improvement.benchmark_optimizer")

# Prompts eligible for optimization (expandable for planner_fast, planner_react)
OPTIMIZABLE_PROMPTS: dict[str, Path] = {
    "evaluator": Path("config/prompts/evaluator.txt"),
}

MAX_VARIANTS = 3
MIN_IMPROVEMENT = 0.03  # minimum +3% quality to deploy


@dataclass
class OptimizationResult:
    prompt_name: str
    baseline_score: float
    best_variant_score: float
    improvement: float
    deployed: bool
    variants_tested: int
    duration: float


class BenchmarkPromptOptimizer:

    def __init__(self, llm: LLMProvider, pvc: PromptVersionControl) -> None:
        self.llm = llm
        self.pvc = pvc

    async def generate_variants(
        self, prompt_name: str, current_content: str, n: int = MAX_VARIANTS,
    ) -> list[str]:
        """Ask Haiku to generate *n* mutated versions of a prompt.

        Each variant is a complete prompt, not a diff.
        Mutations: rephrase instructions, reorder sections, add/remove examples,
        change tone, tighten JSON format spec.
        """
        system = (
            "You are a prompt engineering expert. "
            "Generate a VARIANT of the given prompt that might perform better. "
            "The variant must be a COMPLETE replacement prompt, not a diff. "
            "Preserve the core task (evaluate AI output quality, return JSON "
            "with success/reason/quality_score/retry_hint). "
            "Mutations: rephrase for clarity, add edge case handling, improve "
            "scoring criteria, reorder instructions, make JSON format stricter. "
            "Return ONLY the new prompt text, no explanation or wrapping."
        )

        diversity_hints = [
            "Focus on making scoring criteria more precise.",
            "Focus on better handling of edge cases and partial successes.",
            "Focus on clearer JSON output instructions and stricter format.",
        ]

        variants: list[str] = []
        for i in range(n):
            try:
                resp = await self.llm.complete(
                    messages=[Message(role="user", content=(
                        f"Original prompt:\n---\n{current_content}\n---\n\n"
                        f"Mutation focus: {diversity_hints[i % len(diversity_hints)]}\n"
                        f"Generate variant #{i + 1}."
                    ))],
                    system=system,
                    model_tier="fast",
                    max_tokens=2000,
                )
                variant = resp.content.strip()
                if len(variant) > 100:  # sanity check
                    variants.append(variant)
            except Exception as e:
                log_exception(_log, f"Failed to generate variant #{i + 1}", e)

        return variants

    async def run_quick_benchmark(self) -> float:
        """Run quick benchmark (5 tasks) and return average quality score.

        Imports benchmark infrastructure at call time to avoid circular deps.
        """
        try:
            from benchmark import (  # noqa: F401 — project root module
                TASKS, QUICK_IDS, build_registry, run_loop_task,
                run_command_task, run_orchestrator_task,
            )
            from src.organism.llm.claude import ClaudeProvider
            from src.organism.core.loop import CoreLoop
            from src.organism.memory.manager import MemoryManager
            from config.settings import settings

            llm = ClaudeProvider()
            registry = build_registry()
            memory = None
            if settings.database_url:
                try:
                    memory = MemoryManager()
                    await memory.initialize()
                except Exception:
                    pass

            personality = None
            try:
                from src.organism.core.personality import PersonalityConfig
                p = PersonalityConfig(artel_id=settings.artel_id)
                p.load()
                personality = p
            except Exception:
                pass

            loop = CoreLoop(llm, registry, memory=memory, personality=personality)

            tasks = [t for t in TASKS if t["id"] in QUICK_IDS]
            scores: list[float] = []
            for task_def in tasks:
                try:
                    bm = await run_loop_task(task_def, loop)
                    if bm.quality_score > 0:
                        scores.append(bm.quality_score)
                except Exception:
                    pass

            return sum(scores) / len(scores) if scores else 0.0
        except Exception as e:
            log_exception(_log, "Quick benchmark failed", e)
            return 0.0

    async def optimize(self, prompt_name: str = "evaluator") -> OptimizationResult:
        """Full optimization pipeline for one prompt.

        1. Read current prompt content (from PVC or file)
        2. Run quick benchmark -> baseline score
        3. Generate N variants via Haiku
        4. For each variant: save to PVC -> run quick benchmark -> record score
        5. If best > baseline + MIN_IMPROVEMENT -> keep deployed
        6. Else -> restore original
        """
        t0 = time.time()
        _log.info(f"Starting prompt optimization for '{prompt_name}'")

        # 1. Get current content
        current_content = None
        try:
            current_content = await self.pvc.get_active(prompt_name)
        except Exception:
            pass
        if not current_content:
            file_path = OPTIMIZABLE_PROMPTS.get(prompt_name)
            if file_path and file_path.exists():
                current_content = file_path.read_text(encoding="utf-8")
            else:
                _log.error(f"No content found for prompt '{prompt_name}'")
                return OptimizationResult(
                    prompt_name, 0, 0, 0, False, 0, time.time() - t0,
                )

        # 2. Baseline benchmark
        _log.info("Running baseline benchmark...")
        baseline = await self.run_quick_benchmark()
        _log.info(f"Baseline avg_quality: {baseline:.4f}")

        if baseline <= 0:
            _log.warning("Baseline score is zero, aborting optimization")
            return OptimizationResult(
                prompt_name, baseline, baseline, 0, False, 0, time.time() - t0,
            )

        # 3. Generate variants
        _log.info(f"Generating {MAX_VARIANTS} prompt variants...")
        variants = await self.generate_variants(prompt_name, current_content)
        if not variants:
            _log.warning("No variants generated, aborting")
            return OptimizationResult(
                prompt_name, baseline, baseline, 0, False, 0, time.time() - t0,
            )

        # 4. Evaluate each variant
        best_score = baseline
        best_content = current_content

        for i, variant in enumerate(variants):
            _log.info(f"Evaluating variant {i + 1}/{len(variants)}...")
            try:
                # Deploy variant temporarily
                await self.pvc.save_version(prompt_name, variant)

                score = await self.run_quick_benchmark()
                _log.info(
                    f"Variant {i + 1} avg_quality: {score:.4f} "
                    f"(baseline: {baseline:.4f})"
                )

                if score > best_score:
                    best_score = score
                    best_content = variant
            except Exception as e:
                log_exception(_log, f"Variant {i + 1} evaluation failed", e)

        # 5. Deploy winner or restore original
        improvement = best_score - baseline
        deployed = improvement >= MIN_IMPROVEMENT

        try:
            if deployed:
                await self.pvc.save_version(prompt_name, best_content)
                _log.info(
                    f"Deployed winning variant: +{improvement:.4f} quality improvement"
                )
            else:
                # Restore original — system never left with a junk prompt
                await self.pvc.save_version(prompt_name, current_content)
                _log.info(
                    f"No improvement >= {MIN_IMPROVEMENT}, restored original"
                )
        except Exception as e:
            # Last resort: try to restore original to avoid junk state
            log_exception(_log, "Failed to deploy/restore prompt", e)
            try:
                await self.pvc.save_version(prompt_name, current_content)
            except Exception:
                pass

        duration = time.time() - t0
        result = OptimizationResult(
            prompt_name=prompt_name,
            baseline_score=baseline,
            best_variant_score=best_score,
            improvement=improvement,
            deployed=deployed,
            variants_tested=len(variants),
            duration=duration,
        )
        _log.info(f"Optimization complete: {result}")
        return result

    async def optimize_all(self) -> list[OptimizationResult]:
        """Optimize all registered prompts sequentially."""
        results: list[OptimizationResult] = []
        for name in OPTIMIZABLE_PROMPTS:
            try:
                r = await self.optimize(name)
                results.append(r)
            except Exception as e:
                log_exception(_log, f"Optimization failed for '{name}'", e)
        return results
