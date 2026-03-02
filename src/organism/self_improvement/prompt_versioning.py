"""Q-4.5: Prompt Version Control + auto-rollback.

Tracks prompt content versions alongside their measured quality.
If an active version's avg_quality drops below a threshold after enough
evaluations, auto_rollback() restores the best previous version.
"""
from sqlalchemy import select, func

from src.organism.memory.database import PromptVersion, AsyncSessionLocal
from src.organism.logging.error_handler import get_logger

_log = get_logger("self_improvement.prompt_versioning")


class PromptVersionControl:

    async def save_version(self, prompt_name: str, content: str) -> None:
        """Save a new version of a prompt, activate it, deactivate the previous one."""
        async with AsyncSessionLocal() as session:
            # Deactivate all existing active versions for this prompt
            stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .where(PromptVersion.is_active == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            for row in result.scalars().all():
                row.is_active = False

            # Determine next version number
            max_stmt = (
                select(func.max(PromptVersion.version))
                .where(PromptVersion.prompt_name == prompt_name)
            )
            max_result = await session.execute(max_stmt)
            max_ver = max_result.scalar() or 0

            session.add(PromptVersion(
                prompt_name=prompt_name,
                version=max_ver + 1,
                content=content,
                avg_quality=0.0,
                task_count=0,
                is_active=True,
            ))
            await session.commit()
        _log.info(f"Saved prompt version {max_ver + 1} for '{prompt_name}'")

    async def get_active(self, prompt_name: str) -> str | None:
        """Return the content of the active version, or None if not found."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .where(PromptVersion.is_active == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row.content if row else None

    async def record_quality(self, prompt_name: str, quality_score: float) -> None:
        """Update running average quality on the active version.

        Running average: new_avg = (old_avg * old_count + score) / (old_count + 1)
        Silently skips if no active version exists.
        """
        async with AsyncSessionLocal() as session:
            stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .where(PromptVersion.is_active == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            new_count = row.task_count + 1
            row.avg_quality = round(
                (row.avg_quality * row.task_count + quality_score) / new_count, 4
            )
            row.task_count = new_count
            await session.commit()

    async def auto_rollback(
        self, prompt_name: str, min_quality: float = 0.6, min_tasks: int = 10
    ) -> bool:
        """Roll back to the best previous version if active quality is too low.

        Conditions:
        - active version avg_quality < min_quality
        - active version task_count >= min_tasks  (enough data to judge)
        - a previous version with task_count > 0 exists (has measured quality)

        Returns True if rollback was performed, False otherwise.
        """
        async with AsyncSessionLocal() as session:
            # Get the active version
            active_stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .where(PromptVersion.is_active == True)  # noqa: E712
            )
            active_result = await session.execute(active_stmt)
            active = active_result.scalar_one_or_none()

            if active is None:
                return False

            if not (active.avg_quality < min_quality and active.task_count >= min_tasks):
                return False

            # Find the best previous version (by avg_quality, must have data)
            best_stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .where(PromptVersion.id != active.id)
                .where(PromptVersion.task_count > 0)
                .order_by(PromptVersion.avg_quality.desc())
                .limit(1)
            )
            best_result = await session.execute(best_stmt)
            best = best_result.scalar_one_or_none()

            if best is None:
                return False

            active.is_active = False
            best.is_active = True
            await session.commit()

        _log.warning(
            f"Auto-rollback '{prompt_name}': v{active.version} "
            f"(quality={active.avg_quality:.2f}, tasks={active.task_count}) "
            f"→ v{best.version} (quality={best.avg_quality:.2f})"
        )
        return True

    async def get_history(self, prompt_name: str) -> list[dict]:
        """Return all versions for a prompt ordered by version descending."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(PromptVersion)
                .where(PromptVersion.prompt_name == prompt_name)
                .order_by(PromptVersion.version.desc())
            )
            result = await session.execute(stmt)
            return [
                {
                    "version": r.version,
                    "avg_quality": r.avg_quality,
                    "task_count": r.task_count,
                    "is_active": r.is_active,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in result.scalars().all()
            ]
