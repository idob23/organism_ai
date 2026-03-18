"""SCHED-1b: ManageScheduleTool \u2014 natural language schedule management.

Allows LLM to list/create/delete/enable/disable scheduled jobs via tool calls
instead of requiring slash commands.
"""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any, TYPE_CHECKING

from config.settings import settings
from src.organism.logging.error_handler import get_logger
from .base import BaseTool, ToolResult

if TYPE_CHECKING:
    from src.organism.core.scheduler import ProactiveScheduler

_log = get_logger("tools.manage_schedule")

_WEEKDAY_NAMES = {
    0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
    4: "Fri", 5: "Sat", 6: "Sun",
}


class ManageScheduleTool(BaseTool):

    def __init__(self) -> None:
        self._scheduler: ProactiveScheduler | None = None

    # \u2500\u2500 Dependency injection (setter pattern) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def set_scheduler(self, scheduler: ProactiveScheduler) -> None:
        self._scheduler = scheduler

    # \u2500\u2500 BaseTool interface \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @property
    def name(self) -> str:
        return "manage_schedule"

    @property
    def description(self) -> str:
        return (
            "Manage scheduled tasks \u2014 list current jobs, create a new recurring task, "
            "delete a user-created task, enable or disable a task. "
            "Use when the user wants to set up recurring/periodic tasks, "
            "check what's scheduled, or change schedule settings. "
            "All times must be in UTC. The scheduler checks every 30 seconds. "
            "Results of scheduled tasks are automatically delivered to the user's "
            "current chat \u2014 no additional setup needed."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "delete", "enable", "disable"],
                    "description": "Action to perform",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Job name (for create/delete/enable/disable). "
                        "Must be unique, use snake_case."
                    ),
                },
                "task_text": {
                    "type": "string",
                    "description": (
                        "Task description \u2014 what the agent should do when this job runs "
                        "(for 'create' action)"
                    ),
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["daily", "weekly", "interval"],
                    "description": "Schedule type (for 'create' action)",
                },
                "time_utc": {
                    "type": "string",
                    "description": "Time in UTC, format 'HH:MM' (for 'create' with daily/weekly)",
                },
                "weekday": {
                    "type": "integer",
                    "description": (
                        "Day of week: 0=Monday, 1=Tuesday, ..., 6=Sunday "
                        "(for 'create' with weekly)"
                    ),
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Interval in minutes (for 'create' with schedule_type='interval')",
                },
            },
            "required": ["action"],
        }

    # \u2500\u2500 Execute \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        if not self._scheduler:
            return ToolResult(output="", error="Scheduler not configured", exit_code=1)

        action = input.get("action", "")

        if action == "list":
            return self._action_list()
        elif action == "create":
            return await self._action_create(input)
        elif action == "delete":
            return await self._action_delete(input)
        elif action == "enable":
            return await self._action_set_enabled(input, True)
        elif action == "disable":
            return await self._action_set_enabled(input, False)
        else:
            return ToolResult(
                output="",
                error=f"Unknown action: {action}. "
                      "Valid: list, create, delete, enable, disable",
                exit_code=1,
            )

    # \u2500\u2500 Action handlers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _action_list(self) -> ToolResult:
        jobs = self._scheduler.list_jobs()
        if not jobs:
            return ToolResult(
                output="\u0417\u0430\u043f\u043b\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0445 "
                       "\u0437\u0430\u0434\u0430\u0447 \u043d\u0435\u0442.",
                error="", exit_code=0,
            )
        lines = [
            "\u0417\u0430\u043f\u043b\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 "
            "\u0437\u0430\u0434\u0430\u0447\u0438:"
        ]
        for job in jobs:
            schedule_desc = self._format_schedule(job)
            status = "\u2705" if job.enabled else "\u274c"
            last = job.last_run.strftime("%Y-%m-%d %H:%M UTC") if job.last_run else "never"
            lines.append(
                f"- {job.name} [{schedule_desc}] {status} "
                f"(last run: {last})"
            )
            lines.append(f"  task: {job.task_text[:100]}")
        return ToolResult(output="\n".join(lines), error="", exit_code=0)

    async def _action_create(self, input: dict[str, Any]) -> ToolResult:
        from src.organism.core.scheduler import ScheduledJob

        name = input.get("name", "").strip()
        task_text = input.get("task_text", "").strip()
        schedule_type = input.get("schedule_type", "").strip()
        time_utc = input.get("time_utc", "").strip()
        weekday = input.get("weekday")
        interval_minutes = input.get("interval_minutes")

        if not name:
            return ToolResult(output="", error="'name' is required", exit_code=1)
        if not task_text:
            return ToolResult(output="", error="'task_text' is required", exit_code=1)
        if schedule_type not in ("daily", "weekly", "interval"):
            return ToolResult(
                output="",
                error="'schedule_type' must be daily, weekly, or interval",
                exit_code=1,
            )

        tod = None
        if schedule_type in ("daily", "weekly"):
            if not time_utc:
                return ToolResult(
                    output="",
                    error="'time_utc' (HH:MM) is required for daily/weekly",
                    exit_code=1,
                )
            try:
                parts = time_utc.split(":")
                tod = dt_time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                return ToolResult(
                    output="",
                    error=f"Invalid time format: '{time_utc}'. Use HH:MM",
                    exit_code=1,
                )

        if schedule_type == "weekly":
            if weekday is None or not (0 <= weekday <= 6):
                return ToolResult(
                    output="",
                    error="'weekday' (0=Mon..6=Sun) is required for weekly",
                    exit_code=1,
                )

        if schedule_type == "interval":
            if not interval_minutes or interval_minutes < 1:
                return ToolResult(
                    output="",
                    error="'interval_minutes' (>0) is required for interval",
                    exit_code=1,
                )

        job = ScheduledJob(
            name=name,
            task_text=task_text,
            schedule_type=schedule_type,
            time_of_day=tod,
            weekday=weekday,
            interval_minutes=interval_minutes,
            enabled=True,
            artel_id=settings.artel_id,
        )

        await self._scheduler.create_job(job)
        schedule_desc = self._format_schedule(job)
        return ToolResult(
            output=(
                f"\u0417\u0430\u0434\u0430\u0447\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430: "
                f"{name} [{schedule_desc}]\n"
                f"task: {task_text[:200]}\n"
                "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b "
                "\u0431\u0443\u0434\u0443\u0442 "
                "\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438 "
                "\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u044b "
                "\u0432 \u044d\u0442\u043e\u0442 \u0447\u0430\u0442."
            ),
            error="", exit_code=0,
        )

    async def _action_delete(self, input: dict[str, Any]) -> ToolResult:
        name = input.get("name", "").strip()
        if not name:
            return ToolResult(output="", error="'name' is required", exit_code=1)

        ok = await self._scheduler.delete_user_job(name)
        if not ok:
            return ToolResult(
                output="",
                error=f"Job not found or is a system job: {name}",
                exit_code=1,
            )
        return ToolResult(
            output=f"\u0417\u0430\u0434\u0430\u0447\u0430 \u0443\u0434\u0430\u043b\u0435\u043d\u0430: {name}",
            error="", exit_code=0,
        )

    async def _action_set_enabled(self, input: dict[str, Any], enabled: bool) -> ToolResult:
        name = input.get("name", "").strip()
        if not name:
            return ToolResult(output="", error="'name' is required", exit_code=1)

        ok = await self._scheduler.set_job_enabled(name, enabled)
        if not ok:
            return ToolResult(
                output="",
                error=f"Job not found: {name}",
                exit_code=1,
            )
        state = "\u0432\u043a\u043b\u044e\u0447\u0435\u043d\u0430" if enabled else "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u0430"
        return ToolResult(
            output=f"\u0417\u0430\u0434\u0430\u0447\u0430 {name}: {state}",
            error="", exit_code=0,
        )

    # \u2500\u2500 Helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @staticmethod
    def _format_schedule(job) -> str:
        if job.schedule_type == "daily" and job.time_of_day:
            return f"daily {job.time_of_day.hour:02d}:{job.time_of_day.minute:02d} UTC"
        elif job.schedule_type == "weekly" and job.time_of_day and job.weekday is not None:
            day = _WEEKDAY_NAMES.get(job.weekday, str(job.weekday))
            return f"weekly {day} {job.time_of_day.hour:02d}:{job.time_of_day.minute:02d} UTC"
        elif job.schedule_type == "interval" and job.interval_minutes:
            return f"every {job.interval_minutes}min"
        return job.schedule_type
