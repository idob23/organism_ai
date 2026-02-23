from sqlalchemy import text
from src.organism.memory.database import AsyncSessionLocal


class MetricsAnalyzer:

    async def get_full_report(self) -> dict:
        async with AsyncSessionLocal() as session:
            # Overall stats
            result = await session.execute(text("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
                    AVG(duration) as avg_duration,
                    AVG(steps_count) as avg_steps
                FROM task_memories
            """))
            row = result.fetchone()
            total = row[0] or 0
            successful = row[1] or 0

            # Tool usage stats
            result2 = await session.execute(text("""
                SELECT tools_used, COUNT(*) as cnt,
                       AVG(duration) as avg_dur,
                       SUM(CASE WHEN success THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate
                FROM task_memories
                WHERE tools_used != ''
                GROUP BY tools_used
                ORDER BY cnt DESC
                LIMIT 10
            """))
            tool_stats = [
                {
                    "tools": row[0],
                    "count": row[1],
                    "avg_duration": round(float(row[2] or 0), 2),
                    "success_rate": round(float(row[3] or 0), 1),
                }
                for row in result2.fetchall()
            ]

            # Recent trend (last 10 vs previous 10)
            result3 = await session.execute(text("""
                SELECT success, duration
                FROM task_memories
                ORDER BY created_at DESC
                LIMIT 20
            """))
            recent = result3.fetchall()
            last_10 = recent[:10]
            prev_10 = recent[10:]

            last_rate = sum(1 for r in last_10 if r[0]) / len(last_10) * 100 if last_10 else 0
            prev_rate = sum(1 for r in prev_10 if r[0]) / len(prev_10) * 100 if prev_10 else 0
            trend = "improving" if last_rate > prev_rate else "stable" if last_rate == prev_rate else "declining"

        return {
            "total_tasks": total,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "avg_duration": round(float(row[2] or 0), 2),
            "avg_steps": round(float(row[3] or 0), 2),
            "tool_stats": tool_stats,
            "trend": trend,
            "last_10_success_rate": round(last_rate, 1),
        }
