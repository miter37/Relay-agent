from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Config


class EmbeddingBackend(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float] | None:
        """Return embedding vector for text or None if unavailable."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """Return True if backend is configured and ready."""
        ...


class NullEmbedding(EmbeddingBackend):
    def embed(self, text: str) -> list[float] | None:
        return None

    def available(self) -> bool:
        return False


def get_embedding_backend(config: Config) -> EmbeddingBackend:
    # Future pluggable embedding backends can be registered here.
    # Default is NullEmbedding which falls back to FTS5 lexical search.
    return NullEmbedding()
