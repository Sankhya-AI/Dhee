# `.dheemem` Portable Memory Pack v1

This document defines the current portable archive format used by Dhee to move
durable memory, host-extracted artifact knowledge, and retrieval state across
machines and harnesses.

## Goals

- Preserve durable memory identity across export/import.
- Preserve host-parsed artifact knowledge without re-upload or re-OCR.
- Preserve retrieval quality without fresh embedding/model calls at import time.
- Detect post-export tampering with manifest signatures and per-file hashes.

## File Format

- Container: ZIP archive
- Recommended extension: `.dheemem`
- Manifest: `manifest.json`
- Payload files: newline-delimited JSON (`*.jsonl`)

## Required Files

- `manifest.json`
- `handoff.json`
- `memories.jsonl`
- `memory_history.jsonl`
- `distillation_provenance.jsonl`
- `vector_nodes.jsonl`
- `artifacts_manifest.jsonl`
- `artifact_bindings.jsonl`
- `artifact_extractions.jsonl`
- `artifact_chunks.jsonl`

## Manifest

`manifest.json` contains:

- `format`: always `dheemem`
- `version`: current version string, `1`
- `created_at`: UTC timestamp
- `user_id`: exported user scope
- `files`: per-file metadata
  - `sha256`
  - `records`
- `signature`
  - `algorithm`: currently `ed25519`
  - `key_id`
  - `public_key_pem`
  - `signature_b64`

The manifest signature covers the manifest payload excluding the `signature`
field itself.

## `handoff.json`

`handoff.json` is a derived operational snapshot, not the source of truth.
It exists to help a fresh harness or machine resume quickly before deeper
memory retrieval kicks in.

Current contents include:

- latest session digest summary
- active and recent tasks
- active intentions
- recent durable memories
- recent reusable artifacts
- compact resume hints

Import does not write `handoff.json` into durable stores. It is consumed as a
portable bootstrap hint.

## Payload Semantics

### `memories.jsonl`

Canonical durable memory rows exported directly from the history database.
These rows preserve:

- memory `id`
- content
- categories
- metadata
- source attribution
- timestamps
- strength/layer/state fields
- stored base embedding

### `memory_history.jsonl`

The durable audit/history rows for each exported memory. This allows `dhee why`
to remain useful after importing a pack on a new machine.

### `distillation_provenance.jsonl`

Distillation lineage edges between episodic and semantic memories. These rows
explain synthesis-backed memories after portability import.

### `vector_nodes.jsonl`

Portable vector index entries. Each row includes:

- vector node `id`
- `vector`
- `payload`

This file preserves retrieval state so a new machine can import the pack
without re-embedding the memories.

### `artifacts_manifest.jsonl`

Artifact identity rows from `artifact_assets`.

### `artifact_bindings.jsonl`

Workspace/project/folder bindings for each artifact.

### `artifact_extractions.jsonl`

Host-produced extracted text bodies. Dhee does not create these bodies with its
own OCR/LLM path during export/import.

### `artifact_chunks.jsonl`

Chunked extracted artifact content used for reuse and retrieval.

## Import Strategies

### `merge`

- keeps existing local state
- inserts new memories with preserved IDs
- skips incoming memories whose IDs already exist
- skips incoming memories whose content hash already exists under the target user
- imports only vector nodes whose `payload.memory_id` resolves to a real local
  memory after merge

### `replace`

- deletes current user-scoped memories, vector nodes, artifact rows, and common
  derived structured rows
- imports the pack as the new local truth for that user scope

### `dry-run`

- validates the archive
- reports counts and conflicts
- performs no writes

## Integrity Rules

Import must fail if:

- the manifest signature is invalid
- any required file is missing
- any required file hash does not match the manifest

## Current Non-Goals

These are intentionally not part of v1:

- raw binary artifact payloads inside the archive
- trust chains beyond the embedded public key
- social/shared multi-user packs
- automatic schema migration between incompatible major pack versions

## Compatibility

The Dhee CLI currently supports:

- `dhee export --output pack.dheemem`
- `dhee import pack.dheemem --strategy merge|replace|dry-run`

Legacy JSON export/import remains supported for older workflows, but `.dheemem`
is the canonical portable format.
