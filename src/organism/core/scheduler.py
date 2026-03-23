"""Q-6.2: Proactive scheduler with cron-triggered tasks.

Background scheduler that runs ScheduledJobs (daily/weekly/interval)
and sends results via notify callback (e.g. Telegram).

FIX-89: Jobs loaded from config/jobs/{artel_id}.json instead of hardcode.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Awaitable, Callable

from config.settings import settings
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("core.scheduler")

_JOBS_DIR = Path("config/jobs")


@dataclass
class ScheduledJob:
    name: str
    task_text: str
    schedule_type: str  # "daily" | "weekly" | "interval"
    time_of_day: dt_time | None = None
    weekday: int | None = None  # 0=Mon..6=Sun
    interval_minutes: int | None = None
    enabled: bool = True
    last_run: datetime | None = None
    artel_id: str = "default"
    channel_id: str = ""  # Telegram channel to publish results ("" = personal only)
    personality_id: str = ""  # Personality override for this job ("" = use default)
    requires_approval: bool = False  # If True, send to personal chat for review before publishing


def load_jobs_from_config(artel_id: str) -> list[ScheduledJob]:
    """Load scheduled jobs from config/jobs/{artel_id}.json.

    Falls back to config/jobs/default.json if artel-specific file not found.
    Returns empty list on any error.
    """
    filepath = _JOBS_DIR / f"{artel_id}.json"
    if not filepath.exists():
        filepath = _JOBS_DIR / "default.json"
        if not filepath.exists():
            _log.warning("scheduler.no_config: neither %s.json nor default.json found", artel_id)
            return []

    try:
        raw = filepath.read_text(encoding="utf-8")
        items = json.loads(raw)
    except Exception as exc:
        _log.error("scheduler.config_parse_error: %s: %s", filepath, exc)
        return []

    jobs: list[ScheduledJob] = []
    for item in items:
        try:
            tod = None
            tod_str = item.get("time_of_day", "")
            if tod_str:
                parts = tod_str.split(":")
                tod = dt_time(int(parts[0]), int(parts[1]))
            job = ScheduledJob(
                name=item["name"],
                task_text=item["task_text"],
                schedule_type=item["schedule_type"],
                time_of_day=tod,
                weekday=item.get("weekday"),
                interval_minutes=item.get("interval_minutes"),
                enabled=item.get("enabled_default", False),
                artel_id=settings.artel_id,
                channel_id=item.get("channel_id", ""),
                personality_id=item.get("personality_id", ""),
                requires_approval=item.get("requires_approval", False),
            )
            jobs.append(job)
        except Exception as exc:
            _log.error("scheduler.job_parse_error: %s: %s", item.get("name", "?"), exc)

    _log.info("scheduler.config_loaded: %s (%d jobs)", filepath.name, len(jobs))
    return jobs


class ProactiveScheduler:
    """Background scheduler that triggers tasks on a cron-like schedule."""

    def __init__(
        self,
        task_runner: Callable[..., Awaitable[Any]],
        notify: Callable[[str, str, str, bool, str], Awaitable[None]] | None = None,
    ) -> None:
        self.task_runner = task_runner
        self.notify = notify
        self.jobs: dict[str, ScheduledJob] = {}
        self._running: bool = False
        self._task: asyncio.Task | None = None
    def add_job(self, job: ScheduledJob) -> None:
        self.jobs[job.name] = job
        _log.info("scheduler.add_job: %s (%s)", job.name, job.schedule_type)

    def remove_job(self, name: str) -> None:
        self.jobs.pop(name, None)
        _log.info("scheduler.remove_job: %s", name)

    async def enable_job(self, name: str) -> None:
        if name in self.jobs:
            self.jobs[name].enabled = True
            await self._save_job(self.jobs[name])
            _log.info("scheduler.enable_job: %s", name)

    async def disable_job(self, name: str) -> None:
        if name in self.jobs:
            self.jobs[name].enabled = False
            await self._save_job(self.jobs[name])
            _log.info("scheduler.disable_job: %s", name)

    async def set_job_enabled(self, name: str, enabled: bool) -> bool:
        """Enable/disable a job with DB persistence. Returns False if not found."""
        if name not in self.jobs:
            return False
        self.jobs[name].enabled = enabled
        await self._save_job(self.jobs[name])
        _log.info("scheduler.set_job_enabled: %s \u2192 %s", name, enabled)
        return True

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self.jobs.values())

    # -- Pending publications (FIX-90, FIX-92: persisted to DB) --

    async def add_pending_publication(self, short_id: str, text: str, channel_id: str, job_name: str) -> None:
        """Save pending publication to DB."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                await session.execute(sa_text(
                    "INSERT INTO pending_publications (short_id, text, channel_id, job_name, artel_id) "
                    "VALUES (:sid, :txt, :cid, :jn, :aid)"
                ), {"sid": short_id, "txt": text, "cid": channel_id, "jn": job_name, "aid": settings.artel_id})
                await session.commit()
        except Exception as exc:
            _log.error("scheduler.add_pending_publication failed: %s", exc)

    async def get_pending_publication(self, short_id: str) -> dict | None:
        """Get pending publication from DB by short_id."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return None
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT text, channel_id, job_name, created_at "
                    "FROM pending_publications WHERE short_id = :sid"
                ), {"sid": short_id})
                row = result.fetchone()
                if row:
                    return {"text": row[0], "channel_id": row[1], "job_name": row[2], "created_at": row[3]}
                return None
        except Exception as exc:
            _log.error("scheduler.get_pending_publication failed: %s", exc)
            return None

    async def remove_pending_publication(self, short_id: str) -> dict | None:
        """Atomically remove pending publication from DB. Returns the publication or None.

        FIX-94: Uses DELETE...RETURNING to prevent race condition
        when two admins call /publish simultaneously.
        """
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return None
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "DELETE FROM pending_publications WHERE short_id = :sid "
                    "RETURNING text, channel_id, job_name, created_at"
                ), {"sid": short_id})
                row = result.fetchone()
                await session.commit()
                if row:
                    return {"text": row[0], "channel_id": row[1], "job_name": row[2], "created_at": row[3]}
                return None
        except Exception as exc:
            _log.error("scheduler.remove_pending_publication failed: %s", exc)
            return None

    async def list_pending_publications(self) -> list[tuple[str, dict]]:
        """List all pending publications from DB."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return []
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT short_id, text, channel_id, job_name, created_at "
                    "FROM pending_publications WHERE artel_id = :aid "
                    "ORDER BY created_at ASC"
                ), {"aid": settings.artel_id})
                rows = result.fetchall()
                return [
                    (row[0], {"text": row[1], "channel_id": row[2], "job_name": row[3], "created_at": row[4]})
                    for row in rows
                ]
        except Exception as exc:
            _log.error("scheduler.list_pending_publications failed: %s", exc)
            return []

    # -- Startup sync (FIX-89) --

    async def load_and_sync(self, artel_id: str) -> None:
        """Load jobs from config, sync with DB states, load user jobs."""
        # 1. Load jobs from config
        config_jobs = load_jobs_from_config(artel_id)

        # 2. Load states from DB (enabled, last_run)
        db_states = await self._load_states_from_db()

        # 3. Merge config with DB states
        for job in config_jobs:
            if job.name in db_states:
                job.enabled = db_states[job.name]["enabled"]
                job.last_run = db_states[job.name]["last_run"]
            self.add_job(job)
            await self._save_job(job, is_system=True)

        # 4. Load user-created jobs from DB
        await self._load_user_jobs_from_db()

    async def _load_states_from_db(self) -> dict[str, dict]:
        """Load enabled/last_run states for all jobs from DB."""
        states: dict[str, dict] = {}
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return states
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT name, enabled, last_run "
                    "FROM scheduled_jobs WHERE artel_id = :aid"
                ), {"aid": settings.artel_id})
                for row in result.fetchall():
                    states[row[0]] = {"enabled": row[1], "last_run": row[2]}
        except Exception as exc:
            _log.error("scheduler._load_states_from_db failed: %s", exc)
        return states

    async def _load_user_jobs_from_db(self) -> None:
        """Load user-created (non-system) jobs from DB."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT name, task_text, schedule_type, time_of_day, weekday, "
                    "interval_minutes, enabled, last_run, artel_id, "
                    "COALESCE(channel_id, '') as channel_id, "
                    "COALESCE(personality_id, '') as personality_id, "
                    "COALESCE(requires_approval, false) as requires_approval "
                    "FROM scheduled_jobs WHERE artel_id = :aid AND is_system = false"
                ), {"aid": settings.artel_id})
                rows = result.fetchall()
            loaded = 0
            for row in rows:
                if row[0] in self.jobs:
                    continue  # config job already loaded
                tod = None
                if row[3]:
                    parts = row[3].split(":")
                    tod = dt_time(int(parts[0]), int(parts[1]))
                job = ScheduledJob(
                    name=row[0],
                    task_text=row[1],
                    schedule_type=row[2],
                    time_of_day=tod,
                    weekday=row[4],
                    interval_minutes=row[5],
                    enabled=row[6],
                    last_run=row[7],
                    artel_id=row[8],
                    channel_id=row[9] or "",
                    personality_id=row[10] or "",
                    requires_approval=bool(row[11]) if row[11] is not None else False,
                )
                self.jobs[job.name] = job
                loaded += 1
            if loaded:
                _log.info("scheduler.loaded_user_jobs: %d", loaded)
        except Exception as exc:
            _log.error("scheduler._load_user_jobs_from_db failed: %s", exc)

    # -- Persistence layer (SCHED-1a) --

    async def _save_job(self, job: ScheduledJob, is_system: bool = False) -> None:
        """Upsert a job to DB."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            tod_str = f"{job.time_of_day.hour:02d}:{job.time_of_day.minute:02d}" if job.time_of_day else None
            async with AsyncSessionLocal() as session:
                # Try update first
                result = await session.execute(sa_text(
                    "UPDATE scheduled_jobs SET task_text=:tt, schedule_type=:st, "
                    "time_of_day=:tod, weekday=:wd, interval_minutes=:im, "
                    "enabled=:en, is_system=:sys, channel_id=:cid, personality_id=:pid, "
                    "requires_approval=:ra "
                    "WHERE name=:n AND artel_id=:aid"
                ), {
                    "tt": job.task_text, "st": job.schedule_type, "tod": tod_str,
                    "wd": job.weekday, "im": job.interval_minutes, "en": job.enabled,
                    "sys": is_system, "n": job.name, "aid": job.artel_id,
                    "cid": job.channel_id, "pid": job.personality_id,
                    "ra": job.requires_approval,
                })
                if result.rowcount == 0:
                    await session.execute(sa_text(
                        "INSERT INTO scheduled_jobs "
                        "(name, task_text, schedule_type, time_of_day, weekday, "
                        "interval_minutes, enabled, artel_id, is_system, "
                        "channel_id, personality_id, requires_approval) "
                        "VALUES (:n, :tt, :st, :tod, :wd, :im, :en, :aid, :sys, "
                        ":cid, :pid, :ra)"
                    ), {
                        "n": job.name, "tt": job.task_text, "st": job.schedule_type,
                        "tod": tod_str, "wd": job.weekday, "im": job.interval_minutes,
                        "en": job.enabled, "aid": job.artel_id, "sys": is_system,
                        "cid": job.channel_id, "pid": job.personality_id,
                        "ra": job.requires_approval,
                    })
                await session.commit()
        except Exception as exc:
            _log.error("scheduler._save_job failed: %s", exc)

    async def _delete_job_from_db(self, name: str) -> None:
        """Delete a job from DB."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                await session.execute(sa_text(
                    "DELETE FROM scheduled_jobs WHERE name=:n AND artel_id=:aid"
                ), {"n": name, "aid": settings.artel_id})
                await session.commit()
        except Exception as exc:
            _log.error("scheduler._delete_job_from_db failed: %s", exc)

    async def _update_last_run(self, name: str, last_run: datetime) -> None:
        """Update last_run in DB after execution."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                await session.execute(sa_text(
                    "UPDATE scheduled_jobs SET last_run=:lr WHERE name=:n AND artel_id=:aid"
                ), {"lr": last_run, "n": name, "aid": settings.artel_id})
                await session.commit()
        except Exception as exc:
            _log.error("scheduler._update_last_run failed: %s", exc)

    async def create_job(self, job: ScheduledJob) -> bool:
        """Create user-defined job: add to self.jobs + save to DB."""
        self.jobs[job.name] = job
        await self._save_job(job, is_system=False)
        _log.info("scheduler.create_job: %s (%s)", job.name, job.schedule_type)
        return True

    async def delete_user_job(self, name: str) -> bool:
        """Delete user-defined job (not system). Returns False if not found or system."""
        if name not in self.jobs:
            return False
        # Check if system job in DB
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if AsyncSessionLocal:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(sa_text(
                        "SELECT is_system FROM scheduled_jobs WHERE name=:n AND artel_id=:aid"
                    ), {"n": name, "aid": settings.artel_id})
                    row = result.fetchone()
                    if row and row[0]:
                        return False
        except Exception:
            pass
        self.jobs.pop(name, None)
        await self._delete_job_from_db(name)
        _log.info("scheduler.delete_user_job: %s", name)
        return True

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        _log.info("scheduler.started: %d jobs", len(self.jobs))

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        _log.info("scheduler.stopped")

    async def _run_internal(self, task_text: str) -> None:
        """Handle __internal__:* tasks that don't go through CoreLoop."""
        command = task_text.split(":", 1)[1].strip() if ":" in task_text else ""
        if command == "evolve_prompts":
            try:
                from src.organism.llm.claude import ClaudeProvider
                from src.organism.self_improvement.prompt_versioning import PromptVersionControl
                from src.organism.self_improvement.evolutionary_search import EvolutionaryPromptSearch

                llm = ClaudeProvider()
                pvc = PromptVersionControl()
                evo = EvolutionaryPromptSearch(llm, pvc)
                results = await evo.evolve_all()
                for r in results:
                    _log.info(
                        "scheduler.evolve_result: %s gen=%d fitness=%.4f deployed=%s",
                        r.prompt_name, r.generation, r.best_fitness, r.deployed,
                    )
            except Exception as exc:
                log_exception(_log, "Internal task evolve_prompts failed", exc)
        elif command == "db_cleanup":
            try:
                from src.organism.memory.database import AsyncSessionLocal
                from sqlalchemy import text as sa_text
                async with AsyncSessionLocal() as session:
                    await session.execute(sa_text("SELECT cleanup_expired_cache()"))
                    await session.execute(sa_text("SELECT cleanup_old_reflections(1000)"))
                    await session.execute(sa_text("SELECT cleanup_old_errors(30)"))
                    await session.commit()
                _log.info("Weekly DB cleanup completed")
            except Exception as e:
                _log.error("DB cleanup failed: %s", e)
        else:
            _log.warning("scheduler.unknown_internal: %s", command)

    async def _loop(self) -> None:
        """Main scheduler loop \u2014 checks every 30 seconds."""
        while self._running:
            now = datetime.utcnow()
            for job in list(self.jobs.values()):
                if not job.enabled:
                    continue
                if not self._should_run(job, now):
                    continue
                job.last_run = now
                await self._update_last_run(job.name, now)
                try:
                    _log.info("scheduler.run_job: %s", job.name)
                    # Internal tasks bypass CoreLoop
                    if job.task_text.startswith("__internal__:"):
                        await self._run_internal(job.task_text)
                    else:
                        result = await self.task_runner(
                            job.task_text, personality_id=job.personality_id,
                        )
                        if result.success and self.notify:
                            output = result.answer or result.output or ""
                            await self.notify(
                                job.artel_id,
                                output,
                                job.channel_id,
                                job.requires_approval,
                                job.name,
                            )
                except Exception as exc:
                    _log.error(
                        "scheduler.job_error: %s \u2014 %s: %s",
                        job.name, type(exc).__name__, exc,
                    )
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break

    @staticmethod
    def _should_run(job: ScheduledJob, now: datetime) -> bool:
        if job.schedule_type == "daily":
            if job.time_of_day is None:
                return False
            if now.hour < job.time_of_day.hour:
                return False
            if now.hour == job.time_of_day.hour and now.minute < job.time_of_day.minute:
                return False
            if job.last_run is not None and job.last_run.date() >= now.date():
                return False
            return True

        if job.schedule_type == "weekly":
            if job.time_of_day is None or job.weekday is None:
                return False
            if now.weekday() != job.weekday:
                return False
            if now.hour < job.time_of_day.hour:
                return False
            if now.hour == job.time_of_day.hour and now.minute < job.time_of_day.minute:
                return False
            if job.last_run is not None:
                days_since = (now.date() - job.last_run.date()).days
                if days_since < 1:
                    return False
            return True

        if job.schedule_type == "interval":
            if job.interval_minutes is None:
                return False
            if job.last_run is None:
                return True
            elapsed = (now - job.last_run).total_seconds() / 60.0
            return elapsed >= job.interval_minutes

        return False
