from abc import ABC, abstractmethod


class BaseChannel(ABC):

    @abstractmethod
    async def start(self) -> None:
        """Start the channel (blocking)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel."""
