"""BeliefNode — confidence-tracked facts with contradiction detection.

A BeliefNode is NOT a memory. Memories store content; beliefs track what
the agent currently holds to be TRUE, with quantified confidence.

Every fact stored in memory can have an associated belief:
  - Confidence: 0.0 (no idea) to 1.0 (certain)
  - Evidence: list of supporting/contradicting observations
  - Revision history: track how belief changed over time

Belief revision follows Bayesian-inspired updates:
  - New evidence supporting a belief → confidence increases
  - New evidence contradicting a belief → confidence decreases
  - Contradiction detected → both beliefs flagged, agent prompted to resolve

Beliefs are the foundation for:
  - Selective forgetting: low-confidence, low-utility beliefs decay first
  - Contradiction detection: new facts checked against existing beliefs
  - Confidence-aware retrieval: results annotated with belief strength
  - Reality grounding: beliefs validated against external evidence

Lifecycle: proposed -> held -> challenged -> revised | retracted
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BeliefStatus(str, Enum):
    PROPOSED = "proposed"       # New, low evidence
    HELD = "held"               # Actively believed (confidence > 0.5)
    CHALLENGED = "challenged"   # Contradicting evidence received
    REVISED = "revised"         # Updated based on new evidence
    RETRACTED = "retracted"     # No longer believed


@dataclass
class Evidence:
    """A piece of evidence for or against a belief."""
    id: str
    content: str
    supports: bool              # True = supports, False = contradicts
    source: str                 # "memory", "observation", "user", "inference"
    confidence: float           # how reliable is this evidence (0-1)
    timestamp: float
    memory_id: Optional[str] = None     # link to originating memory
    episode_id: Optional[str] = None    # link to originating episode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "supports": self.supports,
            "source": self.source,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "memory_id": self.memory_id,
            "episode_id": self.episode_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Evidence:
        return cls(
            id=d["id"],
            content=d["content"],
            supports=d["supports"],
            source=d.get("source", "memory"),
            confidence=d.get("confidence", 0.5),
            timestamp=d.get("timestamp", time.time()),
            memory_id=d.get("memory_id"),
            episode_id=d.get("episode_id"),
        )


@dataclass
class BeliefRevision:
    """Record of a belief change."""
    timestamp: float
    old_confidence: float
    new_confidence: float
    old_status: str
    new_status: str
    reason: str
    evidence_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "old_confidence": self.old_confidence,
            "new_confidence": self.new_confidence,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "reason": self.reason,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> BeliefRevision:
        return cls(
            timestamp=d["timestamp"],
            old_confidence=d["old_confidence"],
            new_confidence=d["new_confidence"],
            old_status=d["old_status"],
            new_status=d["new_status"],
            reason=d["reason"],
            evidence_id=d.get("evidence_id"),
        )


@dataclass
class BeliefNode:
    """A confidence-tracked belief about the world."""

    id: str
    user_id: str
    claim: str                  # "Python 3.12 supports pattern matching"
    domain: str                 # "programming", "user_preference", "system_state"
    status: BeliefStatus
    confidence: float           # current confidence (0-1)

    created_at: float
    updated_at: float

    evidence: List[Evidence] = field(default_factory=list)
    revisions: List[BeliefRevision] = field(default_factory=list)
    contradicts: List[str] = field(default_factory=list)    # belief IDs this contradicts

    # Source tracking
    source_memory_ids: List[str] = field(default_factory=list)
    source_episode_ids: List[str] = field(default_factory=list)

    tags: List[str] = field(default_factory=list)

    # Content fingerprint for contradiction detection
    _claim_keywords: List[str] = field(default_factory=list)

    def add_evidence(
        self,
        content: str,
        supports: bool,
        source: str = "memory",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Evidence:
        """Add evidence and update belief confidence via Bayesian update."""
        evidence = Evidence(
            id=str(uuid.uuid4()),
            content=content,
            supports=supports,
            source=source,
            confidence=confidence,
            timestamp=time.time(),
            memory_id=memory_id,
            episode_id=episode_id,
        )
        self.evidence.append(evidence)

        old_confidence = self.confidence
        old_status = self.status.value

        # Bayesian-inspired update
        self._update_confidence(supports, confidence)

        # Record revision if significant change
        delta = abs(self.confidence - old_confidence)
        if delta > 0.01:
            self.revisions.append(BeliefRevision(
                timestamp=time.time(),
                old_confidence=old_confidence,
                new_confidence=self.confidence,
                old_status=old_status,
                new_status=self.status.value,
                reason=f"{'Supporting' if supports else 'Contradicting'} evidence: {content[:100]}",
                evidence_id=evidence.id,
            ))

        self.updated_at = time.time()
        return evidence

    def _update_confidence(self, supports: bool, evidence_strength: float) -> None:
        """Bayesian-inspired confidence update.

        Uses a simplified model where:
        - Supporting evidence increases confidence proportionally to (1 - current)
        - Contradicting evidence decreases proportionally to current
        - Evidence strength modulates the update magnitude

        This ensures:
        - Already-confident beliefs need stronger evidence to change
        - Low-confidence beliefs are easily moved by new evidence
        - Updates are bounded and stable
        """
        lr = 0.15 * evidence_strength  # learning rate scaled by evidence quality

        if supports:
            # Move toward 1.0
            self.confidence += lr * (1.0 - self.confidence)
        else:
            # Move toward 0.0
            self.confidence -= lr * self.confidence

        self.confidence = max(0.0, min(1.0, self.confidence))

        # Update status
        if self.confidence >= 0.7:
            if self.status == BeliefStatus.CHALLENGED:
                self.status = BeliefStatus.REVISED
            elif self.status == BeliefStatus.PROPOSED:
                self.status = BeliefStatus.HELD
        elif self.confidence <= 0.3:
            if self.status in (BeliefStatus.HELD, BeliefStatus.REVISED):
                self.status = BeliefStatus.CHALLENGED
        if self.confidence <= 0.1:
            self.status = BeliefStatus.RETRACTED

    @property
    def supporting_evidence_count(self) -> int:
        return sum(1 for e in self.evidence if e.supports)

    @property
    def contradicting_evidence_count(self) -> int:
        return sum(1 for e in self.evidence if not e.supports)

    @property
    def evidence_ratio(self) -> float:
        """Ratio of supporting to total evidence (0-1)."""
        total = len(self.evidence)
        if total == 0:
            return 0.5
        return self.supporting_evidence_count / total

    @property
    def stability(self) -> float:
        """How stable is this belief? (0 = volatile, 1 = stable).

        Based on recent revision frequency and magnitude.
        """
        if len(self.revisions) < 2:
            return 1.0

        recent = self.revisions[-5:]
        deltas = [abs(r.new_confidence - r.old_confidence) for r in recent]
        avg_delta = sum(deltas) / len(deltas)
        # More frequent, larger changes = less stable
        return max(0.0, 1.0 - avg_delta * len(recent) / 5)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "claim": self.claim,
            "domain": self.domain,
            "status": self.status.value,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence": [e.to_dict() for e in self.evidence],
            "revisions": [r.to_dict() for r in self.revisions],
            "contradicts": self.contradicts,
            "source_memory_ids": self.source_memory_ids,
            "source_episode_ids": self.source_episode_ids,
            "tags": self.tags,
            "_claim_keywords": self._claim_keywords,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Compact format for HyperContext."""
        result = {
            "claim": self.claim[:200],
            "domain": self.domain,
            "confidence": round(self.confidence, 2),
            "status": self.status.value,
            "evidence_for": self.supporting_evidence_count,
            "evidence_against": self.contradicting_evidence_count,
            "stability": round(self.stability, 2),
        }
        if self.contradicts:
            result["has_contradictions"] = True
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> BeliefNode:
        return cls(
            id=d["id"],
            user_id=d["user_id"],
            claim=d["claim"],
            domain=d.get("domain", "general"),
            status=BeliefStatus(d.get("status", "proposed")),
            confidence=d.get("confidence", 0.5),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
            revisions=[BeliefRevision.from_dict(r) for r in d.get("revisions", [])],
            contradicts=d.get("contradicts", []),
            source_memory_ids=d.get("source_memory_ids", []),
            source_episode_ids=d.get("source_episode_ids", []),
            tags=d.get("tags", []),
            _claim_keywords=d.get("_claim_keywords", []),
        )


class BeliefStore:
    """Manages beliefs, contradiction detection, and confidence-aware retrieval.

    Contradiction detection works by:
      1. Each belief has keyword fingerprint from its claim
      2. New beliefs are compared against existing beliefs in same domain
      3. If high keyword overlap but opposite confidence direction → contradiction
      4. Contradicting beliefs are linked and both flagged for review

    No LLM needed for basic contradiction detection. LLM enhances
    semantic similarity when available.
    """

    CONTRADICTION_THRESHOLD = 0.4   # Jaccard overlap to check for contradictions
    RETRACTION_THRESHOLD = 0.1      # Below this confidence → retract

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "beliefs"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._beliefs: Dict[str, BeliefNode] = {}
        self._load()

    def add_belief(
        self,
        user_id: str,
        claim: str,
        domain: str = "general",
        confidence: float = 0.5,
        source: str = "memory",
        memory_id: Optional[str] = None,
        episode_id: Optional[str] = None,
    ) -> Tuple[BeliefNode, List[BeliefNode]]:
        """Add a new belief and check for contradictions.

        Returns: (new_belief, list_of_contradicting_beliefs)
        """
        keywords = self._extract_keywords(claim)

        # Check for existing similar belief (reinforce, don't duplicate)
        existing = self._find_similar(user_id, claim, domain, keywords)
        if existing:
            existing.add_evidence(
                content=f"Reinforced: {claim[:200]}",
                supports=True,
                source=source,
                confidence=confidence,
                memory_id=memory_id,
                episode_id=episode_id,
            )
            self._save_belief(existing)
            return existing, []

        now = time.time()
        belief = BeliefNode(
            id=str(uuid.uuid4()),
            user_id=user_id,
            claim=claim,
            domain=domain,
            status=BeliefStatus.PROPOSED if confidence < 0.7 else BeliefStatus.HELD,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            _claim_keywords=keywords,
            tags=[domain],
        )
        if memory_id:
            belief.source_memory_ids.append(memory_id)
        if episode_id:
            belief.source_episode_ids.append(episode_id)

        # Add initial evidence
        belief.add_evidence(
            content=f"Initial claim: {claim[:200]}",
            supports=True,
            source=source,
            confidence=confidence,
            memory_id=memory_id,
            episode_id=episode_id,
        )

        # Check for contradictions
        contradictions = self._detect_contradictions(belief)
        for contra in contradictions:
            belief.contradicts.append(contra.id)
            if belief.id not in contra.contradicts:
                contra.contradicts.append(belief.id)
            contra.status = BeliefStatus.CHALLENGED
            contra.updated_at = now
            self._save_belief(contra)

        self._beliefs[belief.id] = belief
        self._save_belief(belief)
        return belief, contradictions

    def challenge_belief(
        self,
        belief_id: str,
        contradicting_content: str,
        source: str = "observation",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> Optional[BeliefNode]:
        """Present contradicting evidence to a belief."""
        belief = self._beliefs.get(belief_id)
        if not belief:
            return None

        belief.add_evidence(
            content=contradicting_content,
            supports=False,
            source=source,
            confidence=confidence,
            memory_id=memory_id,
        )
        self._save_belief(belief)
        return belief

    def reinforce_belief(
        self,
        belief_id: str,
        supporting_content: str,
        source: str = "observation",
        confidence: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> Optional[BeliefNode]:
        """Present supporting evidence for a belief."""
        belief = self._beliefs.get(belief_id)
        if not belief:
            return None

        belief.add_evidence(
            content=supporting_content,
            supports=True,
            source=source,
            confidence=confidence,
            memory_id=memory_id,
        )
        self._save_belief(belief)
        return belief

    def get_beliefs(
        self,
        user_id: str,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        include_retracted: bool = False,
        limit: int = 20,
    ) -> List[BeliefNode]:
        """Get beliefs filtered by domain and confidence."""
        beliefs = []
        for b in self._beliefs.values():
            if b.user_id != user_id:
                continue
            if domain and b.domain != domain:
                continue
            if b.confidence < min_confidence:
                continue
            if b.status == BeliefStatus.RETRACTED and not include_retracted:
                continue
            beliefs.append(b)

        beliefs.sort(key=lambda b: b.confidence, reverse=True)
        return beliefs[:limit]

    def get_relevant_beliefs(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> List[BeliefNode]:
        """Get beliefs relevant to a query (for HyperContext injection)."""
        query_words = set(self._extract_keywords(query))
        if not query_words:
            return []

        scored: List[tuple] = []
        for b in self._beliefs.values():
            if b.user_id != user_id:
                continue
            if b.status == BeliefStatus.RETRACTED:
                continue

            b_words = set(b._claim_keywords)
            overlap = len(query_words & b_words)
            if overlap > 0:
                score = overlap * b.confidence * b.stability
                scored.append((b, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [b for b, _ in scored[:limit]]

    def get_contradictions(self, user_id: str) -> List[Tuple[BeliefNode, BeliefNode]]:
        """Get all unresolved contradiction pairs."""
        pairs = []
        seen = set()
        for b in self._beliefs.values():
            if b.user_id != user_id or not b.contradicts:
                continue
            for contra_id in b.contradicts:
                pair_key = tuple(sorted([b.id, contra_id]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                contra = self._beliefs.get(contra_id)
                if contra and contra.status != BeliefStatus.RETRACTED:
                    pairs.append((b, contra))

        return pairs

    def prune_retracted(self, user_id: str, max_age_days: int = 30) -> int:
        """Remove retracted beliefs older than max_age_days."""
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        to_remove = []

        for b_id, b in self._beliefs.items():
            if (
                b.user_id == user_id
                and b.status == BeliefStatus.RETRACTED
                and b.updated_at < cutoff
            ):
                to_remove.append(b_id)

        for b_id in to_remove:
            del self._beliefs[b_id]
            path = os.path.join(self._dir, f"{b_id}.json")
            try:
                os.remove(path)
            except OSError:
                pass
            removed += 1

            # Clean up contradiction links
            for other in self._beliefs.values():
                if b_id in other.contradicts:
                    other.contradicts.remove(b_id)

        return removed

    def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        beliefs = list(self._beliefs.values())
        if user_id:
            beliefs = [b for b in beliefs if b.user_id == user_id]

        by_status = {}
        for b in beliefs:
            by_status[b.status.value] = by_status.get(b.status.value, 0) + 1

        return {
            "total": len(beliefs),
            "by_status": by_status,
            "avg_confidence": (
                sum(b.confidence for b in beliefs) / len(beliefs)
                if beliefs else 0.0
            ),
            "contradictions": sum(1 for b in beliefs if b.contradicts),
        }

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------

    def _detect_contradictions(self, new_belief: BeliefNode) -> List[BeliefNode]:
        """Detect beliefs that potentially contradict the new one.

        Uses keyword overlap + negation pattern detection.
        """
        contradictions = []
        new_words = set(new_belief._claim_keywords)
        if len(new_words) < 2:
            return []

        new_claim_lower = new_belief.claim.lower()

        for existing in self._beliefs.values():
            if existing.user_id != new_belief.user_id:
                continue
            if existing.status == BeliefStatus.RETRACTED:
                continue
            if existing.domain != new_belief.domain:
                continue

            ex_words = set(existing._claim_keywords)
            if not ex_words:
                continue

            # Check keyword overlap
            overlap = len(new_words & ex_words)
            jaccard = overlap / len(new_words | ex_words)

            if jaccard < self.CONTRADICTION_THRESHOLD:
                continue

            # High overlap = same topic. Check for contradiction signals.
            ex_claim_lower = existing.claim.lower()
            if self._has_negation_pattern(new_claim_lower, ex_claim_lower):
                contradictions.append(existing)

        return contradictions

    @staticmethod
    def _has_negation_pattern(claim_a: str, claim_b: str) -> bool:
        """Detect if two claims about the same topic contradict each other.

        Checks for negation words, opposite adjectives, and structural patterns.
        """
        negation_words = {"not", "no", "never", "neither", "cannot", "can't",
                         "don't", "doesn't", "didn't", "won't", "isn't",
                         "aren't", "wasn't", "weren't", "shouldn't", "wouldn't"}

        words_a = set(claim_a.split())
        words_b = set(claim_b.split())

        # If one has negation and the other doesn't on similar content
        neg_a = bool(words_a & negation_words)
        neg_b = bool(words_b & negation_words)
        if neg_a != neg_b:
            return True

        # Opposite value patterns
        opposites = [
            ("true", "false"), ("yes", "no"), ("always", "never"),
            ("correct", "incorrect"), ("valid", "invalid"),
            ("should", "shouldn't"), ("can", "cannot"),
            ("works", "broken"), ("enabled", "disabled"),
            ("supports", "lacks"), ("fast", "slow"),
            ("better", "worse"), ("increase", "decrease"),
        ]
        for pos, neg in opposites:
            if (pos in claim_a and neg in claim_b) or (neg in claim_a and pos in claim_b):
                return True

        return False

    def _find_similar(
        self, user_id: str, claim: str, domain: str, keywords: List[str],
    ) -> Optional[BeliefNode]:
        """Find an existing belief that's essentially the same claim."""
        kw_set = set(keywords)
        if len(kw_set) < 2:
            return None

        for b in self._beliefs.values():
            if b.user_id != user_id or b.domain != domain:
                continue
            if b.status == BeliefStatus.RETRACTED:
                continue
            b_words = set(b._claim_keywords)
            if not b_words:
                continue
            overlap = len(kw_set & b_words) / len(kw_set | b_words)
            if overlap > 0.7:  # Very similar = same belief
                return b
        return None

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract significant keywords for comparison."""
        stop = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into",
            "and", "or", "but", "if", "it", "its", "this", "that",
        }
        words = text.lower().split()
        return [w for w in words if len(w) > 2 and w not in stop][:20]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_belief(self, belief: BeliefNode) -> None:
        path = os.path.join(self._dir, f"{belief.id}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(belief.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as e:
            logger.debug("Failed to save belief %s: %s", belief.id, e)

    def _load(self) -> None:
        if not os.path.isdir(self._dir):
            return
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                belief = BeliefNode.from_dict(data)
                self._beliefs[belief.id] = belief
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to load belief %s: %s", fname, e)

    def flush(self) -> None:
        for belief in self._beliefs.values():
            self._save_belief(belief)
