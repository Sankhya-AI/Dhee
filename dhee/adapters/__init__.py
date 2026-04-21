"""Third-party tool adapters that ingest external memory surfaces into Dhee.

Each adapter lives in its own module and exposes a minimal surface:

* ``detect()`` — best-effort discovery; never raises
* ``backfill(...)`` — ingest everything not already seen
* ``tail_ingest(...)`` — best-effort delta ingest called from session hooks

Adapters write through the standard ``Dhee.remember`` API so ingested
atoms flow through the same embedding, engram extraction, conflict, and
forgetting pipelines as every other Dhee memory.
"""
