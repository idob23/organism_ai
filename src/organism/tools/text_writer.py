import json
import re
from typing import Any
from pathlib import Path

import structlog

from .base import BaseTool, ToolResult, OUTPUTS_DIR
from src.organism.utils.timezone import today_local

_log = structlog.get_logger(__name__)

# FIX-81: heuristic keywords for sectional mode detection
_LONG_DOC_PATTERNS_RU = [
    "\u0431\u0438\u0437\u043d\u0435\u0441-\u043f\u043b\u0430\u043d",       # бизнес-план
    "\u043e\u0442\u0447\u0451\u0442", "\u043e\u0442\u0447\u0435\u0442",     # отчёт/отчет
    "\u0434\u043e\u043a\u043b\u0430\u0434",                                 # доклад
    "\u043f\u043e\u0434\u0440\u043e\u0431\u043d",                           # подробн*
    "\u0434\u0435\u0442\u0430\u043b\u044c\u043d",                           # детальн*
    "20 \u0441\u0442\u0440\u0430\u043d\u0438\u0446",                        # 20 страниц
    "15 \u0441\u0442\u0440\u0430\u043d\u0438\u0446",                        # 15 страниц
    "10 \u0441\u0442\u0440\u0430\u043d\u0438\u0446",                        # 10 страниц
]
_LONG_DOC_PATTERNS_EN = [
    "business plan", "report", "proposal", "white paper",
    "detailed", "comprehensive", "in-depth",
    "20 pages", "15 pages", "10 pages",
]
_SECTION_COUNT_RE = re.compile(r'(\d+)\.\s')


def _is_long_document(prompt: str) -> bool:
    """Heuristic: detect if the prompt requests a long multi-section document."""
    lower = prompt.lower()
    for pat in _LONG_DOC_PATTERNS_RU + _LONG_DOC_PATTERNS_EN:
        if pat in lower:
            return True
    # Count numbered section mentions (e.g. "1. Резюме 2. Описание 3. ...")
    if len(_SECTION_COUNT_RE.findall(prompt)) > 5:
        return True
    return False


class TextWriterTool(BaseTool):

    @property
    def name(self) -> str:
        return "text_writer"

    @property
    def description(self) -> str:
        return (
            "Write long-form text content (articles, proposals, reports, letters) and save to file. "
            "Use this for any writing task that needs to be saved. "
            "Generates content via AI and saves directly  no JSON size limits."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to write  full instructions"},
                "filename": {"type": "string", "description": "File to save to (e.g. report.md)"},
                "language": {"type": "string", "default": "ru", "description": "Language: ru or en"},
            },
            "required": ["prompt", "filename"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        prompt: str = input["prompt"]
        filename: str = input["filename"]
        language: str = input.get("language", "ru")
        user_context: str = input.get("user_context", "")

        from src.organism.llm.claude import ClaudeProvider
        from src.organism.llm.base import Message

        llm = ClaudeProvider()

        # FIX-83: inject current date so LLM uses correct year
        _today = today_local()
        system = (
            f"\u0421\u0435\u0433\u043e\u0434\u043d\u044f: {_today}. "
            "\u0422\u044b \u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 "
            "\u043a\u043e\u043f\u0438\u0440\u0430\u0439\u0442\u0435\u0440 \u0438 "
            "\u0431\u0438\u0437\u043d\u0435\u0441-\u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u043d\u0442. "
            "\u041f\u0438\u0448\u0438 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u043e, "
            "\u0443\u0431\u0435\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e, "
            "\u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e. "
            "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 markdown-\u0444\u043e\u0440\u043c\u0430\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435. "
            "\u041e\u0442\u0432\u0435\u0447\u0430\u0439 \u0442\u043e\u043b\u044c\u043a\u043e \u0442\u0435\u043a\u0441\u0442\u043e\u043c "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430, \u0431\u0435\u0437 \u0432\u0441\u0442\u0443\u043f\u043b\u0435\u043d\u0438\u0439 "
            "\u0442\u0438\u043f\u0430 '\u0412\u043e\u0442 \u0442\u0435\u043a\u0441\u0442:'."
        ) if language == "ru" else (
            f"Today: {_today}. "
            "You are a professional copywriter and business consultant. "
            "Write structured, persuasive, professional content in Markdown."
        )
        if user_context:
            system = system + "\n" + user_context

        # FIX-81: choose generation mode
        if _is_long_document(prompt):
            content = await self._generate_sectional(llm, prompt, system, language)
            if content is None:
                # Fallback to single mode
                content = await self._generate_single(llm, prompt, system)
        else:
            content = await self._generate_single(llm, prompt, system)

        try:
            filepath = OUTPUTS_DIR / Path(filename).name
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                output=content,
                exit_code=0,
            )
        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)

    async def _generate_single(self, llm, prompt: str, system: str) -> str:
        """Original single-call generation."""
        from src.organism.llm.base import Message

        response = await llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=system,
            model_tier="balanced",
            max_tokens=8000,
        )
        return response.content.strip()

    async def _generate_sectional(self, llm, prompt: str, system: str,
                                  language: str) -> str | None:
        """FIX-81: Sectional generation for long documents.

        Phase 1: Haiku generates outline (JSON array of sections).
        Phase 2: Sonnet generates each section with full outline context.
        Phase 3: Merge all sections.
        Returns None on critical failure (caller falls back to single mode).
        """
        from src.organism.llm.base import Message

        # Phase 1: Outline via Haiku
        outline_system = (
            "\u0422\u044b \u0430\u0440\u0445\u0438\u0442\u0435\u043a\u0442\u043e\u0440 "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u043e\u0432. "
            "\u0421\u043e\u0437\u0434\u0430\u0439 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0443 (outline) "
            "\u0434\u043b\u044f \u0437\u0430\u043f\u0440\u043e\u0448\u0435\u043d\u043d\u043e\u0433\u043e "
            "\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430. "
            "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON-\u043c\u0430\u0441\u0441\u0438\u0432 "
            "\u0441\u0435\u043a\u0446\u0438\u0439. "
            "\u041a\u0430\u0436\u0434\u0430\u044f \u0441\u0435\u043a\u0446\u0438\u044f: "
            "{\"title\": \"\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0440\u0430\u0437\u0434\u0435\u043b\u0430\", "
            "\"brief\": \"\u0427\u0442\u043e \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c "
            "\u0432 \u0440\u0430\u0437\u0434\u0435\u043b\u0435, 1-2 \u043f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f\"}. "
            "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0441\u0435\u043a\u0446\u0438\u0439: "
            "8-15 \u0432 \u0437\u0430\u0432\u0438\u0441\u0438\u043c\u043e\u0441\u0442\u0438 "
            "\u043e\u0442 \u0441\u043b\u043e\u0436\u043d\u043e\u0441\u0442\u0438 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430. "
            "\u0412\u0435\u0440\u043d\u0438 \u0422\u041e\u041b\u042c\u041a\u041e JSON, "
            "\u0431\u0435\u0437 markdown, \u0431\u0435\u0437 \u043f\u043e\u044f\u0441\u043d\u0435\u043d\u0438\u0439."
        ) if language == "ru" else (
            "You are a document architect. Create an outline for the requested document. "
            "Return ONLY a JSON array of sections. Each section: "
            "{\"title\": \"Section title\", \"brief\": \"What should be in the section, 1-2 sentences\"}. "
            "Number of sections: 8-15 depending on document complexity. "
            "Return ONLY JSON, no markdown, no explanations."
        )

        try:
            outline_resp = await llm.complete(
                messages=[Message(role="user", content=prompt)],
                system=outline_system,
                model_tier="fast",
                max_tokens=1000,
                temperature=0.3,
            )
        except Exception as e:
            _log.warning("text_writer.sectional: outline failed: %s", e)
            return None

        # FIX-82: log raw Haiku response for debugging
        raw_outline = outline_resp.content.strip()
        _log.info("text_writer.sectional.outline_raw", raw=raw_outline[:500])

        # Parse outline JSON
        sections = self._parse_outline(raw_outline)
        if not sections:
            _log.warning("text_writer.sectional: outline parse failed, fallback to single")
            return None

        _log.info("text_writer.sectional: outline has %d sections", len(sections))

        # Build outline text for context in each section call
        outline_text = "\n".join(
            f"{i + 1}. {s['title']}: {s['brief']}" for i, s in enumerate(sections)
        )

        # Phase 2: Generate each section
        section_texts: list[str] = []
        failed_count = 0

        for idx, section in enumerate(sections):
            # Build previous sections summary (first 200 chars each)
            prev_summary = ""
            if section_texts:
                prev_parts = []
                for prev_idx, prev_text in enumerate(section_texts):
                    prev_parts.append(
                        f"{sections[prev_idx]['title']}: {prev_text[:200]}..."
                    )
                prev_summary = "\n".join(prev_parts)

            section_prompt = (
                f"\u041e\u0441\u043d\u043e\u0432\u043d\u0430\u044f "
                f"\u0437\u0430\u0434\u0430\u0447\u0430: {prompt}\n\n"
                f"\u041f\u043e\u043b\u043d\u0430\u044f \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430 "
                f"\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430:\n{outline_text}\n\n"
                f"\u0421\u0435\u0439\u0447\u0430\u0441 \u043d\u0430\u043f\u0438\u0448\u0438 "
                f"\u0422\u041e\u041b\u042c\u041a\u041e \u0440\u0430\u0437\u0434\u0435\u043b: "
                f"{section['title']}\n"
                f"\u0421\u043e\u0434\u0435\u0440\u0436\u0430\u043d\u0438\u0435 "
                f"\u0440\u0430\u0437\u0434\u0435\u043b\u0430: {section['brief']}\n\n"
            ) if language == "ru" else (
                f"Main task: {prompt}\n\n"
                f"Full document structure:\n{outline_text}\n\n"
                f"Now write ONLY the section: {section['title']}\n"
                f"Section content: {section['brief']}\n\n"
            )

            if prev_summary:
                if language == "ru":
                    section_prompt += (
                        f"\u041f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0438\u0435 "
                        f"\u0440\u0430\u0437\u0434\u0435\u043b\u044b "
                        f"(\u0434\u043b\u044f \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u0430 "
                        f"\u0441\u0432\u044f\u0437\u043d\u043e\u0441\u0442\u0438):\n{prev_summary}\n\n"
                    )
                else:
                    section_prompt += (
                        f"Previous sections (for coherence context):\n{prev_summary}\n\n"
                    )

            if language == "ru":
                section_prompt += (
                    "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 markdown. "
                    "\u041d\u0430\u0447\u043d\u0438 \u0441 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430 "
                    f"## {section['title']}. "
                    "\u041f\u0438\u0448\u0438 \u043f\u043e\u0434\u0440\u043e\u0431\u043d\u043e "
                    "\u0438 \u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e. "
                    "\u0422\u043e\u043b\u044c\u043a\u043e \u0442\u0435\u043a\u0441\u0442 "
                    "\u0440\u0430\u0437\u0434\u0435\u043b\u0430, \u0431\u0435\u0437 "
                    "\u043c\u0435\u0442\u0430-\u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0435\u0432."
                )
            else:
                section_prompt += (
                    f"Use markdown. Start with heading ## {section['title']}. "
                    "Write in detail, professionally. Only the section text, no meta-commentary."
                )

            try:
                section_resp = await llm.complete(
                    messages=[Message(role="user", content=section_prompt)],
                    system=system,
                    model_tier="balanced",
                    max_tokens=2000,
                    temperature=0.5,
                )
                section_texts.append(section_resp.content.strip())
            except Exception as e:
                _log.warning("text_writer.sectional: section %d/%d failed: %s",
                             idx + 1, len(sections), e)
                failed_count += 1

        # Check if enough sections were generated
        if failed_count > len(sections) / 2:
            _log.warning("text_writer.sectional: %d/%d sections failed, fallback to single",
                         failed_count, len(sections))
            return None

        # Phase 3: Merge
        full_content = "\n\n".join(section_texts)
        _log.info("text_writer.sectional: merged %d sections, %d chars",
                  len(section_texts), len(full_content))
        return full_content

    def _parse_outline(self, raw: str) -> list[dict] | None:
        """FIX-82: Parse Haiku outline response into list of {title, brief} dicts.

        Fallback chain:
        1. Strip markdown fences + direct JSON parse
        2. Regex extraction of [...] from mixed text
        3. Parse numbered/bulleted list into sections
        """
        text = raw.strip()

        # --- Level 1: Strip markdown fences, try direct JSON parse ---
        text_clean = re.sub(r'^```(?:json)?\s*', '', text)
        text_clean = re.sub(r'\s*```$', '', text_clean)
        text_clean = text_clean.strip()

        try:
            data = json.loads(text_clean)
            if isinstance(data, list) and len(data) >= 3:
                result = self._normalize_sections(data)
                if result:
                    return result
        except (json.JSONDecodeError, TypeError):
            pass

        # --- Level 2: Extract JSON array from mixed text ---
        match = re.search(r'\[.*\]', text_clean, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list) and len(data) >= 3:
                    result = self._normalize_sections(data)
                    if result:
                        return result
            except (json.JSONDecodeError, TypeError):
                pass

        # --- Level 3: Parse numbered/bulleted list ---
        lines = [l.strip() for l in raw.split('\n') if l.strip()]
        sections = []
        for line in lines:
            # Remove numbering: "1. ", "1) ", "- ", "* ", "## "
            cleaned = re.sub(r'^(?:\d+[\.\)]\s*|[-*]\s*|#{1,3}\s*)', '', line).strip()
            if cleaned and len(cleaned) > 3:
                sections.append({"title": cleaned, "brief": cleaned})

        if len(sections) >= 3:
            _log.info("text_writer.sectional.outline_parsed_from_list", count=len(sections))
            return sections

        return None

    @staticmethod
    def _normalize_sections(data: list) -> list[dict] | None:
        """Normalize parsed JSON sections to ensure {title, brief} format."""
        sections = []
        for item in data:
            if isinstance(item, dict):
                title = item.get("title") or item.get("section") or item.get("name", "")
                brief = (item.get("brief") or item.get("description")
                         or item.get("content", title))
                if title:
                    sections.append({"title": str(title), "brief": str(brief)})
            elif isinstance(item, str):
                sections.append({"title": item, "brief": item})
        return sections if len(sections) >= 3 else None
