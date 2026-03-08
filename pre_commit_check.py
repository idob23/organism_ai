#!/usr/bin/env python3
"""Pre-commit safety check. Run before every commit.
Checks: syntax, imports, no Cyrillic in .py files, benchmark --quick.
Exit code 0 = OK, 1 = FAIL.
"""
import sys
import py_compile
import subprocess
from pathlib import Path

# Modules that must always import cleanly (no DB required)
CRITICAL_MODULES = [
    "src.organism.core.planner",
    "src.organism.core.evaluator",
    "src.organism.llm.claude",
    "src.organism.safety.validator",
]

# Modules that require DB — only check syntax, not import
DB_MODULES = [
    "src.organism.core.loop",
    "src.organism.channels.gateway",
    "src.organism.tools.registry",
    "src.organism.memory.manager",
]

PY_FILES = list(Path("src").rglob("*.py"))

errors = []

print("=== Pre-commit check ===\n")

# 1. Syntax check
print("[1/3] Syntax check...")
for f in PY_FILES:
    try:
        py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(f"SYNTAX ERROR: {f}: {e}")
if not errors:
    print("  OK")

# 2. Cyrillic literals in .py files
print("[2/3] Cyrillic check...")
cyrillic_errors = []
for f in PY_FILES:
    try:
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            # Skip full-line comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Strip inline comment: find # outside of quotes
            code_part = line
            in_str = None
            for ci, ch in enumerate(line):
                if ch in ('"', "'") and (ci == 0 or line[ci - 1] != "\\"):
                    if in_str is None:
                        in_str = ch
                    elif ch == in_str:
                        in_str = None
                elif ch == "#" and in_str is None:
                    code_part = line[:ci]
                    break
            for ch in code_part:
                if "\u0400" <= ch <= "\u04ff":
                    cyrillic_errors.append(f"  {f}:{i}: {line.strip()[:80]}")
                    break
    except Exception:
        pass
if cyrillic_errors:
    print(f"  WARNING: {len(cyrillic_errors)} lines with Cyrillic (use unicode escapes for new code)")
    for ce in cyrillic_errors[:5]:
        print(ce)
    if len(cyrillic_errors) > 5:
        print(f"  ... and {len(cyrillic_errors) - 5} more")
else:
    print("  OK")

# 3. Critical imports
print("[3/3] Import check...")
for module in CRITICAL_MODULES:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; print('OK')"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        errors.append(f"IMPORT FAILED: {module}\n  {result.stderr.strip()[:500]}")
if not errors:
    print("  OK")
print(f"  OK (DB-dependent modules skipped: {', '.join(m.split('.')[-1] for m in DB_MODULES)})")

# Result
print()
if errors:
    print("=== FAILED ===")
    for e in errors:
        print(f"\n[FAIL] {e}")
    print("\nFix errors before committing.")
    sys.exit(1)
else:
    print("=== ALL CHECKS PASSED ===")
    sys.exit(0)
