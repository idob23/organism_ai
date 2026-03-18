"""Q-4.3: Memory commands /remember, /forget, /profile, /style, /stats."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.organism.memory.manager import MemoryManager
    from src.organism.agents.factory import AgentFactory
    from src.organism.core.loop import CoreLoop

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
    "  /approve <id>            \u2014 approve a pending action\n"
    "  /reject <id>             \u2014 reject a pending action\n"
    "  /personality             \u2014 show current personality config\n"
    "  /reset                   \u2014 reset all saved profile data\n"
    "  /insights                \u2014 show insights awaiting verification\n"
    "  /cleanup                \u2014 run database cleanup (expired cache, old reflections, old errors)\n"
    "  /errors [N]              \u2014 show last N errors (default 5)\n"
    "  /test_error              \u2014 send a test error to monitoring\n"
    "  /agents                  \u2014 list role templates and created agents\n"
    "  /create_agent <role> [name] \u2014 create an agent from a role template\n"
    "  /assign <agent> <task>   \u2014 assign a task to a specific agent\n"
    "  /help                    \u2014 show this help\n"
    "\n"
    "  (schedule management also available in natural language)\n"
)


class CommandHandler:

    def __init__(
        self,
        scheduler=None,
        approval=None,
        personality=None,
        factory: "AgentFactory | None" = None,
        loop: "CoreLoop | None" = None,
    ) -> None:
        self.scheduler = scheduler
        self.approval = approval
        self.personality = personality
        self.factory = factory
        self.loop = loop

    def is_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    async def handle(self, text: str, memory: "MemoryManager | None", user_id: str = "default") -> str:
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

        # Approval commands — no memory required
        if cmd == "/approve":
            return self._handle_approval(parts, approved=True)
        elif cmd == "/reject":
            return self._handle_approval(parts, approved=False)

        # Personality — no memory required
        if cmd == "/personality":
            return self._handle_personality()

        # Test error — no memory required
        if cmd == "/test_error":
            return await self._handle_test_error()

        # Agent commands (Q-9.5) — no memory required
        if cmd == "/agents":
            return self._handle_agents()
        elif cmd == "/create_agent":
            return await self._handle_create_agent(parts)
        elif cmd == "/assign":
            return await self._handle_assign(parts)

        if memory is None:
            return "Memory not available (DATABASE_URL not configured)."

        try:
            await memory.initialize()
        except Exception as e:
            return f"Memory error: {e}"

        if cmd == "/remember":
            return await self._handle_remember(parts, memory, user_id)
        elif cmd == "/forget":
            return await self._handle_forget(parts, memory, user_id)
        elif cmd == "/profile":
            return await self._handle_profile(memory, user_id)
        elif cmd == "/history":
            return await self._handle_history(parts, memory, user_id)
        elif cmd == "/style":
            return await self._handle_style(parts, memory, user_id)
        elif cmd == "/stats":
            return await self._handle_stats(memory)
        elif cmd == "/improve":
            return await self._handle_improve(parts, memory)
        elif cmd == "/prompts":
            return await self._handle_prompts(memory)
        elif cmd == "/reset":
            return await self._handle_reset(memory, user_id)
        elif cmd == "/cleanup":
            return await self._handle_cleanup(memory)
        elif cmd == "/insights":
            return await self._handle_insights()
        elif cmd == "/errors":
            return await self._handle_errors(parts, memory)
        else:
            return f"Unknown command: {cmd}\n\n{HELP_TEXT}"

    async def _handle_remember(self, parts: list[str], memory: "MemoryManager", user_id: str) -> str:
        """Save a personal fact: /remember <key> <value>."""
        if len(parts) < 3:
            return "Usage: /remember <key> <value>\nExample: /remember name Igor"
        key = parts[1].lower().strip()
        value = parts[2].strip()
        if not key or not value:
            return "Key and value must not be empty."
        await memory.facts.save_facts([{"fact_type": key, "fact_value": value}], user_id=user_id)
        return f"Saved: {key} = {value}"

    async def _handle_forget(self, parts: list[str], memory: "MemoryManager", user_id: str) -> str:
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
                .where(UserProfile.user_id == user_id)
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

    async def _handle_history(self, parts: list[str], memory: "MemoryManager", user_id: str) -> str:
        """Show change history for a fact key: /history <key>."""
        if len(parts) < 2:
            return "Usage: /history <key>\nExample: /history name"
        key = parts[1].lower().strip()
        history = await memory.facts.get_fact_history(key, user_id=user_id)
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

    async def _handle_profile(self, memory: "MemoryManager", user_id: str) -> str:
        """Show all saved personal facts."""
        facts = await memory.facts.get_all_facts(user_id=user_id)
        if not facts:
            return "No personal facts saved yet.\nUse /remember <key> <value> to add facts."
        lines = ["Your profile:"]
        for k, v in sorted(facts.items()):
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    async def _handle_style(self, parts: list[str], memory: "MemoryManager", user_id: str) -> str:
        """Set writing style preference: /style <formal|informal|technical|brief>."""
        if len(parts) < 2:
            return f"Usage: /style <style>\nValid styles: {', '.join(sorted(VALID_STYLES))}"
        style = parts[1].lower().strip()
        if style not in VALID_STYLES:
            return f"Unknown style: {style}\nValid styles: {', '.join(sorted(VALID_STYLES))}"
        await memory.facts.save_facts([{"fact_type": "style", "fact_value": style}], user_id=user_id)
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
            cache_stats = await memory.cache.get_stats()
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

        summary = await improver.run_cycle(
            memory, llm, kb, days=days, human_approval=self.approval,
        )

        lines = [
            f"Improvement cycle complete (last {days} days):",
            f"  Failed tasks found:   {summary['failures_found']}",
            f"  Patterns analyzed:    {summary['patterns_analyzed']}",
            f"  Insights pending:     {summary.get('insights_pending', 0)}",
            f"  Sent for approval:    {summary.get('insights_sent', 0)}",
            f"  Rules saved:          {summary['rules_saved']}",
        ]
        if summary["rules_saved"] == 0 and summary.get("insights_pending", 0) == 0:
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

    def _handle_approval(self, parts: list[str], approved: bool) -> str:
        """Resolve a pending approval: /approve <id> or /reject <id>."""
        if self.approval is None:
            return "Approval system not available (only in Telegram mode)."
        if len(parts) < 2:
            cmd = "/approve" if approved else "/reject"
            return f"Usage: {cmd} <id>"
        short_id = parts[1].strip()
        return self.approval.resolve(short_id, approved)

    def _handle_personality(self) -> str:
        """Show current personality configuration."""
        if self.personality is None:
            return "Personality not configured."
        p = self.personality
        if not p.raw_content:
            return f"Personality: {p.artel_id}\n  (no personality file loaded)"
        lines = [f"Personality: {p.artel_id}"]
        for name, content in p.sections.items():
            preview = content[:200]
            if len(content) > 200:
                preview += "..."
            lines.append(f"  [{name}]")
            lines.append(f"    {preview}")
        return "\n".join(lines)

    async def _handle_test_error(self) -> str:
        """Insert a test error into error_log to verify monitoring pipeline."""
        try:
            from src.organism.monitoring.error_notifier import capture_error
            await capture_error(
                component="test",
                message="Test error from /test_error command",
                task_id="test-000",
                task_text="Testing error monitoring pipeline",
                level="WARNING",
            )
            return "Test error created. Check your error monitoring channel in ~60 seconds."
        except Exception as e:
            return f"Failed to create test error: {e}"

    async def _handle_reset(self, memory: "MemoryManager", user_id: str) -> str:
        """Clear all user profile facts for this user."""
        from src.organism.memory.database import UserProfile, AsyncSessionLocal
        from sqlalchemy import delete
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    delete(UserProfile)
                    .where(UserProfile.user_id == user_id)
                    .where(UserProfile.valid_until.is_(None))
                )
                count = result.rowcount
                await session.commit()
            return f"Profile reset: {count} fact(s) cleared."
        except Exception as e:
            return f"Reset error: {e}"

    async def _handle_cleanup(self, memory: "MemoryManager") -> str:
        """Run database cleanup functions."""
        from src.organism.memory.database import AsyncSessionLocal
        from sqlalchemy import text as sa_text
        results = []
        async with AsyncSessionLocal() as session:
            try:
                r = await session.execute(sa_text("SELECT cleanup_expired_cache()"))
                n = r.scalar()
                results.append(f"Expired cache entries removed: {n}")
            except Exception as e:
                results.append(f"Cache cleanup error: {e}")

            try:
                r = await session.execute(sa_text("SELECT cleanup_old_reflections(1000)"))
                n = r.scalar()
                results.append(f"Old reflections removed: {n}")
            except Exception as e:
                results.append(f"Reflections cleanup error: {e}")

            try:
                r = await session.execute(sa_text("SELECT cleanup_old_errors(30)"))
                n = r.scalar()
                results.append(f"Old error logs removed: {n}")
            except Exception as e:
                results.append(f"Error log cleanup error: {e}")

            try:
                r = await session.execute(sa_text("SELECT cleanup_old_edges(5000)"))
                n = r.scalar()
                results.append(f"Old graph edges removed: {n}")
            except Exception as e:
                results.append(f"Edges cleanup error: {e}")

            await session.commit()

        return "Database cleanup:\n" + "\n".join(f"  {r}" for r in results)

    async def _handle_insights(self) -> str:
        """Show pending and approved insights: /insights."""
        from src.organism.memory.database import PendingInsight, AsyncSessionLocal
        from sqlalchemy import select
        from config.settings import settings

        try:
            async with AsyncSessionLocal() as session:
                # Pending
                pending_result = await session.execute(
                    select(PendingInsight)
                    .where(PendingInsight.artel_id == settings.artel_id)
                    .where(PendingInsight.status == "pending")
                    .order_by(PendingInsight.confirmations.desc())
                )
                pending = pending_result.scalars().all()

                # Approved
                approved_result = await session.execute(
                    select(PendingInsight)
                    .where(PendingInsight.artel_id == settings.artel_id)
                    .where(PendingInsight.status == "approved")
                    .order_by(PendingInsight.updated_at.desc())
                )
                approved = approved_result.scalars().all()
        except Exception as e:
            return f"Failed to query insights: {e}"

        lines = [f"\U0001f4ca Awaiting verification: {len(pending)}"]
        if pending:
            lines.append("")
            for p in pending:
                lines.append(f"\u2022 [{p.confirmations}x] {p.rule_text[:100]}")

        lines.append(f"\n\u2705 Approved insights: {len(approved)}")
        if approved:
            for a in approved:
                lines.append(f"\u2022 {a.rule_text[:100]}")

        return "\n".join(lines)

    async def _handle_errors(self, parts: list[str], memory: "MemoryManager") -> str:
        """Show recent errors from error_log: /errors [N]."""
        limit = 5
        if len(parts) > 1:
            try:
                limit = min(int(parts[1]), 20)
            except ValueError:
                return "Usage: /errors [N]\nExample: /errors 10"

        from src.organism.memory.database import AsyncSessionLocal
        from sqlalchemy import text as sa_text

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT component, message, task_text, created_at "
                    "FROM error_log ORDER BY created_at DESC LIMIT :n"
                ), {"n": limit})
                rows = result.fetchall()
        except Exception as e:
            return f"Error reading error_log: {e}"

        if not rows:
            return "No errors found"

        lines = [f"Last {len(rows)} error(s):"]
        for row in rows:
            ts = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "?"
            msg = (row.message or "")[:200]
            line = f"\n[{ts}] {row.component}\n{msg}"
            if row.task_text:
                line += f"\nTask: {row.task_text[:100]}"
            lines.append(line)

        return "\n".join(lines)

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

    # ── Agent commands (Q-9.5) ───────────────────────────────────────────

    def _handle_agents(self) -> str:
        """List role templates and created agents: /agents."""
        if self.factory is None:
            return "AgentFactory \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d"

        lines = ["\U0001f4cb \u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 "
                 "\u0448\u0430\u0431\u043b\u043e\u043d\u044b \u0440\u043e\u043b\u0435\u0439:"]
        templates = self.factory.list_role_templates()
        if templates:
            for t in templates:
                desc = t["description"][:80] if t["description"] else ""
                lines.append(f"  \u2022 {t['role_id']} \u2014 {desc}")
        else:
            lines.append("  (\u043d\u0435\u0442 \u0448\u0430\u0431\u043b\u043e\u043d\u043e\u0432)")

        lines.append("")
        lines.append("\U0001f916 \u0421\u043e\u0437\u0434\u0430\u043d\u043d\u044b\u0435 "
                     "\u0430\u0433\u0435\u043d\u0442\u044b:")
        agents = self.factory.list_created_agents()
        if agents:
            for a in agents:
                lines.append(
                    f"  \u2022 {a.get('name', '?')} ({a.get('role_id', '?')}) "
                    f"\u2014 ID: {a.get('agent_id', '?')}"
                )
        else:
            lines.append(
                "  \u0410\u0433\u0435\u043d\u0442\u044b \u0435\u0449\u0451 \u043d\u0435 "
                "\u0441\u043e\u0437\u0434\u0430\u043d\u044b. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 "
                "/create_agent <\u0440\u043e\u043b\u044c>"
            )

        return "\n".join(lines)

    async def _handle_create_agent(self, parts: list[str]) -> str:
        """Create an agent from a role template: /create_agent <role> [name]."""
        if self.factory is None:
            return "AgentFactory \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d"

        if len(parts) < 2:
            templates = self.factory.list_role_templates()
            roles = ", ".join(t["role_id"] for t in templates) if templates else "(\u043d\u0435\u0442)"
            return (
                "Usage: /create_agent <\u0440\u043e\u043b\u044c> [\u0438\u043c\u044f]\n"
                f"\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 \u0440\u043e\u043b\u0438: {roles}"
            )

        role_id = parts[1].lower().strip()

        # Verify role exists
        if self.factory.get_role_template(role_id) is None:
            templates = self.factory.list_role_templates()
            roles = ", ".join(t["role_id"] for t in templates) if templates else "(\u043d\u0435\u0442)"
            return (
                f"\u0420\u043e\u043b\u044c '{role_id}' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430. "
                f"\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435: {roles}"
            )

        name = " ".join(parts[2:]).strip() if len(parts) > 2 else role_id.capitalize()

        if self.loop is None:
            return "\u041e\u0448\u0438\u0431\u043a\u0430: CoreLoop \u043d\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0434\u043b\u044f \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438"

        try:
            result = await self.factory.create_from_role(role_id, name, self.loop.llm)
            if result is None:
                return f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430: \u0440\u043e\u043b\u044c '{role_id}' \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430"
            return (
                f"\u2705 \u0410\u0433\u0435\u043d\u0442 {name} ({role_id}) "
                f"\u0441\u043e\u0437\u0434\u0430\u043d!\n"
                f"ID: {result['agent_id']}\n"
                f"Personality: {result['personality_file']}"
            )
        except Exception as exc:
            return f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u0430\u0433\u0435\u043d\u0442\u0430: {exc}"

    async def _handle_assign(self, parts: list[str]) -> str:
        """Assign a task to a specific agent: /assign <agent> <task>."""
        if self.factory is None:
            return "AgentFactory \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d"

        if len(parts) < 3:
            return "Usage: /assign <\u0430\u0433\u0435\u043d\u0442> <\u0437\u0430\u0434\u0430\u0447\u0430>"

        agent_ref = parts[1].strip()
        task_text = parts[2].strip()

        # Find agent by ID or name
        agents = self.factory.list_created_agents()
        agent_dict = None
        for a in agents:
            if a.get("agent_id") == agent_ref:
                agent_dict = a
                break
        if agent_dict is None:
            ref_lower = agent_ref.lower()
            for a in agents:
                if a.get("name", "").lower() == ref_lower:
                    agent_dict = a
                    break

        if agent_dict is None:
            return (
                f"\u0410\u0433\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d: "
                f"{agent_ref}\n\u0421\u043f\u0438\u0441\u043e\u043a \u0430\u0433\u0435\u043d\u0442\u043e\u0432: /agents"
            )

        if self.loop is None or getattr(self.loop, "_orchestrator", None) is None:
            return "MetaOrchestrator \u043d\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"

        if not hasattr(self.loop._orchestrator, "run_as_agent"):
            return "MetaOrchestrator \u043d\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d"

        try:
            result = await self.loop._orchestrator.run_as_agent(task_text, agent_dict)
            agent_name = agent_dict.get("name", agent_ref)
            if result.success:
                return f"\U0001f916 {agent_name}:\n\n{result.output}"
            return f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f: {result.error}"
        except Exception as exc:
            return f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f: {exc}"
