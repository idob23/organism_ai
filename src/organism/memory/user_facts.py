"""Q-4.1: User Facts Extraction.

Extracts personal facts from user task messages via Haiku and persists them
in the existing user_profile table (key=fact_type, value=fact_value).
Only the user's original task text is processed — never LLM output.
"""
import json
import re

from sqlalchemy import select

from .database import UserProfile, AsyncSessionLocal
from src.organism.llm.base import LLMProvider, Message

def format_for_prompt(facts: dict) -> str:
    """Format user facts as a compact context line for LLM system prompts.

    Example: "User context: name=Igor, role=Technical Director, company=Artel Zoloto"
    Returns "" when facts is empty.
    """
    if not facts:
        return ""
    parts = [f"{k}={v}" for k, v in facts.items()]
    return "User context: " + ", ".join(parts)


KNOWN_FACT_TYPES = {
    "name", "role", "company", "preference",
    "location", "equipment", "team_size",
}

_EXTRACT_SYSTEM = (
    "Extract any personal facts about the user from this message. "
    "Return a JSON array of objects with exactly two keys: "
    "fact_type (one of: name, role, company, preference, location, equipment, team_size) "
    "and fact_value (the extracted value as a short string). "
    "Return empty array [] if no facts are found. "
    "Return ONLY the JSON array, no explanation."
)


class UserFactsExtractor:

    async def extract_facts(self, message: str, llm: LLMProvider) -> list[dict]:
        """Call Haiku to extract personal facts from a user message.

        Returns list of {"fact_type": str, "fact_value": str} dicts.
        Returns [] on any error or when no facts are found.
        """
        try:
            resp = await llm.complete(
                messages=[Message(role="user", content=message[:1000])],
                system=_EXTRACT_SYSTEM,
                model_tier="fast",
                max_tokens=200,
            )
            raw = resp.content.strip()
            match = re.search(r"\[[\s\S]*\]", raw)
            if not match:
                return []
            data = json.loads(match.group(0))
            facts = []
            for item in data:
                ft = str(item.get("fact_type", "")).strip().lower()
                fv = str(item.get("fact_value", "")).strip()
                if ft in KNOWN_FACT_TYPES and fv:
                    facts.append({"fact_type": ft, "fact_value": fv})
            return facts
        except Exception:
            return []

    async def save_facts(self, facts: list[dict]) -> None:
        """Upsert facts into user_profile (key=fact_type, value=fact_value)."""
        if not facts:
            return
        async with AsyncSessionLocal() as session:
            for fact in facts:
                key = fact["fact_type"]
                value = fact["fact_value"]
                existing = await session.get(UserProfile, key)
                if existing:
                    existing.value = value
                else:
                    session.add(UserProfile(key=key, value=value))
            await session.commit()

    async def get_all_facts(self) -> dict:
        """Return all stored user facts as {fact_type: fact_value}."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(UserProfile))
            return {row.key: row.value for row in result.scalars().all()}
