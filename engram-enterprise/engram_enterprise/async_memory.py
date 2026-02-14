"""Native async memory implementation for Engram.

Uses aiosqlite, async Qdrant client, and async LLM/embedder providers
for true async operations without thread pool wrappers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from engram.configs.base import MemoryConfig
from engram.core.decay import calculate_decayed_strength, should_forget, should_promote
from engram_enterprise.async_sqlite import AsyncSQLiteManager
from engram.vector_stores.async_qdrant import AsyncQdrantVectorStore


class AsyncMemory:
    """Native async memory implementation.

    Unlike the sync Memory class, this uses native async throughout:
    - aiosqlite for database operations
    - AsyncQdrantClient for vector operations
    - Async LLM/embedder for AI operations

    Example:
        async with AsyncMemory() as memory:
            await memory.add("User prefers Python", user_id="u1")
            results = await memory.search("programming", user_id="u1")
    """

    def __init__(self, config: Optional[MemoryConfig] = None):
        self.config = config or MemoryConfig()
        self._initialized = False

        # Components initialized lazily
        self._db: Optional[AsyncSQLiteManager] = None
        self._vector_store: Optional[AsyncQdrantVectorStore] = None
        self._llm = None
        self._embedder = None

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def initialize(self) -> None:
        """Initialize all async components."""
        if self._initialized:
            return

        # Initialize database
        self._db = AsyncSQLiteManager(self.config.history_db_path)
        await self._db.initialize()

        # Initialize vector store
        vector_config = self.config.vector_store.config.copy()
        vector_config["collection_name"] = self.config.collection_name
        vector_config["embedding_model_dims"] = self.config.embedding_model_dims
        self._vector_store = AsyncQdrantVectorStore(vector_config)
        await self._vector_store.initialize()

        # Initialize async LLM
        self._llm = self._create_async_llm()

        # Initialize async embedder
        self._embedder = self._create_async_embedder()

        self._initialized = True

    def _create_async_llm(self):
        """Create async LLM based on config."""
        provider = self.config.llm.provider
        llm_config = self.config.llm.config

        if provider == "gemini":
            from engram_enterprise.async_llm import AsyncGeminiLLM
            return AsyncGeminiLLM(llm_config)
        elif provider == "openai":
            from engram_enterprise.async_llm import AsyncOpenAILLM
            return AsyncOpenAILLM(llm_config)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _create_async_embedder(self):
        """Create async embedder based on config."""
        provider = self.config.embedder.provider
        embedder_config = self.config.embedder.config

        if provider == "gemini":
            from engram_enterprise.async_embedder import AsyncGeminiEmbedder
            return AsyncGeminiEmbedder(embedder_config)
        elif provider == "openai":
            from engram_enterprise.async_embedder import AsyncOpenAIEmbedder
            return AsyncOpenAIEmbedder(embedder_config)
        else:
            raise ValueError(f"Unknown embedder provider: {provider}")

    async def close(self) -> None:
        """Close all connections."""
        if self._vector_store:
            await self._vector_store.close()
        if self._db:
            await self._db.close()

    @classmethod
    async def from_config(cls, config_dict: Dict[str, Any]) -> "AsyncMemory":
        """Create AsyncMemory from config dict."""
        instance = cls(MemoryConfig(**config_dict))
        await instance.initialize()
        return instance

    async def add(
        self,
        messages: Union[str, List[Dict[str, str]]],
        user_id: str = "default",
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
        infer: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add a memory.

        Args:
            messages: Content to store (string or list of messages)
            user_id: User identifier
            agent_id: Agent identifier
            metadata: Additional metadata
            categories: Category tags
            infer: Extract facts from content (requires LLM)

        Returns:
            Dict with results including memory IDs
        """
        await self.initialize()

        # Normalize input
        if isinstance(messages, str):
            content = messages
        elif isinstance(messages, list):
            content = " ".join(
                msg.get("content", "") for msg in messages if isinstance(msg, dict)
            )
        else:
            content = str(messages)

        # Generate embedding
        embedding = await self._embedder.embed(content)

        # Generate ID
        memory_id = str(uuid.uuid4())

        # Determine initial layer
        layer = "sml"
        strength = 1.0

        # Store in database
        await self._db.add_memory(
            memory_id=memory_id,
            content=content,
            user_id=user_id,
            agent_id=agent_id,
            metadata=metadata,
            categories=categories,
            layer=layer,
            strength=strength,
            embedding=embedding,
        )

        # Store in vector store
        payload = {
            "memory_id": memory_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "layer": layer,
        }
        await self._vector_store.insert(
            vectors=[embedding],
            payloads=[payload],
            ids=[memory_id],
        )

        return {
            "results": [{"id": memory_id, "memory": content}],
            "count": 1,
        }

    async def search(
        self,
        query: str,
        user_id: str = "default",
        agent_id: Optional[str] = None,
        limit: int = 10,
        categories: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search memories.

        Args:
            query: Search query
            user_id: User identifier
            agent_id: Agent identifier
            limit: Maximum results
            categories: Filter by categories

        Returns:
            List of matching memories with scores
        """
        await self.initialize()

        # Generate query embedding
        query_embedding = await self._embedder.embed(query)

        # Build filters
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id

        # Search vector store
        vector_results = await self._vector_store.search(
            query_vector=query_embedding,
            limit=limit * 2,  # Get extra for filtering
            filters=filters,
        )

        # Enrich with database info
        results = []
        for vr in vector_results[:limit]:
            memory = await self._db.get_memory(vr.id)
            if memory:
                # Increment access count
                await self._db.increment_access(vr.id)

                results.append({
                    "id": vr.id,
                    "memory": memory.get("memory", ""),
                    "content": memory.get("memory", ""),
                    "score": vr.score,
                    "composite_score": vr.score * memory.get("strength", 1.0),
                    "layer": memory.get("layer", "sml"),
                    "strength": memory.get("strength", 1.0),
                    "categories": memory.get("categories", []),
                    "metadata": memory.get("metadata", {}),
                })

        # Sort by composite score
        results.sort(key=lambda x: x["composite_score"], reverse=True)
        return results

    async def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        await self.initialize()
        return await self._db.get_memory(memory_id)

    async def get_all(
        self,
        user_id: str = "default",
        agent_id: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
        **kwargs,
    ) -> Dict[str, Any]:
        """Get all memories matching filters."""
        await self.initialize()
        memories = await self._db.get_all_memories(
            user_id=user_id,
            agent_id=agent_id,
            layer=layer,
            limit=limit,
        )
        return {"results": memories, "count": len(memories)}

    async def update(self, memory_id: str, data: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Update a memory."""
        await self.initialize()

        if isinstance(data, str):
            return await self._db.update_memory(memory_id, content=data)
        else:
            return await self._db.update_memory(
                memory_id,
                content=data.get("content"),
                metadata=data.get("metadata"),
            )

    async def delete(self, memory_id: str) -> None:
        """Delete a memory."""
        await self.initialize()
        await self._db.delete_memory(memory_id)
        await self._vector_store.delete([memory_id])

    async def delete_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Delete all memories matching filters."""
        await self.initialize()

        # Get all matching memories
        memories = await self._db.get_all_memories(
            user_id=user_id,
            agent_id=agent_id,
            limit=10000,
        )

        # Delete each
        for mem in memories:
            await self._db.delete_memory(mem["id"])
            await self._vector_store.delete([mem["id"]])

        return {"deleted": len(memories)}

    async def history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get history for a memory."""
        await self.initialize()
        return await self._db.get_history(memory_id)

    async def apply_decay(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, int]:
        """Apply memory decay (forgetting).

        Uses Ebbinghaus curve to decay memory strength.
        Memories below threshold are forgotten.
        High-access memories may be promoted.
        """
        await self.initialize()

        memories = await self._db.get_all_memories(
            user_id=user_id,
            agent_id=agent_id,
            limit=10000,
        )

        decayed = 0
        forgotten = 0
        promoted = 0

        for mem in memories:
            current_strength = mem.get("strength", 1.0)
            layer = mem.get("layer", "sml")
            access_count = mem.get("access_count", 0)
            last_accessed = mem.get("last_accessed")

            # Calculate decay
            decay_rate = (
                self.config.engram.sml_decay_rate
                if layer == "sml"
                else self.config.engram.lml_decay_rate
            )

            new_strength = calculate_decayed_strength(
                current_strength,
                last_accessed,
                decay_rate,
                access_count,
            )

            # Check if should forget
            if should_forget(new_strength, self.config.engram.forget_threshold):
                await self._db.delete_memory(mem["id"])
                await self._vector_store.delete([mem["id"]])
                forgotten += 1
                continue

            # Check if should promote
            if layer == "sml" and should_promote(
                new_strength,
                access_count,
                self.config.engram.promotion_threshold,
                self.config.engram.min_access_for_promotion,
            ):
                await self._db.update_memory(
                    mem["id"],
                    strength=new_strength,
                    layer="lml",
                )
                await self._db.add_history(
                    mem["id"],
                    "PROMOTE",
                    old_layer="sml",
                    new_layer="lml",
                    old_strength=current_strength,
                    new_strength=new_strength,
                )
                promoted += 1
            elif new_strength != current_strength:
                await self._db.update_memory(mem["id"], strength=new_strength)
                await self._db.add_history(
                    mem["id"],
                    "DECAY",
                    old_strength=current_strength,
                    new_strength=new_strength,
                )
                decayed += 1

        return {
            "decayed": decayed,
            "forgotten": forgotten,
            "promoted": promoted,
        }

    async def get_stats(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get memory statistics."""
        await self.initialize()
        return await self._db.get_stats(user_id=user_id, agent_id=agent_id)

    async def fuse_memories(
        self,
        memory_ids: List[str],
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fuse multiple memories into one."""
        await self.initialize()

        # Get memories
        memories = []
        for mid in memory_ids:
            mem = await self._db.get_memory(mid)
            if mem:
                memories.append(mem)

        if len(memories) < 2:
            return {"error": "Need at least 2 memories to fuse"}

        # Combine content
        combined_content = " | ".join(m.get("memory", "") for m in memories)

        # Use LLM to create fusion
        prompt = f"Summarize these related facts into a single coherent statement:\n{combined_content}"
        fused_content = await self._llm.generate(prompt)

        # Calculate combined strength
        avg_strength = sum(m.get("strength", 1.0) for m in memories) / len(memories)

        # Add new memory
        result = await self.add(
            fused_content,
            user_id=user_id or memories[0].get("user_id", "default"),
            metadata={"fused_from": memory_ids},
        )

        # Update strength
        new_id = result["results"][0]["id"]
        await self._db.update_memory(new_id, strength=min(avg_strength * 1.2, 1.0), layer="lml")

        # Delete originals
        for mid in memory_ids:
            await self.delete(mid)

        return {
            "fused_id": new_id,
            "original_ids": memory_ids,
            "content": fused_content,
        }

    async def promote(self, memory_id: str) -> Dict[str, Any]:
        """Promote a memory to long-term."""
        await self.initialize()
        await self._db.update_memory(memory_id, layer="lml")
        return {"id": memory_id, "layer": "lml"}

    async def demote(self, memory_id: str) -> Dict[str, Any]:
        """Demote a memory to short-term."""
        await self.initialize()
        await self._db.update_memory(memory_id, layer="sml")
        return {"id": memory_id, "layer": "sml"}
