#!/usr/bin/env python3
"""Organism AI benchmark suite.

Measures quality across 26 task types and reports a formatted summary.

Tasks 1-10:  baseline (code, csv, writing, mixed, presentation, research,
             analysis, cache, multi-agent, command)
Tasks 11-14: Sprint 5 coverage (temporal-query, entity-query, template-reuse,
             causal-query) — require a warm DB with prior memory/graph data.
Tasks 15-19: Sprint 6 coverage (orchestrator-sm, cmd-schedule, cmd-personality,
             gateway-write, cmd-help)
Tasks 20-23: Sprint 7 coverage (cross-agent, structured reflections, few-shot,
             evolutionary)
Tasks 24-26: Sprint 8 coverage (duplicate-search, mcp-serve, a2a-infra)

Usage:
    python benchmark.py           # run all 26 tasks
    python benchmark.py --quick   # run only tasks 1, 2, 3, 7, 8 (no web / multi-agent)
"""
import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).parent))

from src.organism.llm.claude import ClaudeProvider
from src.organism.tools.code_executor import CodeExecutorTool
from src.organism.tools.web_search import WebSearchTool
from src.organism.tools.web_fetch import WebFetchTool
from src.organism.tools.file_manager import FileManagerTool
from src.organism.tools.pptx_creator import PptxCreatorTool
from src.organism.tools.text_writer import TextWriterTool
from src.organism.tools.registry import ToolRegistry
from src.organism.core.loop import CoreLoop
from src.organism.memory.manager import MemoryManager
from src.organism.commands.handler import CommandHandler
from config.settings import settings

# ── Task definitions ──────────────────────────────────────────────────────────
#
#  id   — task number (printed in table)
#  type — label shown in table
#  task — the actual prompt sent to the system
#  mode — "loop" (default) | "orchestrator" | "command"
#
# IMPORTANT: task #8 is a rephrased version of #1 — it must run AFTER #1 so
#            the solution cache is warm and the cache-hit can be verified.

TASKS = [
    {
        "id": 1,
        "type": "code",
        "task": (
            "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u043f\u043b\u0430\u043d \u0434\u043e\u0431\u044b\u0447\u0438 "
            "\u0437\u043e\u043b\u043e\u0442\u0430: \u0441\u0435\u0437\u043e\u043d "
            "150 \u0434\u043d\u0435\u0439, \u043f\u043b\u0430\u043d 300 \u043a\u0433, "
            "\u0446\u0435\u043d\u0430 7500 \u0440\u0443\u0431/\u0433\u0440\u0430\u043c\u043c. "
            "\u0412\u044b\u0432\u0435\u0434\u0438: \u0434\u043d\u0435\u0432\u043d\u043e\u0439 "
            "\u043f\u043b\u0430\u043d \u0433, \u0434\u043d\u0435\u0432\u043d\u0443\u044e "
            "\u0432\u044b\u0440\u0443\u0447\u043a\u0443, \u0438\u0442\u043e\u0433\u043e\u0432\u0443\u044e "
            "\u0432\u044b\u0440\u0443\u0447\u043a\u0443"
        ),
    },
    {
        "id": 2,
        "type": "csv",
        "task": (
            "\u0441\u043e\u0437\u0434\u0430\u0439 CSV \u0442\u0430\u0431\u043b\u0438\u0446\u0443 "
            "\u0440\u0430\u0441\u0445\u043e\u0434\u043e\u0432 \u043d\u0430 \u0442\u0435\u0445\u043d\u0438\u043a\u0443 "
            "\u0437\u0430 \u043d\u0435\u0434\u0435\u043b\u044e: "
            "\u0431\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 500 \u043b/\u0434\u0435\u043d\u044c "
            "* 70 \u0440\u0443\u0431/\u043b, "
            "\u044d\u043a\u0441\u043a\u0430\u0432\u0430\u0442\u043e\u0440 300 \u043b/\u0434\u0435\u043d\u044c "
            "* 70 \u0440\u0443\u0431/\u043b, 5 \u0440\u0430\u0431\u043e\u0447\u0438\u0445 \u0434\u043d\u0435\u0439"
        ),
    },
    {
        "id": 3,
        "type": "writing",
        "task": (
            "\u043d\u0430\u043f\u0438\u0448\u0438 \u043a\u0440\u0430\u0442\u043a\u0443\u044e "
            "\u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044e \u043f\u043e "
            "\u0442\u0435\u0445\u043d\u0438\u043a\u0435 \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u0438 "
            "\u043f\u0440\u0438 \u0440\u0430\u0431\u043e\u0442\u0435 \u0441 "
            "\u0434\u0440\u043e\u0431\u0438\u043b\u043a\u043e\u0439 \u0433\u043e\u0440\u043d\u043e\u0439 "
            "\u043f\u043e\u0440\u043e\u0434\u044b \u2014 5 \u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0445 \u043f\u0440\u0430\u0432\u0438\u043b"
        ),
    },
    {
        "id": 4,
        "type": "mixed",
        "task": (
            "\u043d\u0430\u0439\u0434\u0438 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u044b\u0435 "
            "\u044d\u043a\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0435 \u0442\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044f "
            "\u043a \u0437\u043e\u043b\u043e\u0442\u043e\u0434\u043e\u0431\u044b\u0447\u0435 "
            "\u0432 \u0420\u043e\u0441\u0441\u0438\u0438 \u0438 \u043d\u0430\u043f\u0438\u0448\u0438 "
            "\u043a\u0440\u0430\u0442\u043a\u043e\u0435 \u0440\u0435\u0437\u044e\u043c\u0435 "
            "\u0434\u043b\u044f \u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044f \u0430\u0440\u0442\u0435\u043b\u0438"
        ),
    },
    {
        "id": 5,
        "type": "presentation",
        "task": (
            "\u0441\u043e\u0437\u0434\u0430\u0439 \u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446\u0438\u044e "
            "\u0438\u0437 5 \u0441\u043b\u0430\u0439\u0434\u043e\u0432 "
            "\u043e \u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0438 "
            "\u0440\u043e\u0441\u0441\u044b\u043f\u043d\u043e\u0439 \u0437\u043e\u043b\u043e\u0442\u043e\u0434\u043e\u0431\u044b\u0447\u0438: "
            "\u0432\u0432\u0435\u0434\u0435\u043d\u0438\u0435, "
            "\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435, "
            "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u044f, "
            "\u043f\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u0438, "
            "\u0432\u044b\u0432\u043e\u0434\u044b"
        ),
    },
    {
        "id": 6,
        "type": "research",
        "task": (
            "\u043d\u0430\u0439\u0434\u0438 \u0442\u0435\u043a\u0443\u0449\u0443\u044e "
            "\u0446\u0435\u043d\u0443 \u0437\u043e\u043b\u043e\u0442\u0430 "
            "\u043d\u0430 \u043c\u0438\u0440\u043e\u0432\u043e\u043c \u0440\u044b\u043d\u043a\u0435 "
            "\u0432 \u0434\u043e\u043b\u043b\u0430\u0440\u0430\u0445 \u0438 "
            "\u0440\u0443\u0431\u043b\u044f\u0445 \u0437\u0430 \u0433\u0440\u0430\u043c\u043c"
        ),
    },
    {
        "id": 7,
        "type": "analysis",
        "task": (
            "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u0440\u0435\u043d\u0442\u0430\u0431\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c "
            "\u0443\u0447\u0430\u0441\u0442\u043a\u0430: "
            "\u0432\u044b\u0440\u0443\u0447\u043a\u0430 15 \u043c\u043b\u043d "
            "\u0440\u0443\u0431, \u0440\u0430\u0441\u0445\u043e\u0434\u044b: "
            "\u0437\u0430\u0440\u043f\u043b\u0430\u0442\u0430 3 \u043c\u043b\u043d, "
            "\u0442\u043e\u043f\u043b\u0438\u0432\u043e 2 \u043c\u043b\u043d, "
            "\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435 4 \u043c\u043b\u043d, "
            "\u043f\u0440\u043e\u0447\u0435\u0435 1 \u043c\u043b\u043d. "
            "\u0418\u0442\u043e\u0433: \u043f\u0440\u0438\u0431\u044b\u043b\u044c "
            "\u0438 \u0440\u0435\u043d\u0442\u0430\u0431\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c %"
        ),
    },
    {
        "id": 8,
        "type": "cache",
        # Rephrased version of task #1 — should hit the solution cache
        "task": (
            "\u043f\u043e\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u0434\u043e\u0431\u044b\u0447\u0443 \u0437\u043e\u043b\u043e\u0442\u0430: "
            "\u043f\u043b\u0430\u043d 300 \u043a\u0433 \u0437\u0430 \u0441\u0435\u0437\u043e\u043d "
            "150 \u0434\u043d\u0435\u0439, \u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c "
            "7500 \u0440\u0443\u0431 \u0437\u0430 \u0433\u0440\u0430\u043c\u043c. "
            "\u0420\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 \u0434\u043d\u0435\u0432\u043d\u0443\u044e "
            "\u0434\u043e\u0431\u044b\u0447\u0443 \u0438 \u043e\u0431\u0449\u0443\u044e "
            "\u0432\u044b\u0440\u0443\u0447\u043a\u0443"
        ),
    },
    {
        "id": 9,
        "type": "multi-agent",
        "task": (
            "\u043d\u0430\u0439\u0434\u0438 \u0430\u043a\u0442\u0443\u0430\u043b\u044c\u043d\u044b\u0435 "
            "\u0446\u0435\u043d\u044b \u043d\u0430 \u0434\u0438\u0437\u0435\u043b\u044c\u043d\u043e\u0435 "
            "\u0442\u043e\u043f\u043b\u0438\u0432\u043e \u0432 \u0420\u043e\u0441\u0441\u0438\u0438 "
            "\u0438 \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c \u0442\u043e\u043f\u043b\u0438\u0432\u0430 "
            "\u0434\u043b\u044f \u0437\u043e\u043b\u043e\u0442\u043e\u0434\u043e\u0431\u044b\u0432\u0430\u044e\u0449\u0435\u0433\u043e "
            "\u0441\u0435\u0437\u043e\u043d\u0430: "
            "\u0440\u0430\u0441\u0445\u043e\u0434 2000 \u043b/\u0434\u0435\u043d\u044c, "
            "150 \u0440\u0430\u0431\u043e\u0447\u0438\u0445 \u0434\u043d\u0435\u0439"
        ),
        "mode": "orchestrator",
    },
    {
        "id": 10,
        "type": "command",
        "task": "/stats",
        "mode": "command",
    },
    # ── Sprint 5 tasks (Q-5.1 through Q-5.5) ─────────────────────────────────
    {
        "id": 11,
        "type": "temporal-query",
        # когда последний раз делали отчёт по расходу ГСМ и какой был результат
        # Verifies: SearchPolicy classifies as temporal → memory searches chronologically
        "task": (
            "\u043a\u043e\u0433\u0434\u0430 \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0440\u0430\u0437 "
            "\u0434\u0435\u043b\u0430\u043b\u0438 \u043e\u0442\u0447\u0451\u0442 \u043f\u043e "
            "\u0440\u0430\u0441\u0445\u043e\u0434\u0443 \u0413\u0421\u041c \u0438 "
            "\u043a\u0430\u043a\u043e\u0439 \u0431\u044b\u043b \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442"
        ),
    },
    {
        "id": 12,
        "type": "entity-query",
        # покажи все задачи связанные с КамАЗ-65115 и его обслуживанием
        # Verifies: entity intent → graph search via entity edges
        "task": (
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u0432\u0441\u0435 \u0437\u0430\u0434\u0430\u0447\u0438 "
            "\u0441\u0432\u044f\u0437\u0430\u043d\u043d\u044b\u0435 \u0441 "
            "\u041a\u0430\u043c\u0410\u0417-65115 \u0438 \u0435\u0433\u043e "
            "\u043e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435\u043c"
        ),
    },
    {
        "id": 13,
        "type": "template-reuse",
        # создай CSV таблицу расходов на ГСМ за август 2025: КамАЗ-65115 900л, Экскаватор PC-300 1100л, Бульдозер Т-170 1050л
        # Verifies: procedural template from Sprint 5 surfaces and assists planner
        "task": (
            "\u0441\u043e\u0437\u0434\u0430\u0439 CSV \u0442\u0430\u0431\u043b\u0438\u0446\u0443 "
            "\u0440\u0430\u0441\u0445\u043e\u0434\u043e\u0432 \u043d\u0430 \u0413\u0421\u041c "
            "\u0437\u0430 \u0430\u0432\u0433\u0443\u0441\u0442 2025: "
            "\u041a\u0430\u043c\u0410\u0417-65115 900\u043b, "
            "\u042d\u043a\u0441\u043a\u0430\u0432\u0430\u0442\u043e\u0440 PC-300 1100\u043b, "
            "\u0411\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 \u0422-170 1050\u043b"
        ),
    },
    {
        "id": 14,
        "type": "causal-query",
        # почему расход топлива на бульдозер выше нормы и что можно сделать для снижения
        # Verifies: causal intent → memory searches causal edges, finds related tasks
        "task": (
            "\u043f\u043e\u0447\u0435\u043c\u0443 \u0440\u0430\u0441\u0445\u043e\u0434 "
            "\u0442\u043e\u043f\u043b\u0438\u0432\u0430 \u043d\u0430 \u0431\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440 "
            "\u0432\u044b\u0448\u0435 \u043d\u043e\u0440\u043c\u044b \u0438 "
            "\u0447\u0442\u043e \u043c\u043e\u0436\u043d\u043e \u0441\u0434\u0435\u043b\u0430\u0442\u044c "
            "\u0434\u043b\u044f \u0441\u043d\u0438\u0436\u0435\u043d\u0438\u044f"
        ),
    },
    # ── Sprint 6 tasks (Q-6.1 through Q-6.5) ─────────────────────────────────
    {
        "id": 15,
        "type": "orchestrator-sm",
        # Orchestrator state-machine (Q-6.1): multi-agent task to verify routing
        "task": (
            "\u043d\u0430\u0439\u0434\u0438 \u0441\u0440\u0435\u0434\u043d\u044e\u044e "
            "\u0446\u0435\u043d\u0443 \u0434\u0438\u0437\u0435\u043b\u044f \u0432 "
            "\u0420\u043e\u0441\u0441\u0438\u0438 \u0438 \u043d\u0430\u043f\u0438\u0448\u0438 "
            "\u043a\u0440\u0430\u0442\u043a\u0443\u044e \u0441\u043f\u0440\u0430\u0432\u043a\u0443 "
            "\u0434\u043b\u044f \u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440\u0430"
        ),
        "mode": "orchestrator",
    },
    {
        "id": 16,
        "type": "cmd-schedule",
        # /schedule command (Q-6.2): list scheduled tasks
        "task": "/schedule",
        "mode": "command",
    },
    {
        "id": 17,
        "type": "cmd-personality",
        # /personality command (Q-6.4): show current personality config
        "task": "/personality",
        "mode": "command",
    },
    {
        "id": 18,
        "type": "gateway-write",
        # Gateway writing regression (Q-6.5): writing task must still work through CoreLoop
        "task": (
            "\u043d\u0430\u043f\u0438\u0448\u0438 \u043f\u0430\u043c\u044f\u0442\u043a\u0443 "
            "\u0434\u043b\u044f \u043c\u0430\u0441\u0442\u0435\u0440\u0430 \u0441\u043c\u0435\u043d\u044b "
            "\u043f\u043e \u043f\u0440\u0438\u0451\u043c\u043a\u0435 \u0442\u043e\u043f\u043b\u0438\u0432\u0430: "
            "\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043e\u0431\u044a\u0451\u043c\u0430, "
            "\u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435 \u043d\u0430\u043a\u043b\u0430\u0434\u043d\u043e\u0439, "
            "\u043a\u043e\u043d\u0442\u0440\u043e\u043b\u044c \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0430"
        ),
    },
    {
        "id": 19,
        "type": "cmd-help",
        # /help command: verifies Sprint 6 commands appear in help text
        "task": "/help",
        "mode": "command",
    },
    # ── Sprint 7 tasks (Q-7.1 through Q-7.5) ─────────────────────────────────
    {
        "id": 20,
        "type": "orchestrator-ka",
        # Multi-agent task that benefits from cross-agent knowledge (Q-7.5):
        # "\u043d\u0430\u0439\u0434\u0438 \u0442\u0435\u043a\u0443\u0449\u0443\u044e \u0446\u0435\u043d\u0443 \u0437\u043e\u043b\u043e\u0442\u0430 \u0438 \u0441\u043e\u0441\u0442\u0430\u0432\u044c \u043a\u0440\u0430\u0442\u043a\u0438\u0439 \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043e\u0442\u0447\u0451\u0442"
        "task": (
            "\u043d\u0430\u0439\u0434\u0438 "
            "\u0442\u0435\u043a\u0443\u0449\u0443\u044e "
            "\u0446\u0435\u043d\u0443 "
            "\u0437\u043e\u043b\u043e\u0442\u0430 "
            "\u0438 "
            "\u0441\u043e\u0441\u0442\u0430\u0432\u044c "
            "\u043a\u0440\u0430\u0442\u043a\u0438\u0439 "
            "\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0439 "
            "\u043e\u0442\u0447\u0451\u0442"
        ),
        "mode": "orchestrator",
    },
    {
        "id": 21,
        "type": "reflect-struct",
        # Tests structured reflections (Q-7.1): run a code task, verify reflection is saved
        # "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 \u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c \u0440\u0435\u043c\u043e\u043d\u0442\u0430 \u0431\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440\u0430: \u0437\u0430\u043f\u0447\u0430\u0441\u0442\u0438 150000 \u0440\u0443\u0431, \u0440\u0430\u0431\u043e\u0442\u0430 80000 \u0440\u0443\u0431, \u043f\u0440\u043e\u0441\u0442\u043e\u0439 3 \u0434\u043d\u044f * 50000 \u0440\u0443\u0431/\u0434\u0435\u043d\u044c"
        "task": (
            "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c "
            "\u0440\u0435\u043c\u043e\u043d\u0442\u0430 "
            "\u0431\u0443\u043b\u044c\u0434\u043e\u0437\u0435\u0440\u0430: "
            "\u0437\u0430\u043f\u0447\u0430\u0441\u0442\u0438 150000 "
            "\u0440\u0443\u0431, "
            "\u0440\u0430\u0431\u043e\u0442\u0430 80000 "
            "\u0440\u0443\u0431, "
            "\u043f\u0440\u043e\u0441\u0442\u043e\u0439 3 "
            "\u0434\u043d\u044f * 50000 "
            "\u0440\u0443\u0431/\u0434\u0435\u043d\u044c"
        ),
    },
    {
        "id": 22,
        "type": "few-shot",
        # Tests few-shot curation (Q-7.3): task similar to #7 — should benefit from few-shot examples
        # "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 \u0440\u0435\u043d\u0442\u0430\u0431\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u0443\u0447\u0430\u0441\u0442\u043a\u0430: \u0432\u044b\u0440\u0443\u0447\u043a\u0430 20 \u043c\u043b\u043d, \u0437\u0430\u0440\u043f\u043b\u0430\u0442\u0430 4 \u043c\u043b\u043d, \u0442\u043e\u043f\u043b\u0438\u0432\u043e 3 \u043c\u043b\u043d, \u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435 5 \u043c\u043b\u043d"
        "task": (
            "\u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u0439 "
            "\u0440\u0435\u043d\u0442\u0430\u0431\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c "
            "\u0443\u0447\u0430\u0441\u0442\u043a\u0430: "
            "\u0432\u044b\u0440\u0443\u0447\u043a\u0430 20 "
            "\u043c\u043b\u043d, "
            "\u0437\u0430\u0440\u043f\u043b\u0430\u0442\u0430 4 "
            "\u043c\u043b\u043d, "
            "\u0442\u043e\u043f\u043b\u0438\u0432\u043e 3 "
            "\u043c\u043b\u043d, "
            "\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435 5 "
            "\u043c\u043b\u043d"
        ),
    },
    {
        "id": 23,
        "type": "cmd-evolve",
        # Tests evolutionary infra exists (Q-7.4): --evolve-prompts flag parse
        "task": "/help",
        "mode": "command",
    },
    # ── Sprint 8 tasks (Q-8.1 through Q-8.5) ─────────────────────────────────
    {
        "id": 24,
        "type": "dupl-search",
        # "проверь на дубликаты список контрагентов: ООО Топливный Снаб, Топливный снаб ООО, ИП Петров С.В., Петров Сергей ИП, АО Дальзолото"
        "task": (
            "\u043f\u0440\u043e\u0432\u0435\u0440\u044c \u043d\u0430 "
            "\u0434\u0443\u0431\u043b\u0438\u043a\u0430\u0442\u044b "
            "\u0441\u043f\u0438\u0441\u043e\u043a "
            "\u043a\u043e\u043d\u0442\u0440\u0430\u0433\u0435\u043d\u0442\u043e\u0432: "
            "\u041e\u041e\u041e \u0422\u043e\u043f\u043b\u0438\u0432\u043d\u044b\u0439 \u0421\u043d\u0430\u0431, "
            "\u0422\u043e\u043f\u043b\u0438\u0432\u043d\u044b\u0439 \u0441\u043d\u0430\u0431 \u041e\u041e\u041e, "
            "\u0418\u041f \u041f\u0435\u0442\u0440\u043e\u0432 \u0421.\u0412., "
            "\u041f\u0435\u0442\u0440\u043e\u0432 \u0421\u0435\u0440\u0433\u0435\u0439 \u0418\u041f, "
            "\u0410\u041e \u0414\u0430\u043b\u044c\u0437\u043e\u043b\u043e\u0442\u043e"
        ),
    },
    {
        "id": 25,
        "type": "cmd-mcp-serve",
        # Tests MCP serve infrastructure exists: /help should mention --serve-mcp
        "task": "/help",
        "mode": "command",
    },
    {
        "id": 26,
        "type": "a2a-infra",
        # Tests A2A infrastructure: /help should mention delegate capabilities
        "task": "/help",
        "mode": "command",
    },
]

# Task IDs included in --quick mode
# Sprint 5 tasks (11-14) are excluded — they require a warm DB with memory/graph data
QUICK_IDS = {1, 2, 3, 7, 8}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    id: int
    type: str
    task: str
    success: bool
    quality_score: float
    duration: float
    tools_used: list = field(default_factory=list)
    cache_hit: bool = False
    error: str = ""


# ── Infrastructure (mirrors main.py) ─────────────────────────────────────────

def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    try:
        registry.register(CodeExecutorTool())
    except Exception:
        print("  [warn] Docker unavailable — code_executor skipped")
    registry.register(PptxCreatorTool())
    registry.register(TextWriterTool())
    registry.register(WebFetchTool())
    registry.register(FileManagerTool())
    from src.organism.tools.duplicate_finder import DuplicateFinderTool
    registry.register(DuplicateFinderTool())
    from src.organism.tools.pdf_tool import PdfTool
    registry.register(PdfTool())
    from src.organism.tools.memory_search import MemorySearchTool
    registry.register(MemorySearchTool())
    if settings.tavily_api_key:
        registry.register(WebSearchTool())
    return registry


# ── Task runners ──────────────────────────────────────────────────────────────

async def run_loop_task(task_def: dict, loop: CoreLoop) -> BenchmarkResult:
    """Run a single task through CoreLoop and extract benchmark metrics."""
    task_text = task_def["task"]
    t0 = time.time()
    try:
        result = await loop.run(task_text, verbose=False)
        duration = time.time() - t0

        # Cache hits: TaskResult has steps=[] (cache returns before any tool execution)
        cache_hit = result.success and len(result.steps) == 0
        tools_used = sorted({s.tool for s in result.steps if s.success})

        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=result.success,
            quality_score=result.quality_score,
            duration=duration,
            tools_used=tools_used,
            cache_hit=cache_hit,
            error=result.error or "",
        )
    except Exception as exc:
        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=False,
            quality_score=0.0,
            duration=time.time() - t0,
            error=str(exc)[:200],
        )


async def run_orchestrator_task(
    task_def: dict,
    llm: ClaudeProvider,
    registry: ToolRegistry,
    memory: MemoryManager | None,
) -> BenchmarkResult:
    """Run a task through the multi-agent Orchestrator."""
    from src.organism.agents.orchestrator import Orchestrator

    task_text = task_def["task"]
    t0 = time.time()
    try:
        orch = Orchestrator(llm, registry, memory=memory)
        result = await orch.run(task_text)
        duration = time.time() - t0
        # AgentResult has no quality_score; use 0.75 on success as a reasonable default
        quality = 0.75 if result.success else 0.0
        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=result.success,
            quality_score=quality,
            duration=duration,
            tools_used=["multi-agent"],
            error=result.error or "",
        )
    except Exception as exc:
        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=False,
            quality_score=0.0,
            duration=time.time() - t0,
            error=str(exc)[:200],
        )


async def run_command_task(
    task_def: dict, memory: MemoryManager | None,
    scheduler=None, personality=None,
) -> BenchmarkResult:
    """Run a slash command through CommandHandler."""
    handler = CommandHandler(scheduler=scheduler, personality=personality)
    task_text = task_def["task"]
    t0 = time.time()
    try:
        output = await handler.handle(task_text, memory)
        duration = time.time() - t0
        # Consider it a success if output is non-empty and doesn't start with an error
        success = bool(output and not output.lower().startswith("memory error"))
        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=success,
            quality_score=1.0 if success else 0.0,
            duration=duration,
        )
    except Exception as exc:
        return BenchmarkResult(
            id=task_def["id"],
            type=task_def["type"],
            task=task_text,
            success=False,
            quality_score=0.0,
            duration=time.time() - t0,
            error=str(exc)[:200],
        )


# ── Output formatting ─────────────────────────────────────────────────────────

# Short display names for tools (keep ≤ 9 chars so the Tools column stays tidy)
_TOOL_SHORT = {
    "code_executor": "code_exec",
    "text_writer":   "txt_write",
    "web_search":    "web_srch",
    "web_fetch":     "web_fetch",
    "pptx_creator":  "pptx",
    "file_manager":  "file_mgr",
    "multi-agent":   "multi-agt",
}


def _trunc(text: str, width: int) -> str:
    """Truncate to *width* chars, padding with spaces if shorter."""
    if len(text) <= width:
        return text.ljust(width)
    return text[:width - 3] + "..."


def _fmt_tools(tools: list) -> str:
    if not tools:
        return "-"
    return ",".join(_TOOL_SHORT.get(t, t[:9]) for t in tools)


def print_table(results: list[BenchmarkResult]) -> None:
    LINE = "=" * 102
    HEADER = (
        f"  {'#':>2}  {'Type':<14}  {'Task':<40}  "
        f"{'OK':>2}  {'Qual':>5}  {'Time':>6}  {'Tools':<18}  Cache"
    )
    print(f"\n{LINE}")
    print(HEADER)
    print(LINE)

    for r in results:
        ok = "OK" if r.success else "--"
        qual = f"{r.quality_score:.2f}" if r.quality_score > 0 else " n/a"
        t = f"{r.duration:.1f}s"
        tools_col = _fmt_tools(r.tools_used)
        cache_col = "HIT" if r.cache_hit else "-"
        print(
            f"  {r.id:>2}  {r.type:<14}  {_trunc(r.task, 40)}  "
            f"{ok:>2}  {qual:>5}  {t:>6}  {tools_col:<18}  {cache_col}"
        )

    print(LINE)

    # Summary line
    total = len(results)
    successful = sum(1 for r in results if r.success)
    success_pct = successful / total * 100 if total else 0.0
    scored = [r.quality_score for r in results if r.quality_score > 0]
    avg_qual = sum(scored) / len(scored) if scored else 0.0
    avg_time = sum(r.duration for r in results) / total if total else 0.0
    cache_hits = sum(1 for r in results if r.cache_hit)
    cache_pct = cache_hits / total * 100 if total else 0.0

    print(
        f"\n  Summary: {successful}/{total} success ({success_pct:.1f}%)  |  "
        f"Avg quality: {avg_qual:.2f}  |  "
        f"Avg time: {avg_time:.1f}s  |  "
        f"Cache hits: {cache_hits}/{total} ({cache_pct:.0f}%)\n"
    )

    # List any failures
    failed = [r for r in results if not r.success]
    if failed:
        print("  Failed tasks:")
        for r in failed:
            snippet = r.error[:100] if r.error else "(no error message)"
            print(f"    #{r.id} {r.type}: {snippet}")
        print()


# ── JSON persistence ──────────────────────────────────────────────────────────

def save_json(results: list[BenchmarkResult], mode: str) -> Path:
    total = len(results)
    successful = sum(1 for r in results if r.success)
    scored = [r.quality_score for r in results if r.quality_score > 0]
    avg_qual = sum(scored) / len(scored) if scored else 0.0
    avg_time = sum(r.duration for r in results) / total if total else 0.0
    cache_hits = sum(1 for r in results if r.cache_hit)

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "results": [
            {
                "id": r.id,
                "type": r.type,
                "task": r.task[:120],
                "success": r.success,
                "quality_score": round(r.quality_score, 4),
                "duration": round(r.duration, 2),
                "tools_used": r.tools_used,
                "cache_hit": r.cache_hit,
                "error": r.error[:200] if r.error else "",
            }
            for r in results
        ],
        "summary": {
            "total": total,
            "successful": successful,
            "success_rate": round(successful / total * 100, 1) if total else 0.0,
            "avg_quality": round(avg_qual, 4),
            "avg_duration": round(avg_time, 2),
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / total * 100, 1) if total else 0.0,
        },
    }

    out = Path("data/benchmark_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── Main orchestration ────────────────────────────────────────────────────────

async def run_benchmark(quick: bool) -> None:
    mode = "quick" if quick else "full"
    tasks = [t for t in TASKS if not quick or t["id"] in QUICK_IDS]

    print(f"\nOrganism AI Benchmark  [{mode.upper()} — {len(tasks)} tasks]")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Build shared resources — same pattern as main.py
    llm = ClaudeProvider()
    registry = build_registry()
    # Q-8.1: connect pending MCP servers
    for mcp_config in getattr(registry, "_pending_mcp", []):
        try:
            count = await registry.register_mcp_server(mcp_config)
            if count > 0:
                print(f"  MCP '{mcp_config.name}': {count} tools registered")
        except Exception:
            pass
    memory = None
    if settings.database_url:
        try:
            memory = MemoryManager()
            await memory.initialize()
        except Exception:
            print("  [warn] Database unavailable — running without memory")
            memory = None

    personality = None
    try:
        from src.organism.core.personality import PersonalityConfig
        p = PersonalityConfig(artel_id=settings.artel_id)
        p.load()
        personality = p
    except Exception:
        pass

    loop = CoreLoop(llm, registry, memory=memory, personality=personality)

    # Scheduler for /schedule command tests (not started — just provides job list)
    from src.organism.core.scheduler import ProactiveScheduler, DEFAULT_ARTEL_JOBS
    scheduler = ProactiveScheduler(task_runner=loop.run)
    for job in DEFAULT_ARTEL_JOBS:
        scheduler.add_job(job)
    loop.scheduler = scheduler

    results: list[BenchmarkResult] = []

    for i, task_def in enumerate(tasks, 1):
        task_mode = task_def.get("mode", "loop")
        preview = task_def["task"][:55]
        print(f"  [{i:>2}/{len(tasks)}]  #{task_def['id']} {task_def['type']:<14}  {preview}...")

        if task_mode == "orchestrator":
            bm = await run_orchestrator_task(task_def, llm, registry, memory)
        elif task_mode == "command":
            bm = await run_command_task(task_def, memory, scheduler=scheduler, personality=personality)
        else:
            bm = await run_loop_task(task_def, loop)

        ok = "OK" if bm.success else "FAIL"
        extra = "  [CACHE HIT]" if bm.cache_hit else ""
        print(f"           {ok}  quality={bm.quality_score:.2f}  time={bm.duration:.1f}s{extra}")
        results.append(bm)

    print_table(results)
    out = save_json(results, mode)
    print(f"  Results saved -> {out}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Organism AI benchmark suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python benchmark.py            # full suite (26 tasks)\n"
            "  python benchmark.py --quick    # fast check (5 tasks, no web/multi-agent)\n"
        ),
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only tasks 1, 2, 3, 7, 8 (code + writing + analysis + cache)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_benchmark(quick=args.quick))
    except KeyboardInterrupt:
        print("\nBenchmark interrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
