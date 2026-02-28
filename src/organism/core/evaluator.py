import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.organism.llm.base import LLMProvider, Message
from src.organism.tools.base import ToolResult

EVALUATOR_PROMPT = Path("config/prompts/evaluator.txt").read_text(encoding="utf-8")


@dataclass
class EvalResult:
    success: bool
    reason: str
    retry_hint: str = ""
    quality_score: float = 0.0  # 0.0 - 1.0


class Evaluator:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def evaluate(
        self,
        task: str,
        step_description: str,
        result: ToolResult,
    ) -> EvalResult:
        # Fast path: clear failures
        if result.exit_code == -1:
            return EvalResult(
                success=False,
                reason=result.error,
                retry_hint="Fix the code to avoid timeout or system errors.",
                quality_score=0.0,
            )

        if result.exit_code != 0 and result.error:
            return EvalResult(
                success=False,
                reason=f"Code exited with code {result.exit_code}",
                retry_hint=f"Fix this error: {result.error[:300]}",
                quality_score=0.1,
            )

        # Fast path: clear success with substantial output
        if result.exit_code == 0 and result.output and len(result.output.strip()) > 200:
            # Still call LLM for quality assessment but with high baseline
            pass

        # LLM evaluation for quality assessment
        prompt = (
            f"Task: {task}\n"
            f"Step: {step_description}\n"
            f"Exit code: {result.exit_code}\n"
            f"Output: {result.output[:800] if result.output else '(empty)'}\n"
            f"Stderr: {result.error[:300] if result.error else '(none)'}"
        )

        response = await self.llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=EVALUATOR_PROMPT,
            model_tier="fast",
        )

        return self._parse(response.content)

    def _parse(self, text: str) -> EvalResult:
        try:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                data = json.loads(match.group(0))
                quality = data.get("quality_score", 0.0)
                # Clamp quality_score to valid range
                quality = max(0.0, min(1.0, float(quality)))

                return EvalResult(
                    success=bool(data.get("success", False)),
                    reason=data.get("reason", ""),
                    retry_hint=data.get("retry_hint", ""),
                    quality_score=quality,
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        # Fallback: look for true/false in response
        text_lower = text.lower()
        success = '"success": true' in text_lower or "success: true" in text_lower
        return EvalResult(
            success=success,
            reason=text[:200],
            retry_hint="" if success else "Review the output and fix the code.",
            quality_score=0.8 if success else 0.2,
        )