"""Q-6.3: Human-in-the-loop approval for critical actions.

Sends approval request to user (Telegram), waits for /approve or /reject.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from src.organism.logging.error_handler import get_logger

_log = get_logger("core.human_approval")


@dataclass
class PendingApproval:
    request_id: str
    description: str
    task_id: str
    created_at: datetime
    status: str = "pending"  # "pending" | "approved" | "rejected" | "expired"
    response_at: datetime | None = None
    timeout_seconds: int = 300


class HumanApproval:
    """Request human confirmation before critical actions."""

    def __init__(
        self,
        send_fn: Callable[[str], Awaitable[None]],
        timeout: int = 300,
    ) -> None:
        self.send_fn = send_fn
        self.timeout = timeout
        self._pending: dict[str, PendingApproval] = {}
        self._events: dict[str, asyncio.Event] = {}

    async def request_approval(self, description: str, task_id: str = "") -> bool:
        """Send approval request and wait for user response.

        Returns True if approved, False if rejected or timed out.
        """
        request_id = uuid.uuid4().hex
        short_id = request_id[:8]
        approval = PendingApproval(
            request_id=request_id,
            description=description,
            task_id=task_id,
            created_at=datetime.utcnow(),
            timeout_seconds=self.timeout,
        )
        event = asyncio.Event()
        self._pending[request_id] = approval
        self._events[request_id] = event

        # "\u2753 \u0417\u0430\u043f\u0440\u043e\u0441 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f:\n\n{desc}\n\n
        #  \u041e\u0442\u0432\u0435\u0442\u044c\u0442\u0435: /approve {id} \u0438\u043b\u0438 /reject {id}"
        msg = (
            "\u2753 \u0417\u0430\u043f\u0440\u043e\u0441 "
            "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f:\n\n"
            f"{description}\n\n"
            "\u041e\u0442\u0432\u0435\u0442\u044c\u0442\u0435: "
            f"/approve {short_id} "
            "\u0438\u043b\u0438 "
            f"/reject {short_id}"
        )
        try:
            await self.send_fn(msg)
        except Exception as exc:
            _log.error("approval.send_failed: %s: %s", type(exc).__name__, exc)
            self._cleanup(request_id)
            return False

        _log.info("approval.requested: %s — %s", short_id, description[:80])

        try:
            await asyncio.wait_for(event.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            approval.status = "expired"
            _log.warning("approval.expired: %s", short_id)
            return False
        finally:
            self._cleanup(request_id)

        return approval.status == "approved"

    def resolve(self, short_id: str, approved: bool) -> str:
        """Resolve a pending approval by short_id prefix.

        Returns user-facing status message.
        """
        target_id: str | None = None
        for rid in self._pending:
            if rid.startswith(short_id):
                target_id = rid
                break

        if target_id is None:
            # "\u0417\u0430\u043f\u0440\u043e\u0441 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u0438\u043b\u0438 \u0438\u0441\u0442\u0451\u043a"
            return (
                "\u0417\u0430\u043f\u0440\u043e\u0441 "
                "\u043d\u0435 "
                "\u043d\u0430\u0439\u0434\u0435\u043d "
                "\u0438\u043b\u0438 "
                "\u0438\u0441\u0442\u0451\u043a"
            )

        approval = self._pending[target_id]
        approval.status = "approved" if approved else "rejected"
        approval.response_at = datetime.utcnow()

        event = self._events.get(target_id)
        if event:
            event.set()

        desc_short = approval.description[:80]
        if approved:
            _log.info("approval.approved: %s", short_id)
            # "\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e: {desc}"
            return (
                "\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e: "
                f"{desc_short}"
            )
        else:
            _log.info("approval.rejected: %s", short_id)
            # "\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u043e: {desc}"
            return (
                "\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u043e: "
                f"{desc_short}"
            )

    def _cleanup(self, request_id: str) -> None:
        self._pending.pop(request_id, None)
        self._events.pop(request_id, None)
