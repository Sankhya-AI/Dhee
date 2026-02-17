"""SkillMiner — compiles successful trajectories into reusable skills.

Pipeline:
1. Find successful trajectories matching a query
2. Cluster similar trajectories by task description
3. For each cluster (min 2): LLM extracts common pattern as skill
4. Compute skill_signature_hash → dedup against existing skills
5. Save as SKILL.md with source="mined", confidence=0.5
6. Apply optional mutation to prevent rigidity
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram.skills.hashing import skill_signature_hash
from engram.skills.schema import Skill, Trajectory
from engram.skills.store import SkillStore
from engram.skills.trajectory import TrajectoryStore

logger = logging.getLogger(__name__)

SKILL_MINING_PROMPT = """You are analyzing successful agent trajectories to extract a reusable skill.

Given these trajectories that solved similar tasks, extract the common pattern as a skill.

Trajectories:
{trajectories}

Extract a JSON object with these fields:
- name: Short descriptive name for the skill (e.g., "Fix Python Import Error")
- description: One-line description of when to use this skill
- preconditions: List of conditions that should be true before applying (e.g., ["Python project exists", "Error message visible"])
- steps: List of ordered steps to follow (e.g., ["Search for the import statement", "Check if module is installed", "Fix the import path"])
- tags: List of relevant tags (e.g., ["python", "debugging", "imports"])

Respond with ONLY the JSON object, no markdown fences or explanation."""


class SkillMiner:
    """Mines skills from successful agent trajectories."""

    def __init__(
        self,
        trajectory_store: TrajectoryStore,
        skill_store: SkillStore,
        llm: Any = None,
        embedder: Any = None,
        mutation_rate: float = 0.05,
        min_cluster_size: int = 2,
    ):
        self._trajectory_store = trajectory_store
        self._skill_store = skill_store
        self._llm = llm
        self._embedder = embedder
        self._mutation_rate = mutation_rate
        self._min_cluster_size = min_cluster_size

    def mine(
        self,
        task_query: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Skill]:
        """Run a full mining cycle.

        Returns list of newly mined skills.
        """
        # Step 1: Find successful trajectories
        trajectories = self._trajectory_store.find_successful(
            task_query=task_query,
            user_id=user_id,
            limit=limit,
        )

        if len(trajectories) < self._min_cluster_size:
            logger.info(
                "Not enough trajectories for mining (%d < %d)",
                len(trajectories),
                self._min_cluster_size,
            )
            return []

        # Step 2: Cluster by task description similarity
        clusters = self._cluster_trajectories(trajectories)

        # Step 3: Mine skills from each cluster
        mined_skills = []
        for cluster in clusters:
            if len(cluster) < self._min_cluster_size:
                continue

            skill = self._mine_from_cluster(cluster)
            if skill is None:
                continue

            # Step 4: Dedup check
            existing = self._skill_store.get_by_signature(skill.signature_hash)
            if existing:
                logger.info(
                    "Skill '%s' already exists as '%s', skipping",
                    skill.name,
                    existing.name,
                )
                continue

            # Step 5: Save
            self._skill_store.save(skill)
            mined_skills.append(skill)

            # Mark trajectories as mined
            for t in cluster:
                t.mined_skill_ids.append(skill.id)

        return mined_skills

    def _cluster_trajectories(
        self, trajectories: List[Trajectory]
    ) -> List[List[Trajectory]]:
        """Cluster trajectories by task description similarity.

        Uses simple keyword overlap for clustering. Falls back to embedding
        similarity if an embedder is available.
        """
        if not trajectories:
            return []

        if self._embedder:
            return self._cluster_by_embedding(trajectories)

        return self._cluster_by_keywords(trajectories)

    def _cluster_by_keywords(
        self, trajectories: List[Trajectory]
    ) -> List[List[Trajectory]]:
        """Simple keyword-based clustering."""
        clusters: Dict[str, List[Trajectory]] = {}

        for t in trajectories:
            # Normalize task description to a cluster key
            words = set(t.task_description.lower().split())
            # Use sorted significant words as cluster key
            significant = sorted(w for w in words if len(w) > 3)[:5]
            key = " ".join(significant) if significant else "general"

            if key not in clusters:
                clusters[key] = []
            clusters[key].append(t)

        return list(clusters.values())

    def _cluster_by_embedding(
        self, trajectories: List[Trajectory]
    ) -> List[List[Trajectory]]:
        """Embedding-based clustering using cosine similarity."""
        from engram.utils.math import cosine_similarity

        embeddings = []
        for t in trajectories:
            try:
                emb = self._embedder.embed(t.task_description, memory_action="search")
                embeddings.append(emb)
            except Exception:
                embeddings.append(None)

        # Simple greedy clustering: assign each trajectory to nearest cluster
        clusters: List[List[int]] = []
        cluster_centers: List[List[float]] = []
        threshold = 0.7

        for i, emb in enumerate(embeddings):
            if emb is None:
                continue

            best_cluster = -1
            best_sim = 0.0

            for ci, center in enumerate(cluster_centers):
                sim = cosine_similarity(emb, center)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = ci

            if best_sim >= threshold and best_cluster >= 0:
                clusters[best_cluster].append(i)
            else:
                clusters.append([i])
                cluster_centers.append(emb)

        return [
            [trajectories[i] for i in cluster_indices]
            for cluster_indices in clusters
        ]

    def _mine_from_cluster(self, cluster: List[Trajectory]) -> Optional[Skill]:
        """Extract a skill from a cluster of similar trajectories."""
        if self._llm:
            return self._mine_with_llm(cluster)
        return self._mine_heuristic(cluster)

    def _mine_with_llm(self, cluster: List[Trajectory]) -> Optional[Skill]:
        """Use LLM to extract a skill from trajectory cluster."""
        # Format trajectories for the prompt
        formatted = []
        for i, t in enumerate(cluster[:5], 1):  # Limit to 5 for context window
            steps_text = "\n".join(
                f"  - {s.action} ({s.tool}): {s.result_summary}"
                for s in t.steps
            )
            formatted.append(
                f"Trajectory {i}: {t.task_description}\n"
                f"  Outcome: {t.outcome_summary}\n"
                f"  Steps:\n{steps_text}"
            )

        prompt = SKILL_MINING_PROMPT.format(
            trajectories="\n\n".join(formatted)
        )

        try:
            response = self._llm.generate(prompt)
            # Parse JSON response
            response_text = response.strip()
            if response_text.startswith("```"):
                response_text = response_text.strip("`").strip()
                if response_text.startswith("json"):
                    response_text = response_text[4:].strip()

            data = json.loads(response_text)
        except Exception as e:
            logger.warning("LLM skill extraction failed: %s", e)
            return self._mine_heuristic(cluster)

        skill = Skill(
            name=data.get("name", "Mined Skill"),
            description=data.get("description", ""),
            preconditions=data.get("preconditions", []),
            steps=data.get("steps", []),
            tags=data.get("tags", []),
            confidence=0.5,
            source="mined",
            source_trajectory_ids=[t.id for t in cluster],
        )

        # Apply mutation
        skill = self._maybe_mutate(skill)

        return skill

    def _mine_heuristic(self, cluster: List[Trajectory]) -> Optional[Skill]:
        """Extract a skill from trajectories without LLM (heuristic)."""
        if not cluster:
            return None

        # Use the first trajectory as the template
        template = cluster[0]

        # Common steps across trajectories
        step_texts = []
        for step in template.steps:
            text = f"{step.action}"
            if step.tool:
                text += f" using {step.tool}"
            step_texts.append(text)

        # Extract task words for tags
        words = template.task_description.lower().split()
        tags = [w for w in words if len(w) > 3][:5]

        skill = Skill(
            name=f"Auto: {template.task_description[:50]}",
            description=f"Mined from {len(cluster)} successful trajectories",
            steps=step_texts,
            tags=tags,
            confidence=0.5,
            source="mined",
            source_trajectory_ids=[t.id for t in cluster],
        )

        skill = self._maybe_mutate(skill)
        return skill

    def _maybe_mutate(self, skill: Skill) -> Skill:
        """Apply optional mutation to prevent skill rigidity."""
        if random.random() > self._mutation_rate:
            return skill

        mutations = [
            self._mutate_add_verification,
            self._mutate_generalize_step,
        ]

        mutation = random.choice(mutations)
        return mutation(skill)

    def _mutate_add_verification(self, skill: Skill) -> Skill:
        """Add a verification step at the end."""
        if skill.steps and not any("verify" in s.lower() for s in skill.steps):
            skill.steps.append("Verify the result is correct")
        return skill

    def _mutate_generalize_step(self, skill: Skill) -> Skill:
        """Generalize a specific step to be more reusable."""
        if not skill.steps:
            return skill

        # Pick a random step and add a generalization note
        idx = random.randint(0, len(skill.steps) - 1)
        skill.steps[idx] = skill.steps[idx] + " (adapt as needed)"
        return skill
