import json
import time
import uuid
from pathlib import Path
from datetime import datetime
from config.settings import settings


class Logger:

    def __init__(self) -> None:
        self.log_dir = Path(settings.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, event: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"{today}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def log_task_start(self, task_id: str, task: str) -> None:
        self._log({
            "event": "task_start",
            "task_id": task_id,
            "task": task,
            "timestamp": time.time(),
        })

    def log_step(
        self,
        task_id: str,
        step_id: int,
        tool: str,
        success: bool,
        duration: float,
        tokens: int = 0,
        error: str = "",
    ) -> None:
        self._log({
            "event": "step",
            "task_id": task_id,
            "step_id": step_id,
            "tool": tool,
            "success": success,
            "duration": round(duration, 2),
            "tokens": tokens,
            "error": error,
            "timestamp": time.time(),
        })

    def log_task_end(
        self,
        task_id: str,
        success: bool,
        duration: float,
        total_tokens: int,
    ) -> None:
        self._log({
            "event": "task_end",
            "task_id": task_id,
            "success": success,
            "duration": round(duration, 2),
            "total_tokens": total_tokens,
            "timestamp": time.time(),
        })
