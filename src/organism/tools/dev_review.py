import subprocess
import sys
from pathlib import Path
from typing import Any

from config.settings import settings
from src.organism.logging.error_handler import get_logger
from .base import BaseTool, ToolResult

_log = get_logger("tools.dev_review")

ROOT = Path(__file__).resolve().parent.parent.parent.parent

_SCOPE_TEMPLATES: dict[str, list[str]] = {
    "memory": ["reviewer_memory"],
    "core": ["reviewer_core"],
    "tools": ["reviewer_tools"],
    "channels": ["reviewer_channels"],
    "agents": ["reviewer_agents"],
    "infra": ["reviewer_infra"],
    "docs": ["reviewer_docs"],
    "quality": ["reviewer_quality"],
    "self_improvement": ["reviewer_self_improvement"],
    "all": [
        "reviewer_memory", "reviewer_core", "reviewer_tools",
        "reviewer_channels", "reviewer_agents", "reviewer_infra",
        "reviewer_docs", "reviewer_quality", "reviewer_self_improvement",
        "review_coordinator",
    ],
}


class DevReviewTool(BaseTool):

    @property
    def name(self) -> str:
        return "dev_review"

    @property
    def description(self) -> str:
        return (
            "Run code review on the codebase (dev-only). "
            "Runs deterministic health checks, loads review role templates, "
            "and returns a structured review instruction.\n"
            "Scopes: memory, core, tools, channels, agents, infra, "
            "docs, quality, self_improvement, all."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": list(_SCOPE_TEMPLATES.keys()),
                    "description": "Review scope (subsystem or 'all')",
                },
                "focus": {
                    "type": "string",
                    "default": "",
                    "description": "Optional focus area or question",
                },
            },
            "required": ["scope"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        if not settings.dev_mode:
            return ToolResult(
                output="",
                error="dev_review requires DEV_MODE=true",
                exit_code=1,
            )

        scope: str = input.get("scope", "all")
        focus: str = input.get("focus", "")

        if scope not in _SCOPE_TEMPLATES:
            return ToolResult(
                output="",
                error=f"Unknown scope '{scope}'. "
                      f"Valid: {', '.join(_SCOPE_TEMPLATES)}",
                exit_code=1,
            )

        # 1. Run code_health.py
        health_report = self._run_health_check()

        # 2. Load role templates
        template_names = _SCOPE_TEMPLATES[scope]
        templates = self._load_templates(template_names)

        # 3. Build review instruction
        parts = [
            "=== CODE HEALTH REPORT ===",
            health_report,
            "",
            "=== REVIEW SCOPE: {} ===".format(scope),
        ]

        if templates:
            parts.append("")
            parts.append("=== ROLE CONTEXT ===")
            parts.append(templates)

        if focus:
            parts.append("")
            parts.append(f"=== FOCUS: {focus} ===")

        parts.append("")
        parts.append(
            "Review the codebase using /repo/ paths in code_executor. "
            "Start with health report issues, then apply role guidelines."
        )

        return ToolResult(output="\n".join(parts))

    def _run_health_check(self) -> str:
        """Run scripts/code_health.py and return its output."""
        script = ROOT / "scripts" / "code_health.py"
        if not script.exists():
            return "[code_health.py not found]"
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(ROOT),
            )
            return (result.stdout + result.stderr).strip()
        except subprocess.TimeoutExpired:
            return "[code_health.py timed out]"
        except Exception as e:
            return f"[code_health.py error: {e}]"

    def _load_templates(self, names: list[str]) -> str:
        """Load and concatenate role template files."""
        roles_dir = ROOT / "config" / "dev_roles"
        parts = []
        for name in names:
            path = roles_dir / f"{name}.md"
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content and not content.startswith("# "):
                    parts.append(f"--- {name} ---\n{content}")
                elif content:
                    parts.append(content)
        return "\n\n".join(parts)
