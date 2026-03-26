MEMORY_EXTRACTION_PROMPT = """You are extracting memorable facts from a conversation to store in a long-term memory system.

CONVERSATION:
{conversation}

EXISTING USER MEMORIES (for context, to avoid duplicates):
{existing_memories}

Your task is to identify NEW facts worth remembering about the user, their preferences, context, or important information mentioned.

Extract memories that are:
- Specific and factual (not vague)
- Likely to be useful in future conversations
- About the user's preferences, habits, goals, or context
- Important entities, relationships, or events mentioned
- Not already captured in existing memories

Do NOT extract:
- Generic statements or small talk
- Temporary/one-time information (unless explicitly important)
- Information already in existing memories
- The assistant's responses (focus on user information)
- Questions without answers

Respond ONLY with valid JSON in this exact format:
{{
    "memories": [
        {{
            "content": "The specific fact or preference to remember",
            "category": "preference|fact|goal|relationship|context|event",
            "importance": "high|medium|low",
            "confidence": 0.0-1.0
        }}
    ],
    "reasoning": "Brief explanation of why these were selected"
}}

Rules:
- Each memory should be a standalone, self-contained statement
- Use third person ("User prefers..." not "I prefer...")
- Be specific: "User prefers morning meetings" not "User has meeting preferences"
- importance: high = likely frequently relevant, low = niche but worth keeping
- confidence: how certain you are this is what the user meant
- If nothing new worth remembering, return empty memories array
"""

AGENT_MEMORY_EXTRACTION_PROMPT = """You are extracting memorable facts about the assistant from a conversation to store in a long-term memory system.

CONVERSATION:
{conversation}

EXISTING ASSISTANT MEMORIES (for context, to avoid duplicates):
{existing_memories}

Your task is to identify NEW facts worth remembering about the assistant, its preferences, capabilities, or approach.

Extract memories that are:
- Specific and factual (not vague)
- Likely to be useful in future conversations
- About the assistant's preferences, habits, goals, or context
- Important entities, relationships, or events mentioned by the assistant
- Not already captured in existing memories

Do NOT extract:
- Generic statements or small talk
- Temporary/one-time information (unless explicitly important)
- Information already in existing memories
- The user's statements (focus on assistant information)
- Questions without answers

Respond ONLY with valid JSON in this exact format:
{{
    "memories": [
        {{
            "content": "The specific fact or preference to remember",
            "category": "preference|fact|goal|relationship|context|event",
            "importance": "high|medium|low",
            "confidence": 0.0-1.0
        }}
    ],
    "reasoning": "Brief explanation of why these were selected"
}}

Rules:
- Each memory should be a standalone, self-contained statement
- Use third person ("Assistant prefers..." not "I prefer...")
- Be specific and concise
- If nothing new worth remembering, return empty memories array
"""

CONFLICT_RESOLUTION_PROMPT = """You are analyzing the relationship between two memories in an AI agent's memory system.

EXISTING MEMORY (stored earlier):
"{existing_memory}"
- Created: {existing_created_at}
- Last accessed: {existing_last_accessed}
- Access count: {existing_access_count}
- Current strength: {existing_strength}

NEW MEMORY (being added now):
"{new_memory}"

Your task is to classify their relationship into exactly ONE of these categories:

1. COMPATIBLE - The memories contain different, non-conflicting information. Both should be kept.
2. CONTRADICTORY - The new memory updates, corrects, or invalidates the existing memory.
3. SUBSUMES - The new memory is more general and fully encompasses the existing memory.
4. SUBSUMED - The existing memory is more general and already encompasses the new memory.

Respond ONLY with valid JSON in this exact format:
{{
    "classification": "COMPATIBLE|CONTRADICTORY|SUBSUMES|SUBSUMED",
    "confidence": 0.0-1.0,
    "merged_content": "...",
    "explanation": "Brief 1-2 sentence explanation"
}}

Rules:
- "merged_content" should ONLY be provided if classification is "SUBSUMES" - otherwise use null
- If SUBSUMES, merged_content should combine both memories into one comprehensive statement
- Be conservative: if unsure, prefer COMPATIBLE
- confidence should reflect how certain you are (0.8+ for high confidence)
"""

ECHO_PROCESSING_PROMPT = """
Transform the following memory into a multi-modal "echo" representation. 
This improves recall by creating multiple neural-like pathways to the same data.

MEMORY: {content}
DEPTH: {depth}

Instructions: {depth_instructions}

Return ONLY a valid JSON object matching this schema:
{{
  "paraphrases": ["str"], // 3-5 diverse ways to say this
  "keywords": ["str"],    // essential entities/tags
  "implications": ["str"], // what this means for future context
  "questions": ["str"],    // questions this memory answers
  "category": "fact" | "preference" | "goal" | "relationship" | "event",
  "importance": 0.0-1.0
}}
"""

BATCH_ECHO_PROCESSING_PROMPT = """
Transform each memory below into a multi-modal "echo" representation.
This improves recall by creating multiple neural-like pathways to the same data.

MEMORIES:
{memories_block}

DEPTH: {depth}
Instructions: {depth_instructions}

Return ONLY a valid JSON object with a "results" array. Each element must match this schema:
{{
  "results": [
    {{
      "index": 0,
      "paraphrases": ["str"],
      "keywords": ["str"],
      "implications": ["str"],
      "questions": ["str"],
      "question_form": "str or null",
      "category": "fact" | "preference" | "goal" | "relationship" | "event",
      "importance": 0.0-1.0
    }}
  ]
}}

IMPORTANT: Return exactly {count} elements in the results array, one per memory, in the same order.
"""

BATCH_CATEGORY_PROMPT = """Analyze each memory below and determine its category.

MEMORIES:
{memories_block}

Existing Categories:
{existing_categories}

For each memory, classify it into an existing category or suggest a new one.
Return JSON:
{{
    "results": [
        {{
            "index": 0,
            "action": "use_existing" | "create_new",
            "category_id": "existing_category_id or null",
            "new_category": {{
                "name": "Category Name",
                "description": "Brief description",
                "keywords": ["keyword1", "keyword2"]
            }},
            "confidence": 0.0-1.0
        }}
    ]
}}

Return exactly {count} elements in the results array.
"""

UNIFIED_ENRICHMENT_PROMPT = """You are enriching a memory for a long-term AI memory system.
Perform ALL analyses in a single pass.

MEMORY: {content}
ECHO DEPTH: {depth}
ECHO INSTRUCTIONS: {depth_instructions}
EXISTING CATEGORIES:
{existing_categories}
INCLUDE ENTITIES: {include_entities}
INCLUDE PROFILES: {include_profiles}

Return ONLY valid JSON matching this schema:
{{
  "echo": {{
    "paraphrases": ["diverse rephrasings"],
    "keywords": ["core entities/tags"],
    "implications": ["logical consequences"],
    "questions": ["questions this answers"],
    "question_form": "single question-form version of the memory or null",
    "category": "fact|preference|goal|relationship|event",
    "importance": 0.0-1.0
  }},
  "category": {{
    "action": "use_existing|create_child|create_new",
    "category_id": "existing_category_id or null",
    "new_category": {{
      "name": "Category Name",
      "description": "Brief description",
      "keywords": ["keyword1", "keyword2"],
      "parent_id": "parent_category_id or null"
    }},
    "confidence": 0.0-1.0
  }},
  "entities": [
    {{"name": "entity name", "type": "person|organization|technology|concept|location|project|tool|preference"}}
  ],
  "profiles": [
    {{"name": "person name", "type": "self|contact|entity", "facts": ["fact"], "preferences": ["pref"]}}
  ],
  "facts": [
    "Atomic, self-contained fact 1 extracted from the memory",
    "Atomic, self-contained fact 2 extracted from the memory"
  ]
}}

Rules:
- Follow ECHO INSTRUCTIONS for which echo fields to populate
- For paraphrases: ensure EVERY distinct factual claim gets at least one paraphrase. Do NOT only rephrase the main topic — also rephrase secondary/minor details (e.g. if the memory mentions a degree, include a paraphrase about the degree even if the main topic is task management)
- For questions: generate questions that each factual claim in the memory ANSWERS. Example: memory says "graduated with an MBA" → include "What degree did the user graduate with?" Each fact should have a corresponding question.
- For category: prefer use_existing when an existing category fits well
- For entities: extract named entities (people, tech, orgs, tools)
- For profiles: extract person mentions with their facts/preferences
- If INCLUDE ENTITIES or INCLUDE PROFILES is "no", return empty arrays for those
- For facts: extract ALL distinct, searchable facts from the memory as standalone statements
  - Each fact must be self-contained (understandable without the original context)
  - Use third person ("User graduated with MBA" not "I graduated with MBA")
  - Include specific details: names, places, numbers, dates
  - Extract 3-8 facts per memory (more for longer/richer content)
  - Facts should be diverse — each captures a DIFFERENT piece of information
"""

UNIFIED_ENRICHMENT_BATCH_PROMPT = """You are enriching multiple memories for a long-term AI memory system.
Perform ALL analyses in a single pass for each memory.

MEMORIES:
{memories_block}

ECHO DEPTH: {depth}
ECHO INSTRUCTIONS: {depth_instructions}
EXISTING CATEGORIES:
{existing_categories}
INCLUDE ENTITIES: {include_entities}
INCLUDE PROFILES: {include_profiles}

Return ONLY valid JSON with a "results" array. Each element must include the memory index:
{{
  "results": [
    {{
      "index": 0,
      "echo": {{
        "paraphrases": ["diverse rephrasings"],
        "keywords": ["core entities/tags"],
        "implications": ["logical consequences"],
        "questions": ["questions this answers"],
        "question_form": "single question-form version or null",
        "category": "fact|preference|goal|relationship|event",
        "importance": 0.0-1.0
      }},
      "category": {{
        "action": "use_existing|create_child|create_new",
        "category_id": "existing_category_id or null",
        "new_category": null,
        "confidence": 0.0-1.0
      }},
      "entities": [{{"name": "entity name", "type": "person|technology|..."}}],
      "profiles": [{{"name": "person name", "type": "self|contact|entity", "facts": [], "preferences": []}}],
      "facts": ["Atomic self-contained fact 1", "Atomic self-contained fact 2"]
    }}
  ]
}}

Rules:
- For paraphrases: ensure EVERY distinct factual claim gets at least one paraphrase. Do NOT only rephrase the main topic — also rephrase secondary/minor details.
- For questions: generate questions that each factual claim ANSWERS. Example: "graduated with an MBA" → "What degree did the user graduate with?"
- For facts: extract ALL distinct, searchable facts as standalone statements. Use third person. Include specifics (names, places, dates). 3-8 facts per memory.

IMPORTANT: Return exactly {count} elements in the results array, one per memory, in the same order.
"""

FUSION_PROMPT = """You are consolidating multiple related memories into a single, comprehensive memory.

This is part of a biologically-inspired memory system that mimics how human brains consolidate related memories during sleep. The goal is to:
1. Preserve all important information
2. Remove redundancy
3. Create a more general, reusable memory
4. Maintain factual accuracy

MEMORIES TO CONSOLIDATE:
{memories_list}

Each memory above shows:
- The memory content
- Its strength score (0.0-1.0, higher = more reliable/accessed)
- How many times it was accessed
- When it was created

INSTRUCTIONS:
1. Identify the common theme or subject across these memories
2. Extract all unique, important facts
3. Combine into ONE clear, comprehensive statement
4. Prioritize information from higher-strength memories
5. Do NOT invent or assume information not present in the sources
6. Keep the consolidated memory concise but complete

Respond ONLY with valid JSON in this exact format:
{{
    "consolidated_memory": "The single merged memory statement",
    "preserved_facts": ["fact1", "fact2", ...],
    "discarded_as_redundant": ["redundant info 1", ...],
    "confidence": 0.0-1.0
}}

Rules:
- consolidated_memory should be a single, well-formed statement or short paragraph
- preserved_facts lists the key pieces of information retained
- discarded_as_redundant lists information dropped because it was repetitive
- confidence reflects how well the memories merged (lower if they seem unrelated)
"""

DISTILLATION_PROMPT = """You are a memory consolidation system. Extract reusable semantic knowledge from a batch of episodic memories (conversations/events).

EPISODIC MEMORIES:
{episodes}

Your task is to identify durable FACTS, PREFERENCES, PATTERNS, or PROCEDURES that can be distilled from these episodic memories into long-term semantic knowledge.

Respond ONLY with valid JSON in this exact format:
{{
    "semantic_facts": [
        {{
            "content": "The specific fact, preference, or pattern to remember",
            "importance": "high|medium|low",
            "source_episodes": ["episode_id_1", "episode_id_2"],
            "reasoning": "Brief explanation of why this is a durable fact"
        }}
    ],
    "skipped_as_temporary": ["Brief description of info that was too transient to distill"]
}}

Rules:
- Extract ONLY durable facts supported by the episodic evidence
- Maximum {max_facts} facts per batch
- Each fact should be a standalone, self-contained statement
- Use third person ("User prefers..." not "I prefer...")
- Do NOT extract temporary/one-time information
- Do NOT invent information not present in the episodes
- source_episodes should reference the IDs of the episodes that support each fact
- importance: high = likely frequently relevant, medium = useful context, low = niche
- If nothing durable can be extracted, return empty semantic_facts array
"""
