"""Retrieval scoring functions for Engram memory search.

Uses dhee-accel (Rust) when available, pure-Python fallback otherwise.
"""

import math
import re
from typing import Dict, List, Any, Optional, Set

try:
    from dhee_accel import tokenize as _rs_tokenize, bm25_score_batch as _rs_bm25_batch
    _ACCEL = True
except ImportError:
    _ACCEL = False

_TOKEN_RE = re.compile(r'[a-z0-9_]+')


def composite_score(similarity: float, strength: float) -> float:
    """Calculate composite score from similarity and strength."""
    return similarity * strength


def _py_tokenize(text: str) -> List[str]:
    """Pure-Python tokenize: lowercase, split on non-alphanumeric boundaries."""
    return _TOKEN_RE.findall(text.lower())


def tokenize(text: str) -> List[str]:
    """Tokenize text for BM25 scoring."""
    if _ACCEL:
        return _rs_tokenize(text)
    return _py_tokenize(text)


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


def _py_bm25_batch(
    query_terms: List[str],
    documents: List[List[str]],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Pure-Python batch BM25 scoring."""
    if not query_terms or not documents:
        return [0.0] * len(documents)

    total_docs_f = float(total_docs)
    if avg_doc_len == 0.0:
        avg_doc_len = 1.0

    # Document frequency for query terms
    doc_freq: Dict[str, int] = {}
    for term in query_terms:
        count = sum(1 for doc in documents if term in doc)
        doc_freq[term] = count

    scores = []
    for doc in documents:
        if not doc:
            scores.append(0.0)
            continue

        tf: Dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1

        doc_len = float(len(doc))
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            term_f = float(tf[term])
            df = float(doc_freq.get(term, 1))
            idf = math.log((total_docs_f - df + 0.5) / (df + 0.5) + 1.0)
            tf_component = (term_f * (k1 + 1.0)) / (term_f + k1 * (1.0 - b + b * doc_len / avg_doc_len))
            score += idf * tf_component
        scores.append(score)

    return scores


def bm25_score_batch(
    query_terms: List[str],
    documents: List[List[str]],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Batch BM25 scoring for N documents."""
    if _ACCEL:
        return _rs_bm25_batch(query_terms, documents, total_docs, avg_doc_len, k1, b)
    return _py_bm25_batch(query_terms, documents, total_docs, avg_doc_len, k1, b)


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
    """Build a sparse BM25-like weight vector from text."""
    import hashlib as _hashlib

    tokens = tokenize(text)
    if not tokens:
        return {}

    tf: Dict[str, int] = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1

    sparse: Dict[int, float] = {}
    for token, count in tf.items():
        h = int(_hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        weight = count / (count + 1.0)
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

        if self.contrastive_boost > 0 and contrastive_signal > 0:
            hybrid += self.contrastive_boost * contrastive_signal

        return {
            "semantic_score": semantic_similarity,
            "keyword_score": keyword_score,
            "hybrid_score": hybrid,
            "contrastive_signal": contrastive_signal,
            "composite_score": composite_score(hybrid, strength),
        }
