"""Q-4.3: Memory commands /remember, /forget, /profile, /style, /stats."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.organism.memory.manager import MemoryManager

VALID_STYLES = {"formal", "informal", "technical", "brief"}

HELP_TEXT = (
    "Available commands:\n"
    "  /remember <key> <value>  \u2014 save a personal fact\n"
    "  /forget <key>            \u2014 delete a fact by key\n"
    "  /profile                 \u2014 show all saved personal facts\n"
    "  /history <key>           \u2014 show change history for a fact\n"
    "  /style <style>           \u2014 set writing style (formal/informal/technical/brief)\n"
    "  /stats                   \u2014 show system statistics\n"
    "  /improve [days]          \u2014 run auto-improvement cycle (default: last 7 days)\n"
    "  /prompts                 \u2014 show active prompt versions and quality stats\n"
    "  /schedule                \u2014 show scheduled tasks\n"
    "  /schedule_enable <name>  \u2014 enable a scheduled task\n"
    "  /schedule_disable <name> \u2014 disable a scheduled task\n"
    "  /help                    \u2014 show this help\n"
)


class CommandHandler:

    def __init__(self, scheduler=None) -> None:
        self.scheduler = scheduler

    def is_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    async def handle(self, text: str, memory: "MemoryManager | None") -> str:
        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/help":
            return HELP_TEXT

        # Schedule commands — no memory required
        if cmd == "/schedule":
            return self._handle_schedule()
        elif cmd == "/schedule_enable":
            return self._handle_schedule_toggle(parts, enable=True)
        elif cmd == "/schedule_disable":
            return self._handle_schedule_toggle(parts, enable=False)

        if memory is None:
            return "Memory not available (DATABASE_URL not configured)."

        try:
            await memory.initialize()
        except Exception as e:
            return f"Memory error: {e}"

        if cmd == "/remember":
            return await self._handle_remember(parts, memory)
        elif cmd == "/forget":
            return await self._handle_forget(parts, memory)
        elif cmd == "/profile":
            return await self._handle_profile(memory)
        elif cmd == "/history":
            return await self._handle_history(parts, memory)
        elif cmd == "/style":
            return await self._handle_style(parts, memory)
        elif cmd == "/stats":
            return await self._handle_stats(memory)
        elif cmd == "/improve":
            return await self._handle_improve(parts, memory)
        elif cmd == "/prompts":
            return await self._handle_prompts(memory)
        else:
            return f"Unknown command: {cmd}\n\n{HELP_TEXT}"

    async def _handle_remember(self, parts: list[str], memory: "MemoryManager") -> str:
        """Save a personal fact: /remember <key> <value>."""
        if len(parts) < 3:
            return "Usage: /remember <key> <value>\nExample: /remember name Igor"
        key = parts[1].lower().strip()
        value = parts[2].strip()
        if not key or not value:
            return "Key and value must not be empty."
        await memory.facts.save_facts([{"fact_type": key, "fact_value": value}])
        return f"Saved: {key} = {value}"

    async def _handle_forget(self, parts: list[str], memory: "MemoryManager") -> str:
        """Retire the active fact for a key: /forget <key>."""
        if len(parts) < 2:
            return "Usage: /forget <key>\nExample: /forget name"
        key = parts[1].lower().strip()
        from src.organism.memory.database import UserProfile, AsyncSessionLocal
        from sqlalchemy import select
        from datetime import datetime, timezone
        async with AsyncSessionLocal() as session:
            stmt = (
                select(UserProfile)
                .where(UserProfile.key == key)
                .where(UserProfile.valid_until.is_(None))
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            if existing:
                existing.valid_until = datetime.now(timezone.utc)
                await session.commit()
                return f"Deleted: {key}"
        return f"Key not found: {key}"

    async def _handle_history(self, parts: list[str], memory: "MemoryManager") -> str:
        """Show change history for a fact key: /history <key>."""
        if len(parts) < 2:
            return "Usage: /history <key>\nExample: /history name"
        key = parts[1].lower().strip()
        history = await memory.facts.get_fact_history(key)
        if not history:
            return f"No history found for: {key}"
        lines = [f"History for '{key}':"]
        for entry in history:
            status = "(current)" if entry["is_current"] else "(archived)"
            vf = entry["valid_from"][:19] if entry["valid_from"] else "unknown"
            vu = entry["valid_until"][:19] if entry["valid_until"] else "now"
            lines.append(f"  {entry['value']}  {status}")
            lines.append(f"    {vf} -> {vu}")
        return "\n".join(lines)

    async def _handle_profile(self, memory: "MemoryManager") -> str:
        """Show all saved personal facts."""
        facts = await memory.facts.get_all_facts()
        if not facts:
            return "No personal facts saved yet.\nUse /remember <key> <value> to add facts."
        lines = ["Your profile:"]
        for k, v in sorted(facts.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    async def _handle_style(self, parts: list[str], memory: "MemoryManager") -> str:
        """Set writing style preference: /style <formal|informal|technical|brief>."""
        if len(parts) < 2:
            return f"Usage: /style <style>\nValid styles: {', '.join(sorted(VALID_STYLES))}"
        style = parts[1].lower().strip()
        if style not in VALID_STYLES:
            return f"Unknown style: {style}\nValid styles: {', '.join(sorted(VALID_STYLES))}"
        await memory.facts.save_facts([{"fact_type": "style", "fact_value": style}])
        return f"Writing style set: {style}"

    async def _handle_stats(self, memory: "MemoryManager") -> str:
        """Show system statistics."""
        stats = await memory.get_stats()
        lines = [
            "System statistics:",
            f"  Total tasks:   {stats.get('total_tasks', 0)}",
            f"  Successful:    {stats.get('successful_tasks', 0)}",
            f"  Success rate:  {stats.get('success_rate', 0)}%",
            f"  Avg duration:  {stats.get('avg_duration', 0)}s",
            f"  Avg quality:   {stats.get('avg_quality_score', 0)}",
        ]
        try:
            from src.organism.memory.solution_cache import SolutionCache
            cache_stats = await SolutionCache().get_stats()
            lines.append(f"  Cache entries: {cache_stats['cache_entries']}")
            lines.append(f"  Cache hits:    {cache_stats['total_cache_hits']}")
            lines.append(f"  Cache quality: {cache_stats['avg_cached_quality']}")
        except Exception:
            pass
        return "\n".join(lines)

    async def _handle_improve(self, parts: list[str], memory: "MemoryManager") -> str:
        """Run auto-improvement cycle: failures → patterns → KnowledgeBase rules."""
        days = 7
        if len(parts) >= 2:
            try:
                days = int(parts[1])
            except ValueError:
                return "Usage: /improve [days]\nExample: /improve 14"

        from src.organism.self_improvement.auto_improver import AutoImprover
        from src.organism.memory.knowledge_base import KnowledgeBase
        from src.organism.llm.claude import ClaudeProvider

        llm = ClaudeProvider()
        improver = AutoImprover()
        kb = KnowledgeBase()

        summary = await improver.run_cycle(memory, llm, kb, days=days)

        lines = [
            f"Improvement cycle complete (last {days} days):",
            f"  Failed tasks found:   {summary['failures_found']}",
            f"  Patterns analyzed:    {summary['patterns_analyzed']}",
            f"  Rules saved:          {summary['rules_saved']}",
        ]
        if summary["rules_saved"] == 0:
            lines.append("  (not enough repeating failure patterns yet)")
        return "\n".join(lines)

    def _handle_schedule(self) -> str:
        """Show all scheduled tasks: /schedule."""
        if self.scheduler is None:
            return "Scheduler not available (only in Telegram mode)."
        jobs = self.scheduler.list_jobs()
        if not jobs:
            return "No scheduled tasks."
        lines = ["Scheduled tasks:"]
        for j in jobs:
            status = "ON" if j.enabled else "OFF"
            if j.schedule_type == "daily":
                sched = f"daily {j.time_of_day.strftime('%H:%M')}" if j.time_of_day else "daily"
            elif j.schedule_type == "weekly":
                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                day = days[j.weekday] if j.weekday is not None else "?"
                t = j.time_of_day.strftime("%H:%M") if j.time_of_day else ""
                sched = f"weekly {day} {t}".strip()
            else:
                sched = f"every {j.interval_minutes}min" if j.interval_minutes else "interval"
            last = j.last_run.strftime("%Y-%m-%d %H:%M") if j.last_run else "never"
            lines.append(f"  [{status}] {j.name} — {sched} (last: {last})")
        return "\n".join(lines)

    def _handle_schedule_toggle(self, parts: list[str], enable: bool) -> str:
        """Enable or disable a scheduled task."""
        if self.scheduler is None:
            return "Scheduler not available (only in Telegram mode)."
        if len(parts) < 2:
            cmd = "/schedule_enable" if enable else "/schedule_disable"
            return f"Usage: {cmd} <name>"
        name = parts[1].strip()
        if name not in self.scheduler.jobs:
            available = ", ".join(self.scheduler.jobs.keys()) if self.scheduler.jobs else "none"
            return f"Job not found: {name}\nAvailable: {available}"
        if enable:
            self.scheduler.enable_job(name)
            return f"Enabled: {name}"
        else:
            self.scheduler.disable_job(name)
            return f"Disabled: {name}"

    async def _handle_prompts(self, memory: "MemoryManager") -> str:
        """Show active prompt versions and their quality stats."""
        from src.organism.memory.database import PromptVersion, AsyncSessionLocal
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PromptVersion)
                .where(PromptVersion.is_active == True)  # noqa: E712
                .order_by(PromptVersion.prompt_name)
            )
            active_versions = result.scalars().all()

        if not active_versions:
            return (
                "No prompt versions saved yet.\n"
                "Versions are auto-created on first task evaluation."
            )

        lines = ["Active prompt versions:"]
        for v in active_versions:
            quality = f"{v.avg_quality:.2f}" if v.task_count > 0 else "n/a"
            lines.append(
                f"  {v.prompt_name:<20} v{v.version:<3} "
                f"tasks={v.task_count:<5} avg_quality={quality}"
            )
        return "\n".join(lines)
