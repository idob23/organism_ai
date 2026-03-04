import asyncio
import hashlib
import uuid
from .longterm import LongTermMemory
from .working import WorkingMemory
from .user_facts import UserFactsExtractor
from .graph import MemoryGraph
from .causal_analyzer import CausalAnalyzer
from .templates import TemplateExtractor
from .search_policy import SearchPolicy
from .few_shot_store import FewShotStore
from .database import init_db, AgentReflection, TaskMemory, AsyncSessionLocal
from sqlalchemy import select, or_
from src.organism.llm.base import LLMProvider


class MemoryManager:

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.longterm = LongTermMemory()
        self.working = WorkingMemory()
        self.facts = UserFactsExtractor()
        self.graph = MemoryGraph()
        self.templates = TemplateExtractor()
        self.few_shot = FewShotStore()
        self.llm = llm
        self._initialized = False

    async def initialize(self) -> None:
        if not self._initialized:
            await init_db()
            # Restore last_task_id from DB so temporal edges work across CLI invocations
            try:
                async with AsyncSessionLocal() as session:
                    row = await session.scalar(
                        select(TaskMemory.id)
                        .order_by(TaskMemory.created_at.desc())
                        .limit(1)
                    )
                    if row:
                        self.working.last_task_id = row
            except Exception:
                pass
            self._initialized = True

    async def on_task_start(self, task: str) -> list[dict]:
        self.working.clear()
        self.working.task = task

        policy = SearchPolicy()
        intent = policy.classify_intent(task)
        weights = policy.get_weights(intent)

        results: list[dict] = []

        # Vector search — always active, weight varies by intent
        if weights["vector"] > 0:
            try:
                similar = await self.longterm.search_similar(task, limit=3, llm=self.llm)
                for item in similar:
                    item["_source"] = "vector"
                    item["_weight"] = weights["vector"]
                results.extend(similar)
            except Exception:
                pass

        # Temporal — recent tasks via graph edges
        if weights["temporal"] > 0.3 and self.working.last_task_id:
            try:
                temporal = await self.graph.get_related_tasks(
                    self.working.last_task_id, edge_types=["temporal"], limit=3
                )
                for item in temporal:
                    item["_source"] = "temporal"
                    item["_weight"] = weights["temporal"]
                results.extend(temporal)
            except Exception:
                pass

        # Causal — cause-effect relationships via graph
        if weights["causal"] > 0.3 and self.working.last_task_id:
            try:
                causal = await self.graph.get_related_tasks(
                    self.working.last_task_id, edge_types=["causal"], limit=3
                )
                for item in causal:
                    item["_source"] = "causal"
                    item["_weight"] = weights["causal"]
                results.extend(causal)
            except Exception:
                pass

        # Entity — tasks connected through shared entities
        if weights["entity"] > 0.3:
            try:
                entities = self._extract_entities(task, policy)
                for entity in entities[:2]:  # max 2 entities to limit DB calls
                    entity_tasks = await self.graph.get_entity_subgraph(entity, depth=1)
                    for item in entity_tasks:
                        item["_source"] = "entity"
                        item["_weight"] = weights["entity"]
                    results.extend(entity_tasks)
            except Exception:
                pass

        # Deduplicate by task_id / id, sort by _weight desc, cap at 5
        seen: set[str] = set()
        unique: list[dict] = []
        for r in sorted(results, key=lambda x: x.get("_weight", 0), reverse=True):
            tid = r.get("task_id") or r.get("id", "")
            if tid and tid not in seen:
                seen.add(tid)
                unique.append(r)
        return unique[:5]

    def _extract_entities(self, task: str, policy: SearchPolicy | None = None) -> list[str]:
        """Delegate to SearchPolicy.extract_entities (simple heuristic, no LLM)."""
        p = policy or SearchPolicy()
        return p.extract_entities(task)

    async def on_task_end(
        self,
        task: str,
        result: str,
        success: bool,
        duration: float,
        steps_count: int,
        tools_used: list[str],
        quality_score: float = 0.0,
    ) -> None:
        task_id = await self.longterm.save_task(
            task=task,
            result=result,
            success=success,
            duration=duration,
            steps_count=steps_count,
            tools_used=tools_used,
            quality_score=quality_score,
        )
        # Q-5.2: create a temporal edge from the previous task to this one
        try:
            if self.working.last_task_id:
                await self.graph.add_temporal_edge(self.working.last_task_id, task_id)
            self.working.last_task_id = task_id
        except Exception:
            pass
        # Q-5.3: infer causal/entity/procedural edges in background (non-blocking)
        if self.llm:
            try:
                analyzer = CausalAnalyzer(self.graph, self.longterm)
                asyncio.create_task(
                    self._safe_analyze(analyzer, task_id, task, tools_used)
                )
            except Exception:
                pass
        # Q-5.4: extract procedural template for high-quality tasks (fire-and-forget)
        if self.llm and quality_score >= 0.8:
            try:
                asyncio.create_task(
                    self._safe_extract_template(task, tools_used, result, quality_score)
                )
            except Exception:
                pass
        # Extract personal facts from the user's original task text (not from LLM output)
        if self.llm:
            try:
                facts = await self.facts.extract_facts(task, self.llm)
                await self.facts.save_facts(facts)
            except Exception:
                pass

    async def save_reflection(
        self, agent_name: str, task: str, score: int, insight: str,
        failure_type: str | None = None,
        root_cause: str | None = None,
        corrective_action: str | None = None,
        reflection_confidence: float | None = None,
    ) -> None:
        await self.initialize()
        task_hash = hashlib.sha256(task.strip().lower().encode()).hexdigest()[:16]
        async with AsyncSessionLocal() as session:
            session.add(AgentReflection(
                id=uuid.uuid4().hex,
                agent_name=agent_name,
                task_hash=task_hash,
                score=score,
                insight=insight,
                failure_type=failure_type,
                root_cause=root_cause,
                corrective_action=corrective_action,
                reflection_confidence=reflection_confidence,
            ))
            await session.commit()

    async def get_cross_agent_insights(
        self, current_agent: str, task_text: str, limit: int = 5
    ) -> list[dict]:
        """Fetch relevant reflections from OTHER agents for context sharing (Q-7.5).

        Strategy:
        1. Query agent_reflections WHERE agent_name != current_agent
        2. Filter by: score >= 3 OR corrective_action IS NOT NULL
        3. Simple keyword overlap with task_text for relevance
        4. Order by reflection_confidence DESC, score DESC
        5. Return top-N as dicts
        """
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(AgentReflection)
                    .where(AgentReflection.agent_name != current_agent)
                    .where(
                        or_(
                            AgentReflection.score >= 3,
                            AgentReflection.corrective_action.isnot(None),
                        )
                    )
                    .order_by(AgentReflection.created_at.desc())
                    .limit(50)
                )
                result = await session.execute(stmt)
                reflections = result.scalars().all()

            if not reflections:
                return []

            # Keyword overlap scoring
            task_words = set(task_text.lower().split())
            scored: list[tuple[int, AgentReflection]] = []
            for r in reflections:
                ref_text = f"{r.insight or ''} {r.corrective_action or ''} {r.root_cause or ''}"
                ref_words = set(ref_text.lower().split())
                overlap = len(task_words & ref_words)
                if overlap >= 2:
                    scored.append((overlap, r))

            scored.sort(
                key=lambda x: (x[0], x[1].reflection_confidence or 0), reverse=True
            )

            return [
                {
                    "agent": r.agent_name,
                    "insight": r.insight or "",
                    "corrective_action": r.corrective_action or "",
                    "failure_type": r.failure_type or "",
                    "score": r.score,
                }
                for _, r in scored[:limit]
            ]
        except Exception:
            return []

    async def _safe_analyze(
        self,
        analyzer: CausalAnalyzer,
        task_id: str,
        task: str,
        tools_used: list[str],
    ) -> None:
        """Background wrapper for CausalAnalyzer — swallows all exceptions."""
        try:
            await analyzer.analyze_task(task_id, task, tools_used, self.llm)
        except Exception:
            pass

    async def _safe_extract_template(
        self,
        task: str,
        tools_used: list[str],
        result: str,
        quality_score: float,
    ) -> None:
        """Background wrapper for TemplateExtractor — swallows all exceptions."""
        try:
            await self.templates.extract_template(task, tools_used, result, quality_score, self.llm)
        except Exception:
            pass

    async def get_stats(self) -> dict:
        return await self.longterm.get_stats()