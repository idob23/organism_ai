"""BENCH-1: Deterministic expected-value checks for benchmark tasks.

Provides numeric and string-containment checks that bypass the LLM evaluator
for tasks with known correct answers.
"""
import re


def _extract_numbers(text: str) -> list[float]:
    """Extract all numbers from text, normalizing separators."""
    # Match numbers with optional spaces/commas/dots inside
    raw = re.findall(r'-?\d[\d\s,\.]*\d|\d', text)
    results: list[float] = []
    for token in raw:
        # Remove spaces
        clean = token.replace(' ', '').replace('\u00a0', '')
        # Decide comma role: if multiple commas -> thousand separator, remove
        comma_count = clean.count(',')
        dot_count = clean.count('.')
        if comma_count == 1 and dot_count == 0:
            # Single comma, no dots -> decimal separator
            clean = clean.replace(',', '.')
        elif comma_count >= 1:
            # Multiple commas or commas with dots -> thousand separators
            clean = clean.replace(',', '')
        try:
            results.append(float(clean))
        except ValueError:
            continue
    return results


def check_numeric(
    output: str,
    values: list[float],
    tolerance: float,
) -> tuple[float, str]:
    """Check that all expected numbers appear in output within tolerance.

    Returns (score, reason) where score = fraction of values found.
    """
    if not values:
        return 1.0, "no values to check"

    found_numbers = _extract_numbers(output)
    matched: list[float] = []
    missing: list[float] = []

    for expected in values:
        ok = False
        for num in found_numbers:
            if abs(num - expected) / max(abs(expected), 1e-9) <= tolerance:
                ok = True
                break
        if ok:
            matched.append(expected)
        else:
            missing.append(expected)

    score = len(matched) / len(values)
    if missing:
        reason = (
            f"found {len(matched)}/{len(values)}: "
            f"matched={matched}, missing={missing}"
        )
    else:
        reason = f"all {len(values)} values found"
    return round(score, 4), reason


def check_contains_all(
    output: str,
    values: list[str],
) -> tuple[float, str]:
    """Check that all expected strings appear in output (case-insensitive).

    Returns (score, reason) where score = fraction of strings found.
    """
    if not values:
        return 1.0, "no values to check"

    lower = output.lower()
    matched: list[str] = []
    missing: list[str] = []

    for v in values:
        if v.lower() in lower:
            matched.append(v)
        else:
            missing.append(v)

    score = len(matched) / len(values)
    if missing:
        reason = (
            f"found {len(matched)}/{len(values)}: "
            f"matched={matched}, missing={missing}"
        )
    else:
        reason = f"all {len(values)} strings found"
    return round(score, 4), reason


def run_expected_check(
    output: str,
    expected: dict,
) -> tuple[float, str]:
    """Dispatch to the appropriate check function based on check_type."""
    check_type = expected.get("check_type", "")
    if check_type == "numeric":
        return check_numeric(
            output,
            expected.get("values", []),
            expected.get("tolerance", 0.01),
        )
    elif check_type == "contains_all":
        return check_contains_all(
            output,
            expected.get("values", []),
        )
    else:
        return 0.0, f"unknown check_type: {check_type}"
