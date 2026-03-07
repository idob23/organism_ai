"""Chat history storage \u2014 save and retrieve recent messages per user."""
from sqlalchemy import select, text, func
from .database import ChatMessage, AsyncSessionLocal
from src.organism.logging.error_handler import get_logger

_log = get_logger("memory.chat_history")

MAX_CONTEXT_MESSAGES = 10  # last N messages injected into context
MAX_STORED_MESSAGES = 50000  # max stored messages per user


class ChatHistory:

    async def save_message(self, user_id: str, role: str, content: str) -> None:
        """Save a message to chat history."""
        try:
            async with AsyncSessionLocal() as session:
                session.add(ChatMessage(
                    user_id=user_id,
                    role=role,
                    content=content[:5000],
                ))
                await session.commit()
        except Exception as e:
            _log.warning(f"Failed to save chat message: {e}")

    async def get_recent(self, user_id: str, limit: int = MAX_CONTEXT_MESSAGES) -> list[dict]:
        """Get recent messages for user, ordered chronologically."""
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(ChatMessage)
                    .where(ChatMessage.user_id == user_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                return [
                    {"role": r.role, "content": r.content}
                    for r in reversed(rows)  # chronological order
                ]
        except Exception:
            return []

    async def cleanup_old(self, user_id: str) -> None:
        """Keep only last MAX_STORED_MESSAGES per user."""
        try:
            async with AsyncSessionLocal() as session:
                count = await session.scalar(
                    select(func.count()).where(ChatMessage.user_id == user_id)
                )
                if count and count > MAX_STORED_MESSAGES:
                    await session.execute(text(
                        "DELETE FROM chat_messages WHERE id IN ("
                        "  SELECT id FROM chat_messages WHERE user_id = :uid "
                        "  ORDER BY created_at ASC LIMIT :n"
                        ")"
                    ), {"uid": user_id, "n": count - MAX_STORED_MESSAGES})
                    await session.commit()
        except Exception:
            pass
