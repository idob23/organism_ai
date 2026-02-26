from pathlib import Path
from typing import Any
from .base import BaseTool, ToolResult, OUTPUTS_DIR

WORKSPACE = OUTPUTS_DIR


class FileManagerTool(BaseTool):

    @property
    def name(self) -> str:
        return "file_manager"

    @property
    def description(self) -> str:
        return (
            "Read, write, and list files in the workspace. "
            "Use for saving results, reading data files, creating reports. "
            "All files are stored in data/outputs/."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "append", "list", "delete"],
                    "description": "Action to perform",
                },
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (for write/append actions)",
                },
            },
            "required": ["action"],
        }

    def _safe_path(self, path: str) -> Path:
        """Ensure path stays within workspace."""
        full = (WORKSPACE / path).resolve()
        if not str(full).startswith(str(WORKSPACE.resolve())):
            raise ValueError(f"Path '{path}' is outside workspace")
        return full

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        action = input["action"]

        try:
            if action == "list":
                files = list(WORKSPACE.rglob("*"))
                names = [str(f.relative_to(WORKSPACE)) for f in files if f.is_file()]
                return ToolResult(output="\n".join(names) if names else "(empty workspace)")

            path = self._safe_path(input.get("path", ""))

            if action == "read":
                if not path.exists():
                    return ToolResult(output="", error=f"File not found: {path.name}", exit_code=1)
                return ToolResult(output=path.read_text(encoding="utf-8"))

            elif action == "write":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(input.get("content", ""), encoding="utf-8")
                return ToolResult(output=f"Written: {path.name}")

            elif action == "append":
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(input.get("content", ""))
                return ToolResult(output=f"Appended: {path.name}")

            elif action == "delete":
                if path.exists():
                    path.unlink()
                    return ToolResult(output=f"Deleted: {path.name}")
                return ToolResult(output="", error=f"File not found: {path.name}", exit_code=1)

            return ToolResult(output="", error=f"Unknown action: {action}", exit_code=1)

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
