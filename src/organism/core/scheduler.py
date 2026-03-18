"""Q-6.2: Proactive scheduler with cron-triggered tasks.

Background scheduler that runs ScheduledJobs (daily/weekly/interval)
and sends results via notify callback (e.g. Telegram).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Any, Awaitable, Callable

from config.settings import settings
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("core.scheduler")


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


# Default jobs for a gold mining artel
DEFAULT_ARTEL_JOBS: list[ScheduledJob] = [
    ScheduledJob(
        name="morning_summary",
        # "подготовь утреннюю сводку: статус техники, расход ГСМ за вчера, текущие заявки на запчасти"
        task_text=(
            "\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c "
            "\u0443\u0442\u0440\u0435\u043d\u043d\u044e\u044e "
            "\u0441\u0432\u043e\u0434\u043a\u0443: "
            "\u0441\u0442\u0430\u0442\u0443\u0441 "
            "\u0442\u0435\u0445\u043d\u0438\u043a\u0438, "
            "\u0440\u0430\u0441\u0445\u043e\u0434 "
            "\u0413\u0421\u041c \u0437\u0430 "
            "\u0432\u0447\u0435\u0440\u0430, "
            "\u0442\u0435\u043a\u0443\u0449\u0438\u0435 "
            "\u0437\u0430\u044f\u0432\u043a\u0438 "
            "\u043d\u0430 "
            "\u0437\u0430\u043f\u0447\u0430\u0441\u0442\u0438"
        ),
        schedule_type="daily",
        time_of_day=dt_time(6, 30),
        enabled=False,
    ),
    ScheduledJob(
        name="weekly_production",
        # "составь еженедельный отчёт по добыче: объём за неделю, сравнение с планом, основные проблемы"
        task_text=(
            "\u0441\u043e\u0441\u0442\u0430\u0432\u044c "
            "\u0435\u0436\u0435\u043d\u0435\u0434\u0435\u043b\u044c\u043d\u044b\u0439 "
            "\u043e\u0442\u0447\u0451\u0442 "
            "\u043f\u043e "
            "\u0434\u043e\u0431\u044b\u0447\u0435: "
            "\u043e\u0431\u044a\u0451\u043c "
            "\u0437\u0430 "
            "\u043d\u0435\u0434\u0435\u043b\u044e, "
            "\u0441\u0440\u0430\u0432\u043d\u0435\u043d\u0438\u0435 "
            "\u0441 "
            "\u043f\u043b\u0430\u043d\u043e\u043c, "
            "\u043e\u0441\u043d\u043e\u0432\u043d\u044b\u0435 "
            "\u043f\u0440\u043e\u0431\u043b\u0435\u043c\u044b"
        ),
        schedule_type="weekly",
        time_of_day=dt_time(8, 0),
        weekday=0,  # Monday
        enabled=False,
    ),
    ScheduledJob(
        name="fuel_anomaly_check",
        # "проверь расход ГСМ за последние 24 часа, найди отклонения от нормы более 10% по каждой единице техники"
        task_text=(
            "\u043f\u0440\u043e\u0432\u0435\u0440\u044c "
            "\u0440\u0430\u0441\u0445\u043e\u0434 "
            "\u0413\u0421\u041c "
            "\u0437\u0430 "
            "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 "
            "24 "
            "\u0447\u0430\u0441\u0430, "
            "\u043d\u0430\u0439\u0434\u0438 "
            "\u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u044f "
            "\u043e\u0442 "
            "\u043d\u043e\u0440\u043c\u044b "
            "\u0431\u043e\u043b\u0435\u0435 "
            "10% "
            "\u043f\u043e "
            "\u043a\u0430\u0436\u0434\u043e\u0439 "
            "\u0435\u0434\u0438\u043d\u0438\u0446\u0435 "
            "\u0442\u0435\u0445\u043d\u0438\u043a\u0438"
        ),
        schedule_type="interval",
        interval_minutes=360,  # every 6 hours
        enabled=False,
    ),
    ScheduledJob(
        name="weekly_prompt_evolution",
        task_text="__internal__:evolve_prompts",
        schedule_type="weekly",
        time_of_day=dt_time(3, 0),
        weekday=6,  # Sunday
        enabled=False,  # user enables via /schedule_enable
    ),
    ScheduledJob(
        name="db_cleanup",
        task_text="__internal__:db_cleanup",
        schedule_type="weekly",
        weekday=6,  # Sunday
        time_of_day=dt_time(4, 0),
        enabled=False,
    ),
]


class ProactiveScheduler:
    """Background scheduler that triggers tasks on a cron-like schedule."""

    def __init__(
        self,
        task_runner: Callable[[str], Awaitable[Any]],
        notify: Callable[[str, str], Awaitable[None]] | None = None,
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

    def enable_job(self, name: str) -> None:
        if name in self.jobs:
            self.jobs[name].enabled = True
            _log.info("scheduler.enable_job: %s", name)

    def disable_job(self, name: str) -> None:
        if name in self.jobs:
            self.jobs[name].enabled = False
            _log.info("scheduler.disable_job: %s", name)

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self.jobs.values())

    # -- Persistence layer (SCHED-1a) --

    async def load_from_db(self) -> None:
        """Load user-created jobs from DB. Called at startup after DEFAULT_ARTEL_JOBS."""
        try:
            from src.organism.memory.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            if not AsyncSessionLocal:
                return
            async with AsyncSessionLocal() as session:
                result = await session.execute(sa_text(
                    "SELECT name, task_text, schedule_type, time_of_day, weekday, "
                    "interval_minutes, enabled, last_run, artel_id "
                    "FROM scheduled_jobs WHERE artel_id = :aid"
                ), {"aid": settings.artel_id})
                rows = result.fetchall()
            loaded = 0
            for row in rows:
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
                )
                self.jobs[job.name] = job  # overwrites system job if same name
                loaded += 1
            if loaded:
                _log.info("scheduler.loaded_from_db: %d jobs", loaded)
        except Exception as exc:
            _log.error("scheduler.load_from_db failed: %s", exc)

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
                    "enabled=:en, is_system=:sys "
                    "WHERE name=:n AND artel_id=:aid"
                ), {
                    "tt": job.task_text, "st": job.schedule_type, "tod": tod_str,
                    "wd": job.weekday, "im": job.interval_minutes, "en": job.enabled,
                    "sys": is_system, "n": job.name, "aid": job.artel_id,
                })
                if result.rowcount == 0:
                    await session.execute(sa_text(
                        "INSERT INTO scheduled_jobs "
                        "(name, task_text, schedule_type, time_of_day, weekday, "
                        "interval_minutes, enabled, artel_id, is_system) "
                        "VALUES (:n, :tt, :st, :tod, :wd, :im, :en, :aid, :sys)"
                    ), {
                        "n": job.name, "tt": job.task_text, "st": job.schedule_type,
                        "tod": tod_str, "wd": job.weekday, "im": job.interval_minutes,
                        "en": job.enabled, "aid": job.artel_id, "sys": is_system,
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
                    await session.execute(sa_text("SELECT cleanup_old_edges(5000)"))
                    await session.commit()
                _log.info("Weekly DB cleanup completed")
            except Exception as e:
                _log.error("DB cleanup failed: %s", e)
        else:
            _log.warning("scheduler.unknown_internal: %s", command)

    async def _loop(self) -> None:
        """Main scheduler loop — checks every 30 seconds."""
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
                        result = await self.task_runner(job.task_text)
                        if result.success and self.notify:
                            output = result.output or ""
                            await self.notify(
                                job.artel_id,
                                f"[{job.name}] {output[:4000]}",
                            )
                except Exception as exc:
                    _log.error(
                        "scheduler.job_error: %s — %s: %s",
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
