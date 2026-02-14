"""Engram Observability — lightweight no-op stub.

Full observability (Prometheus export, structured logging, etc.)
lives in engram-enterprise.
"""


class _NoOpMetrics:
    """Drop-in replacement that silently discards all metric calls."""

    def record_add(self, *a, **kw): pass
    def record_search(self, *a, **kw): pass
    def record_decay(self, *a, **kw): pass
    def record_get(self, *a, **kw): pass
    def record_delete(self, *a, **kw): pass
    def record_masked_hits(self, *a, **kw): pass
    def record_staged_commit(self, *a, **kw): pass
    def record_commit_approval(self, *a, **kw): pass
    def record_commit_rejection(self, *a, **kw): pass
    def record_ref_protected_skip(self, *a, **kw): pass
    def get_summary(self): return {}


metrics = _NoOpMetrics()
