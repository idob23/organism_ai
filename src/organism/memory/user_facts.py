"""Q-4.1: User Facts Extraction.
Q-5.1: Temporal tracking — save_facts() archives old values instead of overwriting.

Extracts personal facts from user task messages via Haiku and persists them
in the user_profile table.  Each fact change creates a new row; the previous
row gets valid_until stamped.  get_all_facts() returns only active rows
(valid_until IS NULL).
"""
import json
import re
import uuid
from datetime import datetime

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

    async def save_facts(self, facts: list[dict], user_id: str = "default") -> None:
        """Persist facts with temporal archiving, scoped by user_id.

        For each fact:
        - If no active row exists for the key → insert fresh row.
        - If active row exists with the same value → skip (no change).
        - If active row exists with a different value → stamp valid_until on the
          old row, link it to the new row via superseded_by, insert new active row.
        """
        if not facts:
            return
        async with AsyncSessionLocal() as session:
            for fact in facts:
                key = fact["fact_type"]
                value = fact["fact_value"]
                # Find the currently-active row for this key and user
                stmt = (
                    select(UserProfile)
                    .where(UserProfile.user_id == user_id)
                    .where(UserProfile.key == key)
                    .where(UserProfile.valid_until.is_(None))
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing and existing.value == value:
                    continue  # Nothing changed — skip

                new_id = str(uuid.uuid4())
                now = datetime.utcnow()

                if existing:
                    # Archive the old row
                    existing.valid_until = now
                    existing.superseded_by = new_id

                session.add(UserProfile(
                    id=new_id,
                    user_id=user_id,
                    key=key,
                    value=value,
                    valid_from=now,
                    valid_until=None,
                    superseded_by=None,
                ))
            await session.commit()

    async def get_all_facts(self, user_id: str = "default") -> dict:
        """Return only currently-active user facts as {fact_type: fact_value}."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(UserProfile)
                .where(UserProfile.user_id == user_id)
                .where(UserProfile.valid_until.is_(None))
            )
            result = await session.execute(stmt)
            return {row.key: row.value for row in result.scalars().all()}

    async def get_fact_history(self, key: str, user_id: str = "default") -> list[dict]:
        """Return all versions of a fact ordered by valid_from descending."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(UserProfile)
                .where(UserProfile.user_id == user_id)
                .where(UserProfile.key == key)
                .order_by(UserProfile.valid_from.desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            history = []
            for row in rows:
                history.append({
                    "fact_value": row.value,
                    "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                    "valid_until": row.valid_until.isoformat() if row.valid_until else None,
                    "is_current": row.valid_until is None,
                })
            return history
