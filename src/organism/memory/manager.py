import asyncio
import hashlib
import uuid
from .longterm import LongTermMemory
from .working import WorkingMemory
from .user_facts import UserFactsExtractor
from .graph import MemoryGraph
from .causal_analyzer import CausalAnalyzer
from .templates import TemplateExtractor
from .database import init_db, AgentReflection, AsyncSessionLocal
from src.organism.llm.base import LLMProvider


class MemoryManager:

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self.longterm = LongTermMemory()
        self.working = WorkingMemory()
        self.facts = UserFactsExtractor()
        self.graph = MemoryGraph()
        self.templates = TemplateExtractor()
        self.llm = llm
        self._initialized = False

    async def initialize(self) -> None:
        if not self._initialized:
            await init_db()
            self._initialized = True

    async def on_task_start(self, task: str) -> list[dict]:
        self.working.clear()
        self.working.task = task

        # Search for similar past tasks (with LLM reranking when available)
        similar = await self.longterm.search_similar(task, limit=3, llm=self.llm)
        return similar

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
        self, agent_name: str, task: str, score: int, insight: str
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
            ))
            await session.commit()

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