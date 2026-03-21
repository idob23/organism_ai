import re
import time
from pathlib import Path

from .base import BaseAgent, AgentResult, TemperatureLocked
from src.organism.core.loop import CoreLoop
from src.organism.llm.base import Message
from src.organism.tools.base import OUTPUTS_DIR

# Tasks shorter than this go straight to single-pass (outline overhead not worth it)
_SIMPLE_THRESHOLD = 50


def _extract_filename(task: str) -> str:
    m = re.search(
        r"(\w[\w\-]+\.(?:md|txt|docx|html|csv|xlsx|json|pdf|pptx))",
        task, re.IGNORECASE,
    )
    return m.group(1) if m else "output.md"


class WriterAgent(BaseAgent):

    temperature = 0.7      # creative — varied phrasing, richer vocabulary
    max_iterations = 3

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "Generates texts, articles, reports, commercial proposals. Saves to files when asked."

    @property
    def tools(self) -> list[str]:
        return ["text_writer", "file_manager", "pptx_creator"]

    # ------------------------------------------------------------------
    # Phase helpers
    # ------------------------------------------------------------------

    async def _outline(self, task: str) -> str | None:
        """Phase 1: Haiku generates document structure (~200 tokens, temp=0.3)."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=task)],
                system=(
                    "You are a document architect. "
                    "Create a clear outline for the requested document. "
                    "Return ONLY section headers and brief bullet points. "
                    "No full sentences, no introductory remarks."
                ),
                model_tier="fast",
                max_tokens=200,
                temperature=0.3,
            )
            outline = resp.content.strip()
            return outline if outline else None
        except Exception:
            return None

    async def _draft(self, task: str, outline: str) -> str | None:
        """Phase 2: Sonnet writes the full draft from the outline (temp=0.7)."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=(
                    f"Task: {task}\n\n"
                    f"Document outline:\n{outline}\n\n"
                    "Write the complete document following this outline exactly. "
                    "Use Markdown formatting. Be thorough and professional. "
                    "Output only the document text, no meta-commentary."
                ))],
                system=(
                    "You are a professional copywriter and business consultant. "
                    "Write structured, persuasive, professional content in Markdown. "
                    "Match the language of the user task (Russian or English)."
                ),
                model_tier="balanced",
                max_tokens=4000,
                temperature=0.7,
            )
            draft = resp.content.strip()
            return draft if draft else None
        except Exception:
            return None

    async def _polish(self, task: str, draft: str) -> str:
        """Phase 3: Sonnet reviews and polishes the draft (temp=0.2). Returns draft on failure."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=(
                    f"Original task: {task}\n\n"
                    f"Draft:\n{draft}\n\n"
                    "Review and polish this document: fix grammar, improve flow and readability, "
                    "ensure it fully addresses the task. "
                    "Return ONLY the polished document, no commentary."
                ))],
                system=(
                    "You are an editor and proofreader. "
                    "Improve the text while preserving its structure and content. "
                    "Match the language of the draft."
                ),
                model_tier="balanced",
                max_tokens=4000,
                temperature=0.2,
            )
            polished = resp.content.strip()
            return polished if polished else draft
        except Exception:
            return draft  # phase 3 failure is non-fatal — keep draft

    async def _three_phase(self, task: str) -> str | None:
        """Run outline → draft → polish. Returns final text or None if critical phase fails."""
        outline = await self._outline(task)
        if not outline:
            return None

        draft = await self._draft(task, outline)
        if not draft:
            return None

        return await self._polish(task, draft)

    def _save(self, task: str, content: str) -> None:
        """Write final content to OUTPUTS_DIR (mirrors text_writer save logic)."""
        try:
            filepath = OUTPUTS_DIR / Path(_extract_filename(task)).name
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass  # save failure doesn't fail the task

    # ------------------------------------------------------------------
    # Fallback: single-pass via CoreLoop (original behavior)
    # ------------------------------------------------------------------

    async def _single_pass(self, task: str, start: float) -> AgentResult:
        try:
            llm = TemperatureLocked(self.llm, self.temperature)
            loop = CoreLoop(llm, self.registry)
            loop_result = await loop.run(task, skip_orchestrator=True)
            return AgentResult(
                agent=self.name, task=task,
                output=loop_result.answer or loop_result.error or "",
                success=loop_result.success,
                duration=time.time() - start,
                error=loop_result.error or "",
            )
        except Exception as e:
            return AgentResult(
                agent=self.name, task=task, output="",
                success=False, duration=time.time() - start, error=str(e),
            )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def _run_impl(self, task: str, start: float) -> AgentResult:
        # Short/simple tasks — single-pass is faster and sufficient
        if len(task.strip()) < _SIMPLE_THRESHOLD:
            return await self._single_pass(task, start)

        try:
            polished = await self._three_phase(task)
            if polished is None:
                return await self._single_pass(task, start)

            self._save(task, polished)
            return AgentResult(
                agent=self.name, task=task,
                output=polished, success=True,
                duration=time.time() - start,
            )
        except Exception:
            return await self._single_pass(task, start)

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        # Q-7.5: cross-agent knowledge sharing
        effective_task = await self._enrich_with_cross_insights(task)
        result = await self._run_impl(effective_task, start)
        await self._save_reflection(task, result)
        return result
