"""Preset factory methods for MemoryConfig.

These provide ready-made configurations at different complexity levels:
- minimal: Zero-config, no API key needed (hash embedder, in-memory vectors)
- smart: Auto-detected provider + echo + categories + graph
- full: Everything including scenes, profiles, tasks
"""

import os
import tempfile


def minimal_config():
    """Zero-config: hash embedder, in-memory vector store, basic decay. No API key."""
    from dhee.configs.base import (
        CategoryMemConfig,
        EchoMemConfig,
        EmbedderConfig,
        FadeMemConfig,
        KnowledgeGraphConfig,
        LLMConfig,
        MemoryConfig,
        SceneConfig,
        ProfileConfig,
        SkillConfig,
        VectorStoreConfig,
    )

    data_dir = os.environ.get("DHEE_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".dhee")
    os.makedirs(data_dir, exist_ok=True)

    return MemoryConfig(
        embedder=EmbedderConfig(
            provider="simple",
            config={"embedding_dims": 384},
        ),
        llm=LLMConfig(provider="mock", config={}),
        vector_store=VectorStoreConfig(
            provider="memory",
            config={
                "collection_name": "dhee_memories",
                "embedding_model_dims": 384,
            },
        ),
        history_db_path=os.path.join(data_dir, "history.db"),
        collection_name="dhee_memories",
        embedding_model_dims=384,
        fade=FadeMemConfig(enable_forgetting=True),
        echo=EchoMemConfig(enable_echo=False),
        category=CategoryMemConfig(enable_categories=False),
        graph=KnowledgeGraphConfig(enable_graph=False),
        scene=SceneConfig(enable_scenes=False),
        profile=ProfileConfig(enable_profiles=False),
        skill=SkillConfig(enable_skills=False, enable_mining=False),
    )


def smart_config():
    """Auto-detect best available provider + echo + categories. Needs API key or Ollama."""
    from dhee.configs.base import (
        CategoryMemConfig,
        EchoMemConfig,
        EmbedderConfig,
        FadeMemConfig,
        KnowledgeGraphConfig,
        LLMConfig,
        MemoryConfig,
        SceneConfig,
        ProfileConfig,
        SkillConfig,
        VectorStoreConfig,
    )
    from dhee.utils.factory import _detect_provider

    embedder_provider, llm_provider = _detect_provider()
    data_dir = os.environ.get("DHEE_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".dhee")
    os.makedirs(data_dir, exist_ok=True)

    if embedder_provider == "simple":
        dims = 384
        embedder_config = {"embedding_dims": dims}
    elif embedder_provider == "gemini":
        dims = 3072
        embedder_config = {"model": "gemini-embedding-001"}
    elif embedder_provider == "openai":
        dims = 1536
        embedder_config = {"model": "text-embedding-3-small"}
    elif embedder_provider == "ollama":
        dims = 768
        embedder_config = {}
    else:
        dims = 384
        embedder_config = {"embedding_dims": 384}

    # Use zvec for persistent storage when a real provider is available
    use_zvec = embedder_provider != "simple"
    if use_zvec:
        vs = VectorStoreConfig(
            provider="zvec",
            config={
                "path": os.path.join(data_dir, "zvec"),
                "collection_name": "dhee_memories",
                "embedding_model_dims": dims,
            },
        )
    else:
        vs = VectorStoreConfig(
            provider="memory",
            config={
                "collection_name": "dhee_memories",
                "embedding_model_dims": dims,
            },
        )

    # Echo/category need LLM — disable if using mock
    has_llm = llm_provider != "mock"

    return MemoryConfig(
        embedder=EmbedderConfig(provider=embedder_provider, config=embedder_config),
        llm=LLMConfig(provider=llm_provider, config={}),
        vector_store=vs,
        history_db_path=os.path.join(data_dir, "history.db"),
        collection_name="dhee_memories",
        embedding_model_dims=dims,
        fade=FadeMemConfig(enable_forgetting=True),
        echo=EchoMemConfig(enable_echo=has_llm),
        category=CategoryMemConfig(enable_categories=has_llm),
        graph=KnowledgeGraphConfig(enable_graph=True, use_llm_extraction=False),
        scene=SceneConfig(enable_scenes=False),
        profile=ProfileConfig(enable_profiles=False),
        skill=SkillConfig(enable_skills=True, enable_mining=False),
    )


def full_config():
    """Everything: scenes, profiles, graph, tasks. Needs API key or Ollama."""
    from dhee.configs.base import (
        EnrichmentConfig,
        SceneConfig,
        ProfileConfig,
        SkillConfig,
    )

    config = smart_config()
    config.scene = SceneConfig(enable_scenes=True)
    config.profile = ProfileConfig(enable_profiles=True)
    config.echo.enable_echo = True
    config.category.enable_categories = True
    config.graph.enable_graph = True
    config.skill = SkillConfig(enable_skills=True, enable_mining=True)
    config.enrichment = EnrichmentConfig(enable_unified=True)
    return config
