"""Q-7.4: Evolutionary prompt search.

Persistent population of 3-5 prompt variants per component. Each variant
lives in the DB (prompt_population table), evolves over generations via
evaluate -> mutate -> select cycle. Best variant deployed via PVC.

Difference from BenchmarkPromptOptimizer (Q-7.2): that is a one-shot
optimization pass; this maintains a *persistent* population that improves
across weekly cycles.
"""
import time
from dataclasses import dataclass

from sqlalchemy import select, func

from src.organism.llm.base import LLMProvider, Message
from src.organism.memory.database import (
    PromptPopulationMember, AsyncSessionLocal,
)
from src.organism.self_improvement.prompt_versioning import PromptVersionControl
from src.organism.self_improvement.benchmark_optimizer import (
    OPTIMIZABLE_PROMPTS, BenchmarkPromptOptimizer,
)
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("self_improvement.evolutionary_search")

# ---- Constants ----
POPULATION_SIZE = 5
MIN_POPULATION = 3
ELITE_COUNT = 2        # top N survivors kept unchanged across generations
MUTATION_TYPES = ["rephrase", "restructure", "specialize"]
MIN_EVALS_FOR_SELECTION = 1  # minimum evals before a member can be culled


@dataclass
class EvolutionResult:
    prompt_name: str
    generation: int
    population_size: int
    best_fitness: float
    deployed: bool
    duration: float


class EvolutionaryPromptSearch:

    def __init__(self, llm: LLMProvider, pvc: PromptVersionControl) -> None:
        self.llm = llm
        self.pvc = pvc
        self._benchmark_optimizer = BenchmarkPromptOptimizer(llm, pvc)

    # ---- Population management ----

    async def get_population(self, prompt_name: str) -> list[dict]:
        """Return all members for a prompt ordered by fitness desc."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(PromptPopulationMember)
                .where(PromptPopulationMember.prompt_name == prompt_name)
                .order_by(PromptPopulationMember.fitness.desc())
            )
            result = await session.execute(stmt)
            return [
                {
                    "id": m.id,
                    "prompt_name": m.prompt_name,
                    "content": m.content,
                    "generation": m.generation,
                    "fitness": m.fitness,
                    "eval_count": m.eval_count,
                    "parent_id": m.parent_id,
                    "mutation_type": m.mutation_type,
                    "is_active": m.is_active,
                }
                for m in result.scalars().all()
            ]

    async def seed_population(self, prompt_name: str) -> int:
        """Seed initial population from current active prompt content.

        Creates MIN_POPULATION members: 1 original + (MIN_POPULATION-1) mutations.
        Returns the number of members created.
        """
        # Get current content
        current = None
        try:
            current = await self.pvc.get_active(prompt_name)
        except Exception:
            pass
        if not current:
            path = OPTIMIZABLE_PROMPTS.get(prompt_name)
            if path and path.exists():
                current = path.read_text(encoding="utf-8")
        if not current:
            _log.warning(f"No content found for '{prompt_name}', cannot seed")
            return 0

        created = 0
        async with AsyncSessionLocal() as session:
            # Original as generation-0 member
            session.add(PromptPopulationMember(
                prompt_name=prompt_name,
                content=current,
                generation=0,
                fitness=0.0,
                eval_count=0,
                parent_id=None,
                mutation_type=None,
                is_active=True,
            ))
            created += 1

            # Generate initial mutations
            for i in range(MIN_POPULATION - 1):
                mt = MUTATION_TYPES[i % len(MUTATION_TYPES)]
                try:
                    variant = await self._mutate(current, mt)
                    if variant:
                        session.add(PromptPopulationMember(
                            prompt_name=prompt_name,
                            content=variant,
                            generation=0,
                            fitness=0.0,
                            eval_count=0,
                            parent_id=None,
                            mutation_type=mt,
                            is_active=False,
                        ))
                        created += 1
                except Exception as e:
                    log_exception(_log, f"Seed mutation {i + 1} failed", e)

            await session.commit()

        _log.info(f"Seeded {created} members for '{prompt_name}'")
        return created

    # ---- Mutation ----

    async def _mutate(self, content: str, mutation_type: str) -> str | None:
        """Generate a single mutated variant via Haiku."""
        mutation_instructions = {
            "rephrase": (
                "Rephrase the instructions for maximum clarity. "
                "Keep the same structure but use different wording. "
                "Make ambiguous instructions more precise."
            ),
            "restructure": (
                "Reorganize the prompt structure. Move the most important "
                "instructions to the top. Group related rules together. "
                "Add or remove section headers for clarity."
            ),
            "specialize": (
                "Make the prompt more specialized and precise. "
                "Add specific edge case handling. Tighten the JSON format "
                "specification. Add examples where helpful."
            ),
        }
        instruction = mutation_instructions.get(
            mutation_type, mutation_instructions["rephrase"],
        )

        system = (
            "You are an expert prompt engineer performing evolutionary "
            "optimization. Generate a COMPLETE replacement prompt, not a diff. "
            "Preserve the core task and output format. "
            "Return ONLY the new prompt text, no explanation or wrapping."
        )

        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=(
                    f"Original prompt:\n---\n{content}\n---\n\n"
                    f"Mutation type: {mutation_type}\n"
                    f"Instructions: {instruction}\n"
                    f"Generate the mutated prompt."
                ))],
                system=system,
                model_tier="fast",
                max_tokens=2000,
            )
            variant = resp.content.strip()
            if len(variant) > 100:
                return variant
        except Exception as e:
            log_exception(_log, f"Mutation ({mutation_type}) failed", e)

        return None

    # ---- Evaluation ----

    async def evaluate_member(self, member_id: int) -> float:
        """Run quick benchmark with a member's content as active prompt.

        Temporarily deploys the member's content via PVC, runs benchmark,
        records fitness. Returns the fitness score.
        """
        # Fetch member
        async with AsyncSessionLocal() as session:
            stmt = select(PromptPopulationMember).where(
                PromptPopulationMember.id == member_id,
            )
            result = await session.execute(stmt)
            member = result.scalar_one_or_none()
            if not member:
                return 0.0
            prompt_name = member.prompt_name
            content = member.content

        # Deploy temporarily and benchmark
        try:
            await self.pvc.save_version(prompt_name, content)
            score = await self._benchmark_optimizer.run_quick_benchmark()
        except Exception as e:
            log_exception(_log, f"Evaluate member {member_id} failed", e)
            score = 0.0

        # Update fitness (running average)
        async with AsyncSessionLocal() as session:
            stmt = select(PromptPopulationMember).where(
                PromptPopulationMember.id == member_id,
            )
            result = await session.execute(stmt)
            member = result.scalar_one_or_none()
            if member and score > 0:
                new_count = member.eval_count + 1
                member.fitness = round(
                    (member.fitness * member.eval_count + score) / new_count, 4,
                )
                member.eval_count = new_count
                await session.commit()
                _log.info(
                    f"Member {member_id} fitness={member.fitness:.4f} "
                    f"(evals={new_count})"
                )

        return score

    # ---- Evolution cycle ----

    async def evolve(self, prompt_name: str) -> EvolutionResult:
        """Full evolution cycle for one prompt.

        1. Get or seed population
        2. Evaluate all members (quick benchmark each)
        3. Selection: keep ELITE_COUNT best, cull worst
        4. Mutation: generate children from elites to fill POPULATION_SIZE
        5. Deploy best member via PVC
        """
        t0 = time.time()
        _log.info(f"Starting evolution cycle for '{prompt_name}'")

        # 1. Get or seed population
        population = await self.get_population(prompt_name)
        if len(population) < MIN_POPULATION:
            await self.seed_population(prompt_name)
            population = await self.get_population(prompt_name)

        if not population:
            _log.error(f"Cannot evolve '{prompt_name}': empty population")
            return EvolutionResult(prompt_name, 0, 0, 0.0, False, time.time() - t0)

        # Determine current generation
        max_gen = max(m["generation"] for m in population)

        # 2. Evaluate all members
        for member in population:
            try:
                await self.evaluate_member(member["id"])
            except Exception as e:
                log_exception(
                    _log, f"Eval failed for member {member['id']}", e,
                )

        # Re-fetch after evaluation
        population = await self.get_population(prompt_name)

        # 3. Selection — keep top ELITE_COUNT, cull excess with enough evals
        evaluated = [
            m for m in population if m["eval_count"] >= MIN_EVALS_FOR_SELECTION
        ]
        if len(evaluated) > ELITE_COUNT:
            # Sort by fitness desc (already sorted from DB)
            elites = evaluated[:ELITE_COUNT]
            to_cull = evaluated[ELITE_COUNT:]

            # Delete culled members
            cull_ids = [m["id"] for m in to_cull]
            if cull_ids:
                async with AsyncSessionLocal() as session:
                    for cid in cull_ids:
                        stmt = select(PromptPopulationMember).where(
                            PromptPopulationMember.id == cid,
                        )
                        result = await session.execute(stmt)
                        row = result.scalar_one_or_none()
                        if row:
                            await session.delete(row)
                    await session.commit()
                _log.info(f"Culled {len(cull_ids)} members")
        else:
            elites = evaluated or population[:ELITE_COUNT]

        # 4. Mutation — fill population to POPULATION_SIZE from elites
        current_count = len(elites) + len(
            [m for m in population if m["eval_count"] < MIN_EVALS_FOR_SELECTION],
        )
        new_gen = max_gen + 1
        children_needed = max(0, POPULATION_SIZE - current_count)

        for i in range(children_needed):
            parent = elites[i % len(elites)] if elites else population[0]
            mt = MUTATION_TYPES[i % len(MUTATION_TYPES)]
            try:
                variant = await self._mutate(parent["content"], mt)
                if variant:
                    async with AsyncSessionLocal() as session:
                        session.add(PromptPopulationMember(
                            prompt_name=prompt_name,
                            content=variant,
                            generation=new_gen,
                            fitness=0.0,
                            eval_count=0,
                            parent_id=parent["id"],
                            mutation_type=mt,
                            is_active=False,
                        ))
                        await session.commit()
                    _log.info(
                        f"Created child (gen={new_gen}, type={mt}, "
                        f"parent={parent['id']})"
                    )
            except Exception as e:
                log_exception(_log, f"Child mutation {i + 1} failed", e)

        # 5. Deploy best member
        population = await self.get_population(prompt_name)
        best = population[0] if population else None
        deployed = False

        if best and best["fitness"] > 0:
            try:
                await self.pvc.save_version(prompt_name, best["content"])
                # Mark as active in population
                async with AsyncSessionLocal() as session:
                    # Clear old active flags
                    all_stmt = (
                        select(PromptPopulationMember)
                        .where(PromptPopulationMember.prompt_name == prompt_name)
                        .where(PromptPopulationMember.is_active == True)  # noqa: E712
                    )
                    result = await session.execute(all_stmt)
                    for row in result.scalars().all():
                        row.is_active = False
                    # Set new active
                    best_stmt = select(PromptPopulationMember).where(
                        PromptPopulationMember.id == best["id"],
                    )
                    result = await session.execute(best_stmt)
                    best_row = result.scalar_one_or_none()
                    if best_row:
                        best_row.is_active = True
                    await session.commit()
                deployed = True
                _log.info(
                    f"Deployed best member {best['id']} "
                    f"(fitness={best['fitness']:.4f})"
                )
            except Exception as e:
                log_exception(_log, "Failed to deploy best member", e)

        duration = time.time() - t0
        result = EvolutionResult(
            prompt_name=prompt_name,
            generation=new_gen,
            population_size=len(population),
            best_fitness=best["fitness"] if best else 0.0,
            deployed=deployed,
            duration=duration,
        )
        _log.info(f"Evolution complete: {result}")
        return result

    async def evolve_all(self) -> list[EvolutionResult]:
        """Run evolution cycle for all optimizable prompts."""
        results: list[EvolutionResult] = []
        for name in OPTIMIZABLE_PROMPTS:
            try:
                r = await self.evolve(name)
                results.append(r)
            except Exception as e:
                log_exception(_log, f"Evolution failed for '{name}'", e)
        return results
