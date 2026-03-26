from abc import ABC, abstractmethod
from typing import List, Optional


class BaseEmbedder(ABC):
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    def embed(self, text: str, memory_action: Optional[str] = None):
        pass

    def embed_batch(
        self, texts: List[str], memory_action: Optional[str] = None
    ) -> List[List[float]]:
        """Embed multiple texts. Default: sequential fallback.

        Providers with native batch support (OpenAI, NVIDIA, Gemini)
        override this for single-API-call batching.
        """
        return [self.embed(text, memory_action=memory_action) for text in texts]
