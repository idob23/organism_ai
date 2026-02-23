from src.organism.llm.base import LLMProvider, Message
from src.organism.self_improvement.metrics import MetricsAnalyzer


OPTIMIZER_SYSTEM = """You are an AI performance analyst.
Analyze the metrics of an AI agent system and provide specific, actionable recommendations.
Be concise. Focus on the most impactful improvements.
Respond in the same language as the user message."""


class PromptOptimizer:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        self.analyzer = MetricsAnalyzer()

    async def analyze_and_recommend(self) -> str:
        report = await self.analyzer.get_full_report()

        prompt = f"""Analyze this AI agent performance report and give 3-5 specific recommendations:

Total tasks: {report['total_tasks']}
Success rate: {report['success_rate']}%
Average duration: {report['avg_duration']}s
Average steps per task: {report['avg_steps']}
Recent trend: {report['trend']}
Last 10 tasks success rate: {report['last_10_success_rate']}%

Tool usage patterns:
{chr(10).join(f"- {t['tools']}: {t['count']} uses, {t['success_rate']}% success, {t['avg_duration']}s avg" for t in report['tool_stats'])}

Provide specific recommendations to improve success rate, speed, and efficiency.
Focus on patterns in tool usage and task types."""

        response = await self.llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=OPTIMIZER_SYSTEM,
            model_tier="balanced",
        )
        return response.content
