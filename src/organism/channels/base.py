from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    text: str
    user_id: str        # telegram user id, "local" for CLI, session id for web
    channel: str        # "telegram", "cli", "web"
    metadata: dict = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    text: str
    user_id: str
    channel: str
    is_file: bool = False   # if True, text contains file path
    metadata: dict = field(default_factory=dict)


class BaseChannel(ABC):

    @abstractmethod
    async def start(self) -> None:
        """Start the channel (blocking)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel."""

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Send an outgoing message through this channel."""
