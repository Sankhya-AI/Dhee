"""Retrieval scoring functions for Engram memory search.

Requires dhee-accel (Rust) for tokenize and BM25 operations.
"""

import math
from typing import Dict, List, Any, Optional, Set

from dhee_accel import tokenize as _rs_tokenize, bm25_score_batch as _rs_bm25_batch


def composite_score(similarity: float, strength: float) -> float:
    """Calculate composite score from similarity and strength."""
    return similarity * strength


def tokenize(text: str) -> List[str]:
    """Tokenize text for BM25 scoring (Rust-accelerated)."""
    return _rs_tokenize(text)


def calculate_bm25_score(
    query_terms: Set[str],
    doc_terms: List[str],
    doc_freq: Dict[str, int],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Calculate BM25 score for a document against query terms."""
    if not doc_terms or not query_terms:
        return 0.0

    doc_len = len(doc_terms)
    if avg_doc_len == 0:
        avg_doc_len = doc_len or 1

    term_freq: Dict[str, int] = {}
    for term in doc_terms:
        term_freq[term] = term_freq.get(term, 0) + 1

    score = 0.0
    for term in query_terms:
        if term not in term_freq:
            continue

        tf = term_freq[term]
        df = doc_freq.get(term, 1)

        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        tf_component = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))

        score += idf * tf_component

    return score


def bm25_score_batch(
    query_terms: List[str],
    documents: List[List[str]],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Batch BM25 scoring for N documents (Rust-accelerated)."""
    return _rs_bm25_batch(query_terms, documents, total_docs, avg_doc_len, k1, b)


def calculate_keyword_score(
    query_terms: Set[str],
    memory_content: str,
    echo_keywords: Optional[List[str]] = None,
    echo_paraphrases: Optional[List[str]] = None,
) -> float:
    """Calculate keyword match score for a memory."""
    if not query_terms:
        return 0.0

    content_terms = set(tokenize(memory_content))

    if echo_keywords:
        content_terms.update(kw.lower() for kw in echo_keywords)

    if echo_paraphrases:
        for paraphrase in echo_paraphrases:
            content_terms.update(tokenize(paraphrase))

    if not content_terms:
        return 0.0

    matches = query_terms & content_terms
    if not matches:
        return 0.0

    score = len(matches) / len(query_terms)
    return score


def build_sparse_vector(text: str, dim: int = 30000) -> Dict[int, float]:
    """Build a sparse BM25-like weight vector from text.

    Tokenizes via Rust, hashes tokens to sparse indices, and returns
    a dict mapping index → weight. Useful for hybrid dense+sparse search
    if the vector store supports sparse fields.
    """
    import hashlib as _hashlib

    tokens = tokenize(text)
    if not tokens:
        return {}

    # Term frequency
    tf: Dict[str, int] = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1

    sparse: Dict[int, float] = {}
    doc_len = len(tokens)
    for token, count in tf.items():
        # Hash token to a sparse index
        h = int(_hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        # BM25-like weight: tf / (tf + 1)
        weight = count / (count + 1.0)
        # Accumulate in case of hash collision
        sparse[idx] = sparse.get(idx, 0.0) + weight

    return sparse


def hybrid_score(
    semantic_score: float,
    keyword_score: float,
    alpha: float = 0.7,
) -> float:
    """Combine semantic and keyword scores using weighted average."""
    return alpha * semantic_score + (1 - alpha) * keyword_score


class HybridSearcher:
    """Helper class for hybrid search across memories."""

    def __init__(self, alpha: float = 0.7, contrastive_boost: float = 0.0):
        self.alpha = alpha
        self.contrastive_boost = contrastive_boost

    def score_memory(
        self,
        query_terms: Set[str],
        semantic_similarity: float,
        memory_content: str,
        echo_keywords: Optional[List[str]] = None,
        echo_paraphrases: Optional[List[str]] = None,
        strength: float = 1.0,
        contrastive_signal: float = 0.0,
    ) -> Dict[str, float]:
        keyword_score = calculate_keyword_score(
            query_terms=query_terms,
            memory_content=memory_content,
            echo_keywords=echo_keywords,
            echo_paraphrases=echo_paraphrases,
        )

        hybrid = hybrid_score(semantic_similarity, keyword_score, self.alpha)

        # Apply contrastive boost: results aligned with past successes score higher
        if self.contrastive_boost > 0 and contrastive_signal > 0:
            hybrid += self.contrastive_boost * contrastive_signal

        return {
            "semantic_score": semantic_similarity,
            "keyword_score": keyword_score,
            "hybrid_score": hybrid,
            "contrastive_signal": contrastive_signal,
            "composite_score": composite_score(hybrid, strength),
        }
