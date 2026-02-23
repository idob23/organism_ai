from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    task: str = ""
    steps_results: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def add_step_result(self, step_id: int, tool: str, output: str, success: bool) -> None:
        self.steps_results.append({
            "step_id": step_id,
            "tool": tool,
            "output": output[:500],
            "success": success,
        })

    def get_context_summary(self) -> str:
        if not self.steps_results:
            return ""
        lines = ["Previous steps results:"]
        for r in self.steps_results:
            status = "OK" if r["success"] else "FAIL"
            lines.append(f"  Step {r['step_id']} [{r['tool']}] {status}: {r['output'][:200]}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.steps_results.clear()
        self.context.clear()
