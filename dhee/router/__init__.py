"""Dhee router — tool I/O digest + pointer-based raw storage.

The router wraps tools that would otherwise dump large raw output into the
model's context. Instead, it executes the operation, digests the result,
stores the raw behind a pointer, and returns only `digest + ptr` to the
model. The model can call `dhee_expand_result(ptr)` when the digest is
insufficient.

Wired wrappers: `dhee_read`, `dhee_bash`, `dhee_agent`, `dhee_expand_result`.
`dhee_grep` is planned (Movement 1) — not yet registered.
"""
