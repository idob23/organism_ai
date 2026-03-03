"""Q-6.3: confirm_with_user tool — ask human approval before critical actions."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .base import BaseTool, ToolResult

if TYPE_CHECKING:
    from src.organism.core.human_approval import HumanApproval


class ConfirmUserTool(BaseTool):

    def __init__(self, approval: "HumanApproval") -> None:
        self.approval = approval

    @property
    def name(self) -> str:
        return "confirm_with_user"

    @property
    def description(self) -> str:
        return (
            "Ask user for approval before a critical action "
            "(writing to 1C, sending documents, deleting data). "
            "Returns approved or rejected."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What will be done (shown to user for confirmation)",
                },
                "task_id": {
                    "type": "string",
                    "description": "Current task id (optional)",
                    "default": "",
                },
            },
            "required": ["description"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        description = input.get("description", "")
        task_id = input.get("task_id", "")
        if not description:
            return ToolResult(
                output="",
                error="description is required",
                exit_code=1,
            )
        try:
            approved = await self.approval.request_approval(description, task_id)
        except Exception as exc:
            return ToolResult(
                output="",
                error=f"Approval error: {type(exc).__name__}: {exc}",
                exit_code=1,
            )
        if approved:
            return ToolResult(output="User approved", exit_code=0)
        else:
            return ToolResult(
                output="User rejected or timeout",
                error="User rejected or timeout",
                exit_code=1,
            )
