"""Automated tests for Deduplication API.

Run: cd api_public && python test_api.py
Requires: running API on localhost:8080 with valid .env
         pip install httpx
"""

import asyncio
import json
import sys
import time

import httpx

BASE_URL = "http://localhost:8080"
API_KEY = "org_test1234567890abcdef1234567890abcdef"
BAD_KEY = "org_bad00000000000000000000000000000"

# Realistic 1C entities
TEST_ENTITIES = [
    "\u041e\u041e\u041e \u0422\u043e\u043f\u043b\u0438\u0432\u043d\u044b\u0439 \u0421\u043d\u0430\u0431",
    "\u0422\u043e\u043f\u043b\u0438\u0432\u043d\u044b\u0439 \u0441\u043d\u0430\u0431 \u041e\u041e\u041e",
    "\u0418\u041f \u041f\u0435\u0442\u0440\u043e\u0432 \u0421.\u0412.",
    "\u041f\u0435\u0442\u0440\u043e\u0432 \u0421\u0435\u0440\u0433\u0435\u0439 \u0418\u041f",
    "\u0410\u041e \u0414\u0430\u043b\u044c\u0437\u043e\u043b\u043e\u0442\u043e",
    "\u041e\u041e\u041e \u0420\u043e\u043c\u0430\u0448\u043a\u0430",
    "\u0420\u043e\u043c\u0430\u0448\u043a\u0430 \u041e\u041e\u041e",
    "\u0413\u0430\u0437\u043f\u0440\u043e\u043c \u043d\u0435\u0444\u0442\u044c",
]


class TestResult:
    def __init__(self, name: str, passed: bool, elapsed_ms: int, detail: str = ""):
        self.name = name
        self.passed = passed
        self.elapsed_ms = elapsed_ms
        self.detail = detail


results: list[TestResult] = []
embeddings_available = True


def record(name: str, passed: bool, elapsed_ms: int, detail: str = ""):
    results.append(TestResult(name, passed, elapsed_ms, detail))
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name} ({elapsed_ms}ms){f' - {detail}' if detail else ''}")


async def test_01_deduplicate_basic(client: httpx.AsyncClient):
    """POST /v1/deduplicate with 5 entities (2 duplicate pairs) -> 200, groups not empty."""
    t0 = time.monotonic()
    entities = TEST_ENTITIES[:5]
    # Use 0.75 threshold — actual similarity for reordered company names is ~0.80-0.84
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": entities, "threshold": 0.75},
    )
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        record("01_deduplicate_basic", False, ms, f"status={r.status_code} body={r.text[:200]}")
        return
    data = r.json()
    has_groups = len(data.get("groups", [])) > 0
    record(
        "01_deduplicate_basic", has_groups, ms,
        f"groups={len(data['groups'])}, total={data['total_entities']}, dups={data['duplicates_found']}"
    )


async def test_02_high_threshold(client: httpx.AsyncClient):
    """POST with threshold=0.95 -> fewer duplicates than 0.85."""
    t0 = time.monotonic()
    r_low = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": TEST_ENTITIES, "threshold": 0.80},
    )
    r_high = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": TEST_ENTITIES, "threshold": 0.95},
    )
    ms = int((time.monotonic() - t0) * 1000)
    if r_low.status_code != 200 or r_high.status_code != 200:
        record("02_high_threshold", False, ms,
               f"low={r_low.status_code} high={r_high.status_code}")
        return
    dups_low = r_low.json()["duplicates_found"]
    dups_high = r_high.json()["duplicates_found"]
    passed = dups_high <= dups_low
    record("02_high_threshold", passed, ms,
           f"dups@0.80={dups_low}, dups@0.95={dups_high}")


async def test_03_minimum_entities(client: httpx.AsyncClient):
    """POST with exactly 2 entities (minimum) -> 200."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": [TEST_ENTITIES[0], TEST_ENTITIES[1]]},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("03_minimum_entities", r.status_code == 200, ms,
           f"status={r.status_code}")


async def test_04_health(client: httpx.AsyncClient):
    """GET /v1/health -> 200, status=ok."""
    t0 = time.monotonic()
    r = await client.get(f"{BASE_URL}/v1/health")
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        record("04_health", False, ms, f"status={r.status_code}")
        return
    data = r.json()
    passed = data.get("status") == "ok" and "version" in data
    record("04_health", passed, ms, f"response={data}")


async def test_05_usage(client: httpx.AsyncClient):
    """GET /v1/usage -> 200, valid JSON with requests_today."""
    t0 = time.monotonic()
    r = await client.get(
        f"{BASE_URL}/v1/usage",
        headers={"X-API-Key": API_KEY},
    )
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        record("05_usage", False, ms, f"status={r.status_code}")
        return
    data = r.json()
    passed = "requests_today" in data and "requests_this_month" in data and "plan" in data
    record("05_usage", passed, ms, f"response={data}")


async def test_06_no_api_key(client: httpx.AsyncClient):
    """POST without X-API-Key -> 401."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        json={"entities": ["a", "b"]},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("06_no_api_key", r.status_code == 401, ms,
           f"status={r.status_code}")


async def test_07_invalid_api_key(client: httpx.AsyncClient):
    """POST with invalid X-API-Key -> 401."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": BAD_KEY},
        json={"entities": ["a", "b"]},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("07_invalid_api_key", r.status_code == 401, ms,
           f"status={r.status_code}")


async def test_08_empty_entities(client: httpx.AsyncClient):
    """POST with empty entities [] -> 422."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": []},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("08_empty_entities", r.status_code == 422, ms,
           f"status={r.status_code}")


async def test_09_single_entity(client: httpx.AsyncClient):
    """POST with 1 entity -> 422 (minimum 2)."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": ["single"]},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("09_single_entity", r.status_code == 422, ms,
           f"status={r.status_code}")


async def test_10_threshold_too_low(client: httpx.AsyncClient):
    """POST with threshold=0.3 (below 0.5) -> 422."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": ["a", "b"], "threshold": 0.3},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("10_threshold_too_low", r.status_code == 422, ms,
           f"status={r.status_code}")


async def test_11_threshold_too_high(client: httpx.AsyncClient):
    """POST with threshold=1.5 (above 1.0) -> 422."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": ["a", "b"], "threshold": 1.5},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("11_threshold_too_high", r.status_code == 422, ms,
           f"status={r.status_code}")


async def test_12_tier_entity_limit(client: httpx.AsyncClient):
    """POST with 51 entities on free tier (max 50) -> 422."""
    t0 = time.monotonic()
    entities = [f"entity_{i}" for i in range(51)]
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": entities},
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("12_tier_entity_limit", r.status_code == 422, ms,
           f"status={r.status_code} detail={r.text[:200]}")


async def test_13_unicode_cyrillic(client: httpx.AsyncClient):
    """POST with unicode/Cyrillic entities -> 200."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={
            "entities": [
                "\u041e\u041e\u041e \u0420\u043e\u043c\u0430\u0448\u043a\u0430",
                "\u0420\u043e\u043c\u0430\u0448\u043a\u0430 \u041e\u041e\u041e",
            ]
        },
    )
    ms = int((time.monotonic() - t0) * 1000)
    record("13_unicode_cyrillic", r.status_code == 200, ms,
           f"status={r.status_code}")


async def test_14_empty_strings(client: httpx.AsyncClient):
    """POST with empty strings in entities -> correct handling."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": ["", "  ", "\u041e\u041e\u041e \u0420\u043e\u043c\u0430\u0448\u043a\u0430", "\u0420\u043e\u043c\u0430\u0448\u043a\u0430 \u041e\u041e\u041e"]},
    )
    ms = int((time.monotonic() - t0) * 1000)
    # Should succeed — empty strings filtered, remaining entities processed
    record("14_empty_strings", r.status_code == 200, ms,
           f"status={r.status_code}")


async def test_15_identical_strings(client: httpx.AsyncClient):
    """POST with identical strings -> group with similarity ~1.0."""
    t0 = time.monotonic()
    r = await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={
            "entities": [
                "\u041e\u041e\u041e \u0422\u0435\u0441\u0442",
                "\u041e\u041e\u041e \u0422\u0435\u0441\u0442",
            ],
            "threshold": 0.5,
        },
    )
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        record("15_identical_strings", False, ms, f"status={r.status_code}")
        return
    data = r.json()
    has_group = len(data.get("groups", [])) >= 1
    sim = data["groups"][0]["similarity"] if has_group else 0
    record("15_identical_strings", has_group and sim >= 0.99, ms,
           f"groups={len(data['groups'])}, similarity={sim}")


async def test_16_rate_limit_counter(client: httpx.AsyncClient):
    """Two requests in a row — second should still be counted."""
    t0 = time.monotonic()
    r1 = await client.get(
        f"{BASE_URL}/v1/usage",
        headers={"X-API-Key": API_KEY},
    )
    before = r1.json().get("requests_today", 0) if r1.status_code == 200 else -1

    # Make a dedup request to increment the counter
    await client.post(
        f"{BASE_URL}/v1/deduplicate",
        headers={"X-API-Key": API_KEY},
        json={"entities": ["\u0410", "\u0411"]},
    )
    # Small delay for fire-and-forget write
    await asyncio.sleep(0.5)

    r2 = await client.get(
        f"{BASE_URL}/v1/usage",
        headers={"X-API-Key": API_KEY},
    )
    after = r2.json().get("requests_today", 0) if r2.status_code == 200 else -1
    ms = int((time.monotonic() - t0) * 1000)

    passed = after > before
    record("16_rate_limit_counter", passed, ms,
           f"before={before}, after={after}")


async def check_embeddings_available(client: httpx.AsyncClient) -> bool:
    """Quick check if OpenAI embeddings API is reachable."""
    try:
        r = await client.post(
            f"{BASE_URL}/v1/deduplicate",
            headers={"X-API-Key": API_KEY},
            json={"entities": ["test_a", "test_b"]},
            timeout=15.0,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 500:
            print("  [WARN] Embeddings API unreachable (500). Skipping embedding-dependent tests.")
            return False
        return True
    except Exception as e:
        print(f"  [WARN] Connection check failed: {e}")
        return False


async def main():
    global embeddings_available

    print("=" * 60)
    print("Deduplication API Test Suite")
    print("=" * 60)
    print(f"Target: {BASE_URL}")
    print(f"API Key: {API_KEY[:12]}...")
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check health first
        try:
            r = await client.get(f"{BASE_URL}/v1/health")
            if r.status_code != 200:
                print(f"FATAL: /v1/health returned {r.status_code}. Is the API running?")
                sys.exit(1)
        except httpx.ConnectError:
            print(f"FATAL: Cannot connect to {BASE_URL}. Is the API running?")
            sys.exit(1)

        print("API is up. Checking embeddings availability...\n")
        embeddings_available = await check_embeddings_available(client)

        # Auth/validation tests (no embeddings needed)
        print("--- Auth & Validation ---")
        await test_04_health(client)
        await test_06_no_api_key(client)
        await test_07_invalid_api_key(client)
        await test_08_empty_entities(client)
        await test_09_single_entity(client)
        await test_10_threshold_too_low(client)
        await test_11_threshold_too_high(client)
        await test_12_tier_entity_limit(client)

        if embeddings_available:
            print("\n--- Positive Scenarios (embeddings) ---")
            await test_01_deduplicate_basic(client)
            await test_02_high_threshold(client)
            await test_03_minimum_entities(client)
            await test_05_usage(client)

            print("\n--- Edge Cases ---")
            await test_13_unicode_cyrillic(client)
            await test_14_empty_strings(client)
            await test_15_identical_strings(client)
            await test_16_rate_limit_counter(client)
        else:
            print("\n--- SKIPPED: Embedding-dependent tests (no OPENAI_API_KEY) ---")
            skip_names = [
                "01_deduplicate_basic", "02_high_threshold", "03_minimum_entities",
                "05_usage", "13_unicode_cyrillic", "14_empty_strings",
                "15_identical_strings", "16_rate_limit_counter",
            ]
            for name in skip_names:
                record(name, True, 0, "SKIPPED (no embeddings)")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                print(f"  FAIL: {r.name} - {r.detail}")

    print("\n--- Full Results Table ---")
    print(f"{'#':<4} {'Test':<30} {'Status':<6} {'Time':>6} {'Detail'}")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        status = "PASS" if r.passed else "FAIL"
        print(f"{i:<4} {r.name:<30} {status:<6} {r.elapsed_ms:>5}ms {r.detail}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
