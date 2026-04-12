"""Code Health Report — deterministic checks for codebase consistency.

Usage: python scripts/code_health.py
Exit code: 0 = all pass, 1 = issues found.
"""

import os
import re
import sys
from pathlib import Path

# Resolve project root (scripts/ is one level deep)
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "organism"


# ── Utilities ───────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _all_py_files(base: Path) -> list[Path]:
    """Return all .py files under base, excluding __pycache__."""
    return sorted(
        p for p in base.rglob("*.py")
        if "__pycache__" not in str(p)
    )


# ── Check 1: File Structure Sync ────────────────────────────────────────

def check_file_structure() -> tuple[bool, str]:
    """Compare real .py files in src/organism/ vs CLAUDE.md file tree."""
    claude_md = _read(ROOT / "CLAUDE.md")

    # Extract .py filenames mentioned in CLAUDE.md
    doc_files: set[str] = set()
    for m in re.finditer(r'(\w+\.py)', claude_md):
        doc_files.add(m.group(1))
    # CLAUDE.md lists tools by bare name across multiple lines:
    #   always: code_executor, pptx_creator, ...
    #           file_manager, duplicate_finder, ...
    #   conditional: web_search (tavily), ...
    # Grab the whole tools block and extract snake_case names
    tools_match = re.search(
        r'#\s*always:\s*(.+?)#\s*\+\s*mcp_client',
        claude_md, re.DOTALL,
    )
    if tools_match:
        tools_block = re.sub(r'\([^)]*\)', '', tools_match.group(1))
        tools_block = re.sub(r'#', ' ', tools_block)
        skip = {"always", "conditional", "telegram", "only"}
        for name in re.findall(r'(\w+_\w+|\w{4,})', tools_block):
            if name not in skip:
                doc_files.add(name + ".py")

    # Get actual .py filenames in src/organism/
    code_files: set[str] = set()
    for p in _all_py_files(SRC):
        if p.name != "__init__.py":
            code_files.add(p.name)

    missing_in_docs = code_files - doc_files
    orphan_in_docs = doc_files - code_files

    # Filter out known non-src files that might appear in CLAUDE.md
    non_src = {
        "benchmark.py", "pre_commit_check.py", "main.py", "settings.py",
        "health_check.py", "code_health.py",
    }
    orphan_in_docs -= non_src

    issues = []
    if missing_in_docs:
        issues.append(f"  Missing in docs: {', '.join(sorted(missing_in_docs))}")
    if orphan_in_docs:
        issues.append(f"  Orphan in docs: {', '.join(sorted(orphan_in_docs))}")

    if issues:
        return False, (
            f"File Structure Sync: {len(code_files)} files in code, "
            f"{len(doc_files)} refs in docs\n" + "\n".join(issues)
        )
    return True, (
        f"File Structure Sync: {len(code_files)} files in code, "
        f"{len(doc_files)} refs in docs"
    )


# ── Check 2: Tool Registry Sync ─────────────────────────────────────────

def _extract_registered_tools(filepath: Path) -> set[str]:
    """Extract tool class names from registry.register() calls in build_registry()."""
    text = _read(filepath)

    # Find build_registry function body
    match = re.search(r'def build_registry\b.*?(?=\ndef |\Z)', text, re.DOTALL)
    if not match:
        return set()
    body = match.group(0)

    tools: set[str] = set()
    # Match both registry.register(Tool()) and _register(Tool()) patterns
    for m in re.finditer(r'(?:registry\.register|_register)\(\s*(\w+)\s*\(', body):
        tools.add(m.group(1))
    return tools


def check_tool_registry() -> tuple[bool, str]:
    """Compare tools registered in main.py vs benchmark.py.

    CAPABILITY-1: build_registry moved to src/organism/tools/bootstrap.py.
    Both main.py and benchmark.py import from there (single source).
    """
    bootstrap = ROOT / "src" / "organism" / "tools" / "bootstrap.py"
    main_tools = _extract_registered_tools(bootstrap)
    bench_tools = main_tools  # same function, imported by both

    # Exclude tools that are intentionally different
    # ConfirmUserTool: registered in run_telegram(), not build_registry()
    # DelegateToAgentTool: conditional on A2A_PEERS env
    # TelegramSenderTool: conditional on TELEGRAM_BOT_TOKEN env
    # DevReviewTool: conditional on DEV_MODE
    conditional = {"DelegateToAgentTool", "TelegramSenderTool", "DevReviewTool"}
    main_tools -= conditional
    bench_tools -= conditional

    only_main = main_tools - bench_tools
    only_bench = bench_tools - main_tools

    issues = []
    if only_main:
        issues.append(f"  Only in main.py: {', '.join(sorted(only_main))}")
    if only_bench:
        issues.append(f"  Only in benchmark.py: {', '.join(sorted(only_bench))}")

    if issues:
        return False, "Tool Registry Sync:\n" + "\n".join(issues)
    return True, f"Tool Registry Sync: {len(main_tools)} tools match"


# ── Check 3: Command Sync ────────────────────────────────────────────────

def _extract_help_commands(filepath: Path) -> set[str]:
    """Extract /command names from HELP_TEXT in handler.py."""
    text = _read(filepath)
    # HELP_TEXT = (\n...\n) — closing paren is on its own line
    match = re.search(r'HELP_TEXT\s*=\s*\((.+?)\n\)', text, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r'/(\w+)', match.group(1)))


def _extract_conventions_commands(filepath: Path) -> set[str]:
    """Extract /command names from Conventions.md bot commands section."""
    text = _read(filepath)
    # Find the code block after "Команды бота" header
    # Use regex to find the fenced code block in that section
    match = re.search(
        r'##\s+\u041a\u043e\u043c\u0430\u043d\u0434\u044b\s+\u0431\u043e\u0442\u0430'
        r'.*?```(.*?)```',
        text, re.DOTALL,
    )
    if not match:
        return set()
    block = match.group(1)
    return set(re.findall(r'/(\w+)', block))


def check_command_sync() -> tuple[bool, str]:
    """Compare commands in handler.py HELP_TEXT vs CONVENTIONS.md."""
    handler = ROOT / "src" / "organism" / "commands" / "handler.py"
    conventions = ROOT / "CONVENTIONS.md"

    help_cmds = _extract_help_commands(handler)
    conv_cmds = _extract_conventions_commands(conventions)

    if not help_cmds:
        return False, "Command Sync: could not parse HELP_TEXT"
    if not conv_cmds:
        return False, "Command Sync: could not parse CONVENTIONS.md"

    only_help = help_cmds - conv_cmds
    only_conv = conv_cmds - help_cmds

    issues = []
    if only_help:
        issues.append(f"  Only in HELP_TEXT: /{', /'.join(sorted(only_help))}")
    if only_conv:
        issues.append(f"  Only in CONVENTIONS.md: /{', /'.join(sorted(only_conv))}")

    if issues:
        return False, "Command Sync:\n" + "\n".join(issues)
    return True, f"Command Sync: {len(help_cmds)} commands match"


# ── Check 4: Orphan Python Files ─────────────────────────────────────────

def check_orphan_files() -> tuple[bool, str]:
    """Find .py files in src/organism/ not imported anywhere."""
    all_files = _all_py_files(SRC)
    all_files = [f for f in all_files if f.name != "__init__.py"]

    # Files intentionally not imported elsewhere
    _ORPHAN_EXCLUDE = {
        # decomposer.py: reserved for future use (FIX-44 disabled from main path, kept for complex multi-phase tasks)
        "decomposer.py",
        # planner.py: used by loop.py via direct import of Planner, PlanStep
        "planner.py",
    }
    all_files = [f for f in all_files if f.name not in _ORPHAN_EXCLUDE]

    # Collect all source text for import searching
    search_paths = list(_all_py_files(SRC))
    search_paths.extend([ROOT / "main.py", ROOT / "benchmark.py"])
    all_source = ""
    for p in search_paths:
        all_source += _read(p) + "\n"

    orphans = []
    for f in all_files:
        # Build possible import paths
        rel = f.relative_to(ROOT / "src" / "organism")
        parts = list(rel.with_suffix("").parts)
        # e.g. core/loop.py -> src.organism.core.loop
        module_path = "src.organism." + ".".join(parts)

        # Also check just the module name (from X import Y)
        module_name = parts[-1]
        parent_path = ".".join(["src", "organism"] + parts[:-1])

        # Check if this module is referenced (absolute or relative imports)
        found = False
        if module_path in all_source:
            found = True
        elif f"from {parent_path} import" in all_source:
            # Check if the specific name is imported
            pattern = rf'from\s+{re.escape(parent_path)}\s+import\s+[^#\n]*\b{re.escape(module_name)}\b'
            if re.search(pattern, all_source):
                found = True
        elif f"import {module_path}" in all_source:
            found = True

        # Check relative imports: from .module_name import ...
        if not found:
            rel_pattern = rf'from\s+\.{re.escape(module_name)}\s+import'
            if re.search(rel_pattern, all_source):
                found = True

        if not found:
            orphans.append(str(rel))

    if orphans:
        return False, (
            f"Orphan Files: {len(orphans)} file(s) not imported anywhere\n"
            + "\n".join(f"  {o}" for o in sorted(orphans))
        )
    return True, f"Orphan Files: all {len(all_files)} files are imported"


# ── Check 5: Dead Imports ────────────────────────────────────────────────

def check_dead_imports() -> tuple[bool, str]:
    """Find imports from src.organism.* that are unused in the same file."""
    all_files = _all_py_files(SRC)
    all_files.extend([ROOT / "main.py", ROOT / "benchmark.py"])

    dead: list[str] = []
    for f in all_files:
        text = _read(f)
        lines = text.split("\n")
        for i, line in enumerate(lines):
            # Match: from src.organism.X import Y, Z
            m = re.match(
                r'^from\s+src\.organism\.\S+\s+import\s+(.+)', line
            )
            if not m:
                continue

            # Skip TYPE_CHECKING imports
            # Look back for "if TYPE_CHECKING:" guard
            if any("TYPE_CHECKING" in lines[j] for j in range(max(0, i - 3), i)):
                continue

            imported = m.group(1)
            # Parse individual names (handle multi-line, aliases)
            names = []
            for part in imported.split(","):
                part = part.strip().rstrip("\\").strip()
                if not part or part == "(":
                    continue
                # Handle "X as Y" — check for Y
                if " as " in part:
                    names.append(part.split(" as ")[-1].strip())
                else:
                    # Remove trailing parenthesis or comments
                    name = re.sub(r'[)#].*', '', part).strip()
                    if name:
                        names.append(name)

            # Check usage in rest of file
            rest = "\n".join(lines[i + 1:])
            for name in names:
                if not name or name.startswith("#"):
                    continue
                # Check if name appears as a word in rest of file
                if not re.search(rf'\b{re.escape(name)}\b', rest):
                    rel = f.relative_to(ROOT)
                    dead.append(f"  {rel}:{i+1}: '{name}' unused")

    if dead:
        # Cap output
        shown = dead[:20]
        extra = f"\n  ... and {len(dead) - 20} more" if len(dead) > 20 else ""
        return False, (
            f"Dead Imports: {len(dead)} potentially unused import(s)\n"
            + "\n".join(shown) + extra
        )
    return True, "Dead Imports: no issues found"


# ── Check 6: Benchmark Task Count ────────────────────────────────────────

def check_benchmark_count() -> tuple[bool, str]:
    """Compare TASKS count in benchmark.py vs docs."""
    bench_text = _read(ROOT / "benchmark.py")
    # Count tasks in TASKS list only (not other "id": occurrences)
    tasks_match = re.search(r'TASKS\s*=\s*\[(.+?)\n\]', bench_text, re.DOTALL)
    tasks_block = tasks_match.group(1) if tasks_match else bench_text
    task_count = len(re.findall(r'"id"\s*:', tasks_block))

    claude_text = _read(ROOT / "CLAUDE.md")
    roadmap_text = _read(ROOT / "organism_ai_roadmap.md")

    issues = []

    # Check CLAUDE.md for "X/X" or "X tasks"
    m = re.search(r'Benchmark:\s*(\d+)/(\d+)', claude_text)
    if m:
        doc_count = int(m.group(1))
        if doc_count != task_count:
            issues.append(
                f"  CLAUDE.md says {doc_count}, benchmark.py has {task_count}"
            )

    # Check roadmap
    m = re.search(r'Benchmark:\s*(\d+)/(\d+)', roadmap_text)
    if m:
        road_count = int(m.group(1))
        if road_count != task_count:
            issues.append(
                f"  Roadmap says {road_count}, benchmark.py has {task_count}"
            )

    if issues:
        return False, f"Benchmark Count:\n" + "\n".join(issues)
    return True, f"Benchmark Count: {task_count} tasks in code and docs"


# ── Check 7: Migration Order ─────────────────────────────────────────────

def check_migration_order() -> tuple[bool, str]:
    """Verify _MIGRATIONS list has sequential version numbers."""
    db_text = _read(ROOT / "src" / "organism" / "memory" / "database.py")

    # Extract migration version numbers
    match = re.search(r'_MIGRATIONS\s*=\s*\[(.+?)\]', db_text, re.DOTALL)
    if not match:
        return False, "Migration Order: could not find _MIGRATIONS list"

    versions = [int(v) for v in re.findall(r'\(\s*(\d+)\s*,', match.group(1))]

    if not versions:
        return False, "Migration Order: no migrations found"

    # Check sequential
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        gaps = set(expected) - set(versions)
        extra = set(versions) - set(expected)
        issues = []
        if gaps:
            issues.append(f"  Missing versions: {sorted(gaps)}")
        if extra:
            issues.append(f"  Unexpected versions: {sorted(extra)}")
        if versions != sorted(versions):
            issues.append("  Versions not in order")
        return False, "Migration Order:\n" + "\n".join(issues)

    return True, f"Migration Order: {len(versions)} migrations, sequential"


# ── Check 8: Artel ID Coverage ──────────────────────────────────────────

_ARTEL_TABLES = {
    "task_memories", "solution_cache", "agent_reflections",
    "user_profile", "knowledge_rules", "procedural_templates",
    "chat_messages", "few_shot_examples", "memory_edges",
}

_ARTEL_ORM_CLASSES = {
    "TaskMemory", "SolutionCacheEntry", "AgentReflection", "UserProfile",
    "KnowledgeRule", "ProceduralTemplate", "ChatMessage", "FewShotExample",
    "MemoryEdge",
}

_ARTEL_EXCLUDE = {
    "database.py",       # ORM definitions + migrations
    "state_machine.py",  # no DB access
    "planner.py",        # receives pre-filtered data, no direct DB queries
    "user_facts.py",     # isolates by user_id, not artel_id
}

_SQL_KEYWORDS = re.compile(
    r'\b(SELECT|INSERT|UPDATE|DELETE|FROM|JOIN|INTO)\b', re.IGNORECASE,
)

_ORM_QUERY_CONTEXT = re.compile(
    r'(select\(|session\.add\(|session\.execute\(|\.where\()',
)


def check_artel_id_coverage() -> tuple[bool, str]:
    """Verify every file that queries artel_id tables also references artel_id."""
    targets: list[Path] = _all_py_files(SRC)
    targets.extend([ROOT / "main.py", ROOT / "benchmark.py"])

    violations: list[str] = []
    checked = 0

    for fpath in targets:
        if fpath.name in _ARTEL_EXCLUDE:
            continue

        content = _read(fpath)
        if not content:
            continue

        # Find which artel tables this file references in query context
        touched_tables: set[str] = set()

        for line in content.split("\n"):
            # SQL context: line mentions table name + SQL keyword
            for table in _ARTEL_TABLES:
                if table in line and _SQL_KEYWORDS.search(line):
                    touched_tables.add(table)

            # ORM context: line mentions model class + query pattern
            if _ORM_QUERY_CONTEXT.search(line):
                for cls in _ARTEL_ORM_CLASSES:
                    if cls in line:
                        touched_tables.add(cls)

        if not touched_tables:
            continue

        checked += 1
        if "artel_id" not in content:
            rel = fpath.relative_to(ROOT)
            tables_str = ", ".join(sorted(touched_tables))
            violations.append(f"  {rel} references {{{tables_str}}} but has no artel_id filtering")

    if violations:
        return False, (
            f"Artel ID Coverage: {checked} files checked\n"
            + "\n".join(sorted(violations))
        )
    return True, f"Artel ID Coverage: {checked} files checked, all have artel_id filtering"


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> int:
    checks = [
        check_file_structure,
        check_tool_registry,
        check_command_sync,
        check_orphan_files,
        check_dead_imports,
        check_benchmark_count,
        check_migration_order,
        check_artel_id_coverage,
    ]

    print("=== Code Health Report ===\n")

    passed = 0
    failed = 0
    for check in checks:
        ok, msg = check()
        tag = "[PASS]" if ok else "[FAIL]"
        print(f"{tag} {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    total = passed + failed
    print(f"\n=== Summary: {passed}/{total} passed", end="")
    if failed:
        print(f", {failed} issue(s) found ===")
    else:
        print(" ===")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
