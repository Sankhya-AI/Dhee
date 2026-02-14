from typing import Any, Dict

class EmbedderFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from engram.embeddings.gemini import GeminiEmbedder

            return GeminiEmbedder(config)
        if provider == "simple":
            from engram.embeddings.simple import SimpleEmbedder

            return SimpleEmbedder(config)
        if provider == "openai":
            from engram.embeddings.openai import OpenAIEmbedder

            return OpenAIEmbedder(config)
        if provider == "ollama":
            from engram.embeddings.ollama import OllamaEmbedder

            return OllamaEmbedder(config)
        if provider == "nvidia":
            from engram.embeddings.nvidia import NvidiaEmbedder

            return NvidiaEmbedder(config)
        raise ValueError(f"Unsupported embedder provider: {provider}")


class LLMFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "gemini":
            from engram.llms.gemini import GeminiLLM

            return GeminiLLM(config)
        if provider == "mock":
            from engram.llms.mock import MockLLM

            return MockLLM(config)
        if provider == "openai":
            from engram.llms.openai import OpenAILLM

            return OpenAILLM(config)
        if provider == "ollama":
            from engram.llms.ollama import OllamaLLM

            return OllamaLLM(config)
        if provider == "nvidia":
            from engram.llms.nvidia import NvidiaLLM

            return NvidiaLLM(config)
        raise ValueError(f"Unsupported LLM provider: {provider}")


class VectorStoreFactory:
    @classmethod
    def create(cls, provider: str, config: Dict[str, Any]):
        if provider == "memory":
            from engram.vector_stores.memory import InMemoryVectorStore

            return InMemoryVectorStore(config)
        if provider == "sqlite_vec":
            from engram.vector_stores.sqlite_vec import SqliteVecStore

            return SqliteVecStore(config)
        raise ValueError(f"Unsupported vector store provider: {provider}")
