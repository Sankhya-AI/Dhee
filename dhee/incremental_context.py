"""Persistent incremental materialized context for Dhee.

This module provides a deterministic local materialized-view engine:
source records are content-hashed, versioned extractors emit target
records, target dependency edges drive dirty propagation, and a durable
file store keeps manifests plus target payload files across process
restarts.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import secrets
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union


SCHEMA_VERSION = "dhee.incremental_context.v1"
SOURCE_RECORD_SCHEMA_VERSION = "dhee.source_record.v1"
TARGET_STATE_SCHEMA_VERSION = "dhee.target_state.v1"
TARGET_PAYLOAD_SCHEMA_VERSION = "dhee.target_payload.v1"
SOURCE_EDGE = "source"
TARGET_EDGE = "target"
DEFAULT_USER_ID = "default"
DEFAULT_PRIVACY_SCOPE = "workspace"

_PRIVACY_RANKS = {
    "public": 0,
    "workspace": 1,
    "project": 2,
    "user": 3,
    "private": 4,
    "secret": 5,
}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class IncrementalContextError(ValueError):
    """Base exception for invalid incremental context graphs."""


class DuplicateTargetError(IncrementalContextError):
    """Raised when two extractors emit the same target id."""


class MissingDependencyError(IncrementalContextError):
    """Raised when a target depends on a missing source or target."""


class DependencyCycleError(IncrementalContextError):
    """Raised when target dependencies contain a cycle."""


class UnsupportedManifestError(IncrementalContextError):
    """Raised when a persisted manifest uses an unsupported schema."""


def _canonicalize(value: Any, path: str = "value") -> Any:
    if dataclasses.is_dataclass(value):
        return _canonicalize(dataclasses.asdict(value), path=path)
    if isinstance(value, Mapping):
        normalized: Dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            text_key = str(key)
            if text_key in normalized:
                raise TypeError(f"{path} has duplicate JSON key after string coercion: {text_key}")
            normalized[text_key] = _canonicalize(value[key], path=f"{path}.{text_key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item, path=f"{path}[]") for item in value]
    if isinstance(value, set):
        items = [_canonicalize(item, path=f"{path}[]") for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"{path} is not JSON-serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON representation for hashing or storage."""

    return json.dumps(
        _canonicalize(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any) -> str:
    """Return a SHA-256 hash for a JSON-safe value."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_sorted(values: Optional[Sequence[str]]) -> Tuple[str, ...]:
    if not values:
        return ()
    return tuple(sorted({str(value) for value in values}))


def _normalize_scope(value: Optional[str]) -> str:
    scope = str(value or DEFAULT_PRIVACY_SCOPE).strip().lower()
    return scope or DEFAULT_PRIVACY_SCOPE


def _privacy_rank(scope: str) -> int:
    return _PRIVACY_RANKS.get(_normalize_scope(scope), max(_PRIVACY_RANKS.values()) + 1)


def _strictest_scope(scopes: Iterable[Optional[str]]) -> str:
    normalized = [_normalize_scope(scope) for scope in scopes if scope]
    if not normalized:
        return DEFAULT_PRIVACY_SCOPE
    return max(normalized, key=lambda scope: (_privacy_rank(scope), scope))


def _target_filename(target_id: str) -> str:
    slug = _SAFE_NAME_RE.sub("_", str(target_id)).strip("._-") or "target"
    slug = slug[:72]
    suffix = hashlib.sha256(str(target_id).encode("utf-8")).hexdigest()[:16]
    return f"{slug}-{suffix}.json"


def _empty_manifest() -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "dependency_edges": [],
        "extractors": {},
        "schema_version": SCHEMA_VERSION,
        "sources": {},
        "targets": {},
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    without_hash = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    return stable_hash(without_hash)


def _manifest_hash_matches(manifest: Mapping[str, Any]) -> bool:
    return manifest.get("manifest_hash") == _manifest_hash(manifest)


def _normalize_manifest(
    manifest: Optional[Mapping[str, Any]],
    *,
    require_supported: bool = True,
) -> Dict[str, Any]:
    if not manifest:
        return _empty_manifest()
    normalized = _canonicalize(manifest)
    if not isinstance(normalized, dict):
        raise ValueError("manifest must be a mapping")
    normalized.setdefault("dependency_edges", [])
    normalized.setdefault("extractors", {})
    normalized.setdefault("sources", {})
    normalized.setdefault("targets", {})
    normalized.setdefault("schema_version", SCHEMA_VERSION)
    if require_supported and normalized.get("schema_version") != SCHEMA_VERSION:
        raise UnsupportedManifestError(
            f"unsupported incremental context manifest schema: {normalized.get('schema_version')}"
        )
    normalized.setdefault("manifest_hash", _manifest_hash(normalized))
    return normalized


def _atomic_json_write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    encoded = json.dumps(_canonicalize(data), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@dataclass(frozen=True)
class SourceRecord:
    """A raw context source with explicit lineage, privacy, and lifecycle."""

    id: str
    content: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)
    content_hash: Optional[str] = None
    schema_version: str = SOURCE_RECORD_SCHEMA_VERSION
    user_id: str = DEFAULT_USER_ID
    privacy_scope: str = DEFAULT_PRIVACY_SCOPE
    source_ref: Optional[str] = None
    deleted_at: Optional[str] = None
    redacted_at: Optional[str] = None
    redaction_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SourceRecord.id is required")
        if self.schema_version != SOURCE_RECORD_SCHEMA_VERSION:
            raise UnsupportedManifestError(f"unsupported source schema: {self.schema_version}")
        _canonicalize(self.metadata, path=f"source[{self.id}].metadata")
        object.__setattr__(self, "privacy_scope", _normalize_scope(self.privacy_scope))
        object.__setattr__(self, "user_id", str(self.user_id or DEFAULT_USER_ID))
        object.__setattr__(self, "source_ref", str(self.source_ref or self.id))
        if self.content_hash is None:
            if self.redacted_at or self.deleted_at:
                lifecycle_hash = {
                    "deleted_at": self.deleted_at,
                    "id": self.id,
                    "redacted_at": self.redacted_at,
                    "redaction_reason": self.redaction_reason,
                    "source_ref": self.source_ref,
                }
                object.__setattr__(self, "content_hash", stable_hash(lifecycle_hash))
            else:
                object.__setattr__(self, "content_hash", stable_hash(self.content))

    @property
    def active(self) -> bool:
        return not self.deleted_at and not self.redacted_at

    @property
    def metadata_hash(self) -> str:
        return stable_hash(self.metadata)

    @property
    def lifecycle_hash(self) -> str:
        return stable_hash(
            {
                "deleted_at": self.deleted_at,
                "redacted_at": self.redacted_at,
                "redaction_reason": self.redaction_reason,
            }
        )

    @property
    def state_hash(self) -> str:
        return stable_hash(
            {
                "content_hash": self.content_hash,
                "deleted_at": self.deleted_at,
                "metadata_hash": self.metadata_hash,
                "privacy_scope": self.privacy_scope,
                "redacted_at": self.redacted_at,
                "redaction_reason": self.redaction_reason,
                "schema_version": self.schema_version,
                "source_ref": self.source_ref,
                "user_id": self.user_id,
            }
        )

    def manifest_entry(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "content_hash": self.content_hash,
            "deleted_at": self.deleted_at,
            "lifecycle_hash": self.lifecycle_hash,
            "metadata_hash": self.metadata_hash,
            "privacy_scope": self.privacy_scope,
            "redacted_at": self.redacted_at,
            "redaction_reason": self.redaction_reason,
            "schema_version": self.schema_version,
            "source_ref": self.source_ref,
            "state_hash": self.state_hash,
            "user_id": self.user_id,
        }


@dataclass(frozen=True)
class ExtractionContext:
    """Read-only context handed to extractors during a build."""

    sources: Mapping[str, SourceRecord]

    def source_hash(self, source_id: str) -> str:
        return self.sources[source_id].content_hash or ""

    def source(self, source_id: str) -> SourceRecord:
        return self.sources[source_id]


@dataclass(frozen=True)
class ExtractedTarget:
    """A materialized context target emitted by an extractor.

    ``source_ids=None`` means the current source is the dependency. An
    explicit empty sequence means the target is derived only from target
    dependencies or extractor state.
    """

    id: str
    payload: Any
    kind: str = "context"
    source_ids: Optional[Sequence[str]] = None
    dependencies: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = TARGET_STATE_SCHEMA_VERSION
    user_id: Optional[str] = None
    privacy_scope: Optional[str] = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    deleted_at: Optional[str] = None
    redacted_at: Optional[str] = None
    redaction_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ExtractedTarget.id is required")
        if not self.kind:
            raise ValueError("ExtractedTarget.kind is required")
        if self.schema_version != TARGET_STATE_SCHEMA_VERSION:
            raise UnsupportedManifestError(f"unsupported target schema: {self.schema_version}")
        _canonicalize(self.payload, path=f"target[{self.id}].payload")
        _canonicalize(self.metadata, path=f"target[{self.id}].metadata")
        _canonicalize(self.provenance, path=f"target[{self.id}].provenance")


TargetLike = Union[ExtractedTarget, Mapping[str, Any]]
ExtractorFn = Callable[[SourceRecord, ExtractionContext], Iterable[TargetLike]]


@dataclass(frozen=True)
class ContextExtractor:
    """A named/versioned deterministic transformation from source to targets."""

    name: str
    version: str
    extract: ExtractorFn

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ContextExtractor.name is required")
        if not self.version:
            raise ValueError("ContextExtractor.version is required")
        if not callable(self.extract):
            raise ValueError("ContextExtractor.extract must be callable")

    def manifest_entry(self) -> Dict[str, Any]:
        return {"version": self.version}


@dataclass(frozen=True)
class DependencyEdge:
    """A directed dependency edge from a source or target to a target."""

    upstream: str
    downstream: str
    kind: str
    extractor: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "downstream": self.downstream,
            "extractor": self.extractor,
            "kind": self.kind,
            "upstream": self.upstream,
        }


@dataclass(frozen=True)
class _PendingTarget:
    id: str
    kind: str
    payload: Any
    metadata: Mapping[str, Any]
    extractor: str
    extractor_version: str
    source_ids: Tuple[str, ...]
    dependencies: Tuple[str, ...]
    schema_version: str
    user_id: Optional[str]
    privacy_scope: Optional[str]
    provenance: Mapping[str, Any]
    deleted_at: Optional[str]
    redacted_at: Optional[str]
    redaction_reason: Optional[str]


@dataclass(frozen=True)
class TargetState:
    """A fully resolved materialized target plus manifest hashes."""

    id: str
    kind: str
    payload: Any
    metadata: Mapping[str, Any]
    extractor: str
    extractor_version: str
    source_ids: Tuple[str, ...]
    source_refs: Tuple[str, ...]
    source_hashes: Mapping[str, str]
    dependencies: Tuple[str, ...]
    dependency_hashes: Mapping[str, str]
    payload_hash: str
    target_hash: str
    schema_version: str = TARGET_STATE_SCHEMA_VERSION
    user_id: str = DEFAULT_USER_ID
    privacy_scope: str = DEFAULT_PRIVACY_SCOPE
    provenance: Mapping[str, Any] = field(default_factory=dict)
    deleted_at: Optional[str] = None
    redacted_at: Optional[str] = None
    redaction_reason: Optional[str] = None
    target_path: Optional[str] = None

    @classmethod
    def from_pending(
        cls,
        pending: _PendingTarget,
        *,
        sources: Mapping[str, SourceRecord],
        dependency_states: Mapping[str, "TargetState"],
    ) -> "TargetState":
        dependency_hashes = {
            dependency_id: dependency_states[dependency_id].target_hash
            for dependency_id in pending.dependencies
        }
        transitive_source_ids: Set[str] = set(pending.source_ids)
        dependency_scopes: List[str] = []
        dependency_user_ids: List[str] = []
        for dependency_id in pending.dependencies:
            dependency = dependency_states[dependency_id]
            transitive_source_ids.update(dependency.source_ids)
            dependency_scopes.append(dependency.privacy_scope)
            dependency_user_ids.append(dependency.user_id)
        source_ids = tuple(sorted(transitive_source_ids))
        source_records = [sources[source_id] for source_id in source_ids]
        source_hashes = {source.id: source.content_hash or "" for source in source_records}
        source_refs = tuple(source.source_ref or source.id for source in source_records)
        source_scopes = [source.privacy_scope for source in source_records]
        explicit_scope = pending.privacy_scope or pending.metadata.get("privacy_scope")
        privacy_scope = _strictest_scope(
            source_scopes + dependency_scopes + ([str(explicit_scope)] if explicit_scope else [])
        )
        source_user_ids = sorted(
            {source.user_id for source in source_records if source.user_id}
            | {user_id for user_id in dependency_user_ids if user_id}
        )
        explicit_user_id = pending.user_id or pending.metadata.get("user_id")
        if explicit_user_id:
            user_id = str(explicit_user_id)
        elif len(source_user_ids) == 1:
            user_id = source_user_ids[0]
        elif source_user_ids:
            user_id = "mixed"
        else:
            user_id = DEFAULT_USER_ID

        payload_hash = stable_hash(
            {
                "kind": pending.kind,
                "metadata": pending.metadata,
                "payload": pending.payload,
            }
        )
        lineage = {
            "dependencies": list(pending.dependencies),
            "dependency_hashes": dict(sorted(dependency_hashes.items())),
            "direct_source_ids": list(pending.source_ids),
            "extractor": pending.extractor,
            "extractor_version": pending.extractor_version,
            "source_hashes": dict(sorted(source_hashes.items())),
            "source_ids": list(source_ids),
            "source_refs": list(source_refs),
        }
        provenance = {
            "extractor": pending.extractor,
            "extractor_version": pending.extractor_version,
            "direct_source_ids": list(pending.source_ids),
            "source_hashes": dict(sorted(source_hashes.items())),
            "source_ids": list(source_ids),
            "source_refs": list(source_refs),
        }
        if pending.dependencies:
            provenance["dependencies"] = list(pending.dependencies)
            provenance["dependency_hashes"] = dict(sorted(dependency_hashes.items()))
        if pending.provenance:
            provenance["extractor_provenance"] = _canonicalize(pending.provenance)

        target_hash = stable_hash(
            {
                "deleted_at": pending.deleted_at,
                "lineage": lineage,
                "payload_hash": payload_hash,
                "privacy_scope": privacy_scope,
                "provenance": provenance,
                "redacted_at": pending.redacted_at,
                "redaction_reason": pending.redaction_reason,
                "schema_version": pending.schema_version,
                "user_id": user_id,
            }
        )
        return cls(
            id=pending.id,
            kind=pending.kind,
            payload=pending.payload,
            metadata=dict(pending.metadata),
            extractor=pending.extractor,
            extractor_version=pending.extractor_version,
            source_ids=source_ids,
            source_refs=source_refs,
            source_hashes=dict(sorted(source_hashes.items())),
            dependencies=pending.dependencies,
            dependency_hashes=dict(sorted(dependency_hashes.items())),
            payload_hash=payload_hash,
            target_hash=target_hash,
            schema_version=pending.schema_version,
            user_id=user_id,
            privacy_scope=privacy_scope,
            provenance=provenance,
            deleted_at=pending.deleted_at,
            redacted_at=pending.redacted_at,
            redaction_reason=pending.redaction_reason,
            target_path=f"targets/{_target_filename(pending.id)}",
        )

    @property
    def active(self) -> bool:
        return not self.deleted_at and not self.redacted_at

    def manifest_entry(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "deleted_at": self.deleted_at,
            "dependencies": list(self.dependencies),
            "dependency_hashes": dict(self.dependency_hashes),
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "kind": self.kind,
            "metadata_hash": stable_hash(self.metadata),
            "payload_hash": self.payload_hash,
            "privacy_scope": self.privacy_scope,
            "provenance": _canonicalize(self.provenance),
            "redacted_at": self.redacted_at,
            "redaction_reason": self.redaction_reason,
            "schema_version": self.schema_version,
            "source_hashes": dict(self.source_hashes),
            "source_ids": list(self.source_ids),
            "source_refs": list(self.source_refs),
            "target_hash": self.target_hash,
            "target_path": self.target_path,
            "user_id": self.user_id,
        }

    def to_dict(self) -> Dict[str, Any]:
        entry = self.manifest_entry()
        entry.update(
            {
                "id": self.id,
                "metadata": dict(self.metadata),
                "payload": _canonicalize(self.payload),
            }
        )
        return entry

    def payload_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "metadata": dict(self.metadata),
            "payload": _canonicalize(self.payload),
            "payload_hash": self.payload_hash,
            "privacy_scope": self.privacy_scope,
            "provenance": _canonicalize(self.provenance),
            "schema_version": TARGET_PAYLOAD_SCHEMA_VERSION,
            "source_ids": list(self.source_ids),
            "source_refs": list(self.source_refs),
            "target_hash": self.target_hash,
            "target_schema_version": self.schema_version,
            "user_id": self.user_id,
        }


@dataclass(frozen=True)
class ContextBuildResult:
    """Serializable result from a plan or rebuild."""

    manifest: Mapping[str, Any]
    targets: Mapping[str, Any]
    dirty: Mapping[str, Any]
    stats: Mapping[str, Any]
    target_states: Mapping[str, TargetState] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dirty": _canonicalize(self.dirty),
            "manifest": _canonicalize(self.manifest),
            "stats": _canonicalize(self.stats),
            "targets": _canonicalize(self.targets),
        }


SourceLike = Union[SourceRecord, Mapping[str, Any], Tuple[str, Any]]


def _normalize_source(source: SourceLike) -> SourceRecord:
    if isinstance(source, SourceRecord):
        return source
    if isinstance(source, tuple) and len(source) == 2:
        return SourceRecord(id=str(source[0]), content=source[1])
    if isinstance(source, Mapping):
        if "id" not in source:
            raise ValueError("source mapping must include id")
        return SourceRecord(
            id=str(source["id"]),
            content=source.get("content", ""),
            metadata=dict(source.get("metadata") or {}),
            content_hash=source.get("content_hash"),
            schema_version=str(source.get("schema_version") or SOURCE_RECORD_SCHEMA_VERSION),
            user_id=str(source.get("user_id") or DEFAULT_USER_ID),
            privacy_scope=str(source.get("privacy_scope") or DEFAULT_PRIVACY_SCOPE),
            source_ref=source.get("source_ref"),
            deleted_at=source.get("deleted_at"),
            redacted_at=source.get("redacted_at"),
            redaction_reason=source.get("redaction_reason"),
        )
    raise TypeError(f"unsupported source type: {type(source)!r}")


def _normalize_sources(sources: Iterable[SourceLike]) -> Dict[str, SourceRecord]:
    normalized: Dict[str, SourceRecord] = {}
    for source in sources:
        record = _normalize_source(source)
        if record.id in normalized:
            raise ValueError(f"duplicate source id: {record.id}")
        normalized[record.id] = record
    return {source_id: normalized[source_id] for source_id in sorted(normalized)}


def _target_from_mapping(value: Mapping[str, Any]) -> ExtractedTarget:
    if "id" not in value:
        raise ValueError("target mapping must include id")
    if "payload" not in value:
        raise ValueError("target mapping must include payload")
    source_ids = value.get("source_ids")
    return ExtractedTarget(
        id=str(value["id"]),
        payload=value["payload"],
        kind=str(value.get("kind") or "context"),
        source_ids=None if source_ids is None else tuple(str(item) for item in source_ids),
        dependencies=tuple(str(item) for item in value.get("dependencies") or ()),
        metadata=dict(value.get("metadata") or {}),
        schema_version=str(value.get("schema_version") or TARGET_STATE_SCHEMA_VERSION),
        user_id=value.get("user_id"),
        privacy_scope=value.get("privacy_scope"),
        provenance=dict(value.get("provenance") or {}),
        deleted_at=value.get("deleted_at"),
        redacted_at=value.get("redacted_at"),
        redaction_reason=value.get("redaction_reason"),
    )


def _normalize_target(value: TargetLike, current_source_id: str) -> _PendingTarget:
    target = value if isinstance(value, ExtractedTarget) else _target_from_mapping(value)
    source_ids = (current_source_id,) if target.source_ids is None else tuple(str(item) for item in target.source_ids)
    return _PendingTarget(
        id=str(target.id),
        kind=str(target.kind),
        payload=target.payload,
        metadata=dict(target.metadata),
        extractor="",
        extractor_version="",
        source_ids=_unique_sorted(source_ids),
        dependencies=_unique_sorted(tuple(str(item) for item in target.dependencies)),
        schema_version=target.schema_version,
        user_id=target.user_id,
        privacy_scope=target.privacy_scope,
        provenance=dict(target.provenance),
        deleted_at=target.deleted_at,
        redacted_at=target.redacted_at,
        redaction_reason=target.redaction_reason,
    )


def _edge_key(edge: Union[DependencyEdge, Mapping[str, Any]]) -> Tuple[str, str, str, str]:
    if isinstance(edge, DependencyEdge):
        return (edge.kind, edge.upstream, edge.downstream, edge.extractor)
    return (
        str(edge.get("kind")),
        str(edge.get("upstream")),
        str(edge.get("downstream")),
        str(edge.get("extractor")),
    )


class FileContextStore:
    """Durable local store for incremental context manifests and targets."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"
        self.targets_dir = self.root / "targets"

    def ensure(self) -> None:
        self.targets_dir.mkdir(parents=True, exist_ok=True)

    def target_path(self, target_id: str, entry: Optional[Mapping[str, Any]] = None) -> Path:
        rel_path = str((entry or {}).get("target_path") or f"targets/{_target_filename(target_id)}")
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            rel_path = f"targets/{_target_filename(target_id)}"
        return self.root / rel_path

    def load_manifest(self, *, require_supported: bool = True) -> Optional[Dict[str, Any]]:
        if not self.manifest_path.exists():
            return None
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return _normalize_manifest(data, require_supported=require_supported)

    def write_manifest(self, manifest: Mapping[str, Any]) -> None:
        normalized = _normalize_manifest(manifest)
        _atomic_json_write(self.manifest_path, normalized)

    def write_target(self, target: TargetState) -> Path:
        self.ensure()
        path = self.target_path(target.id, target.manifest_entry())
        _atomic_json_write(path, target.payload_record())
        return path

    def remove_target(self, target_id: str, entry: Optional[Mapping[str, Any]] = None) -> bool:
        path = self.target_path(target_id, entry)
        if not path.exists():
            return False
        path.unlink()
        return True

    def read_target(self, target_id: str, entry: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return json.loads(self.target_path(target_id, entry).read_text(encoding="utf-8"))

    def target_payload_ok(self, target_id: str, entry: Mapping[str, Any]) -> bool:
        path = self.target_path(target_id, entry)
        if not path.exists():
            return False
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, JSONDecodeError):
            return False
        try:
            return _payload_record_matches_manifest(record, target_id, entry)
        except (TypeError, ValueError):
            return False

    def prune_orphan_targets(self, live_target_ids: Iterable[str], manifest: Mapping[str, Any]) -> int:
        if not self.targets_dir.exists():
            return 0
        live_paths = {
            self.target_path(target_id, (manifest.get("targets", {}) or {}).get(target_id, {})).resolve()
            for target_id in live_target_ids
        }
        removed = 0
        for path in self.targets_dir.glob("*.json"):
            if path.resolve() in live_paths:
                continue
            path.unlink()
            removed += 1
        return removed


ContextStore = FileContextStore


def _coerce_store(store: Union[FileContextStore, str, Path]) -> FileContextStore:
    if isinstance(store, FileContextStore):
        return store
    return FileContextStore(store)


class IncrementalContextEngine:
    """Deterministic persistent materialized context engine."""

    def __init__(self, extractors: Optional[Iterable[ContextExtractor]] = None):
        self._extractors: Dict[str, ContextExtractor] = {}
        for extractor in extractors or ():
            self.register_extractor(extractor)

    @property
    def extractors(self) -> Mapping[str, ContextExtractor]:
        return dict(self._extractors)

    def register_extractor(self, extractor: ContextExtractor) -> None:
        if extractor.name in self._extractors:
            raise ValueError(f"duplicate extractor name: {extractor.name}")
        self._extractors[extractor.name] = extractor

    def plan(
        self,
        sources: Iterable[SourceLike],
        *,
        previous_manifest: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dry-run the materialization and return dirty decisions without writing files."""

        return self.build(sources, previous_manifest=previous_manifest).to_dict()

    def build(
        self,
        sources: Iterable[SourceLike],
        *,
        previous_manifest: Optional[Mapping[str, Any]] = None,
    ) -> ContextBuildResult:
        previous = _normalize_manifest(previous_manifest)
        source_records = _normalize_sources(sources)
        context = ExtractionContext(sources=source_records)
        pending, edges, skipped_targets = self._extract_all(source_records, context)
        targets = self._resolve_targets(pending, source_records)
        manifest = self._build_manifest(source_records, targets, edges)
        dirty = self._diff_manifests(previous, manifest)
        stats = self._stats(
            source_records=source_records,
            targets=targets,
            manifest=manifest,
            dirty=dirty,
            skipped_targets=skipped_targets,
        )
        return ContextBuildResult(
            manifest=manifest,
            targets={target_id: targets[target_id].to_dict() for target_id in sorted(targets)},
            dirty=dirty,
            stats=stats,
            target_states=targets,
        )

    def rebuild(
        self,
        sources: Iterable[SourceLike],
        *,
        previous_manifest: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Rebuild targets in memory and return a serializable result dict."""

        return self.build(sources, previous_manifest=previous_manifest).to_dict()

    def rebuild_to_store(
        self,
        sources: Iterable[SourceLike],
        store: Union[FileContextStore, str, Path],
    ) -> Dict[str, Any]:
        """Rebuild targets, persist payloads, prune stale files, then write the manifest."""

        file_store = _coerce_store(store)
        previous_manifest = file_store.load_manifest(require_supported=True)
        result = self.build(sources, previous_manifest=previous_manifest)
        previous_targets = (previous_manifest or {}).get("targets", {}) if previous_manifest else {}
        current_targets = result.manifest.get("targets", {}) or {}
        written = 0
        skipped = 0

        for target_id in sorted(result.target_states):
            target = result.target_states[target_id]
            entry = current_targets[target_id]
            dirty = target_id in result.dirty["targets"]
            if dirty or not file_store.target_payload_ok(target_id, entry):
                file_store.write_target(target)
                written += 1
            else:
                skipped += 1

        removed = 0
        for target_id in result.dirty["removed_targets"]:
            removed += int(file_store.remove_target(target_id, previous_targets.get(target_id, {})))
        removed += file_store.prune_orphan_targets(result.target_states.keys(), result.manifest)
        file_store.write_manifest(result.manifest)

        output = result.to_dict()
        output["store"] = {
            "manifest_path": str(file_store.manifest_path),
            "removed_target_file_count": removed,
            "skipped_target_file_count": skipped,
            "target_dir": str(file_store.targets_dir),
            "written_target_file_count": written,
        }
        output["stats"] = dict(output["stats"])
        output["stats"].update(
            {
                "removed_target_file_count": removed,
                "skipped_target_file_count": skipped,
                "written_target_file_count": written,
            }
        )
        return output

    def load_manifest(self, store: Union[FileContextStore, str, Path]) -> Optional[Dict[str, Any]]:
        return _coerce_store(store).load_manifest(require_supported=True)

    def verify(self, sources: Iterable[SourceLike], manifest: Mapping[str, Any]) -> Dict[str, Any]:
        """Re-materialize sources and compare the current state to ``manifest``."""

        issues: List[Dict[str, Any]] = []
        try:
            expected = _normalize_manifest(manifest)
        except UnsupportedManifestError as exc:
            return {
                "issues": [{"code": "unsupported_schema_version", "message": str(exc)}],
                "ok": False,
                "status": "drifted",
            }
        if not _manifest_hash_matches(expected):
            issues.append({"code": "manifest_hash_mismatch"})
        current = self.build(sources, previous_manifest=expected).to_dict()
        current_manifest = current["manifest"]
        source_drift = current["dirty"]["sources"]
        target_drift = current["dirty"]["targets"]
        extractor_drift = self._extractor_drift(expected, current_manifest)
        missing_targets = sorted(set(expected["targets"]) - set(current_manifest["targets"]))
        extra_targets = sorted(set(current_manifest["targets"]) - set(expected["targets"]))
        if source_drift or target_drift or extractor_drift or missing_targets or extra_targets:
            issues.append({"code": "current_state_drift"})
        ok = (
            not issues
            and expected.get("manifest_hash") == current_manifest.get("manifest_hash")
            and not missing_targets
            and not extra_targets
        )
        return {
            "current_manifest_hash": current_manifest.get("manifest_hash"),
            "expected_manifest_hash": expected.get("manifest_hash"),
            "extractor_drift": extractor_drift,
            "extra_targets": extra_targets,
            "issues": issues,
            "missing_targets": missing_targets,
            "ok": ok,
            "source_drift": source_drift,
            "status": "verified" if ok else "drifted",
            "target_drift": target_drift,
        }

    def verify_store(
        self,
        sources: Iterable[SourceLike],
        store: Union[FileContextStore, str, Path],
    ) -> Dict[str, Any]:
        """Verify manifest graph integrity and durable target payload files."""

        file_store = _coerce_store(store)
        issues: List[Dict[str, Any]] = []
        try:
            manifest = file_store.load_manifest(require_supported=False)
        except (OSError, JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "issues": [{"code": "manifest_corrupt", "message": str(exc)}],
                "ok": False,
                "status": "corrupt",
            }
        if manifest is None:
            return {
                "issues": [{"code": "manifest_missing", "path": str(file_store.manifest_path)}],
                "ok": False,
                "status": "missing",
            }

        if manifest.get("schema_version") != SCHEMA_VERSION:
            issues.append(
                {
                    "code": "unsupported_schema_version",
                    "schema_version": manifest.get("schema_version"),
                }
            )
        if not _manifest_hash_matches(manifest):
            issues.append({"code": "manifest_hash_mismatch"})

        issues.extend(self._verify_manifest_graph(manifest))
        for target_id, entry in sorted((manifest.get("targets", {}) or {}).items()):
            issues.extend(self._verify_payload_file(file_store, target_id, entry))

        if manifest.get("schema_version") == SCHEMA_VERSION:
            current = self.verify(sources, manifest)
            if not current["ok"]:
                issues.append(
                    {
                        "code": "current_state_drift",
                        "source_drift": current.get("source_drift", {}),
                        "target_drift": current.get("target_drift", {}),
                    }
                )

        return {
            "issue_count": len(issues),
            "issues": issues,
            "manifest_hash": manifest.get("manifest_hash"),
            "ok": not issues,
            "status": "verified" if not issues else "drifted",
        }

    def _extract_all(
        self,
        sources: Mapping[str, SourceRecord],
        context: ExtractionContext,
    ) -> Tuple[Dict[str, _PendingTarget], List[DependencyEdge], List[Dict[str, Any]]]:
        pending: Dict[str, _PendingTarget] = {}
        edges: List[DependencyEdge] = []
        skipped_targets: List[Dict[str, Any]] = []
        for source_id in sorted(sources):
            source = sources[source_id]
            if not source.active:
                skipped_targets.append({"reason": "inactive_source", "source_id": source_id})
                continue
            for extractor_name in sorted(self._extractors):
                extractor = self._extractors[extractor_name]
                for raw_target in extractor.extract(source, context) or ():
                    normalized = _normalize_target(raw_target, source_id)
                    target = dataclasses.replace(
                        normalized,
                        extractor=extractor.name,
                        extractor_version=extractor.version,
                    )
                    if target.id in pending:
                        raise DuplicateTargetError(f"duplicate target id emitted: {target.id}")
                    missing_sources = [sid for sid in target.source_ids if sid not in sources]
                    if missing_sources:
                        raise MissingDependencyError(
                            f"target {target.id} references missing source(s): {', '.join(missing_sources)}"
                        )
                    inactive_sources = [sid for sid in target.source_ids if not sources[sid].active]
                    if inactive_sources:
                        skipped_targets.append(
                            {
                                "reason": "inactive_source_dependency",
                                "source_ids": inactive_sources,
                                "target_id": target.id,
                            }
                        )
                        continue
                    pending[target.id] = target
                    for upstream_source_id in target.source_ids:
                        edges.append(
                            DependencyEdge(
                                upstream=upstream_source_id,
                                downstream=target.id,
                                kind=SOURCE_EDGE,
                                extractor=extractor.name,
                            )
                        )
                    for dependency_id in target.dependencies:
                        edges.append(
                            DependencyEdge(
                                upstream=dependency_id,
                                downstream=target.id,
                                kind=TARGET_EDGE,
                                extractor=extractor.name,
                            )
                        )
        return pending, sorted(edges, key=_edge_key), skipped_targets

    def _resolve_targets(
        self,
        pending: Mapping[str, _PendingTarget],
        sources: Mapping[str, SourceRecord],
    ) -> Dict[str, TargetState]:
        states: Dict[str, TargetState] = {}
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(target_id: str, lineage: Tuple[str, ...] = ()) -> None:
            if target_id in visited:
                return
            if target_id in visiting:
                cycle = " -> ".join(lineage + (target_id,))
                raise DependencyCycleError(f"target dependency cycle: {cycle}")
            if target_id not in pending:
                raise MissingDependencyError(f"missing target dependency: {target_id}")
            visiting.add(target_id)
            target = pending[target_id]
            for dependency_id in target.dependencies:
                visit(dependency_id, lineage + (target_id,))
            states[target_id] = TargetState.from_pending(
                target,
                sources=sources,
                dependency_states=states,
            )
            visiting.remove(target_id)
            visited.add(target_id)

        for target_id in sorted(pending):
            visit(target_id)
        return {target_id: states[target_id] for target_id in sorted(states)}

    def _build_manifest(
        self,
        sources: Mapping[str, SourceRecord],
        targets: Mapping[str, TargetState],
        edges: Sequence[DependencyEdge],
    ) -> Dict[str, Any]:
        manifest: Dict[str, Any] = {
            "dependency_edges": [edge.to_dict() for edge in sorted(edges, key=_edge_key)],
            "extractors": {
                name: self._extractors[name].manifest_entry()
                for name in sorted(self._extractors)
            },
            "schema_version": SCHEMA_VERSION,
            "sources": {source_id: sources[source_id].manifest_entry() for source_id in sorted(sources)},
            "targets": {target_id: targets[target_id].manifest_entry() for target_id in sorted(targets)},
        }
        manifest["manifest_hash"] = _manifest_hash(manifest)
        return manifest

    def _diff_manifests(
        self,
        previous_manifest: Mapping[str, Any],
        current_manifest: Mapping[str, Any],
    ) -> Dict[str, Any]:
        previous_sources = previous_manifest.get("sources", {}) or {}
        current_sources = current_manifest.get("sources", {}) or {}
        dirty_sources: Dict[str, Dict[str, Any]] = {}

        for source_id in sorted(set(previous_sources) | set(current_sources)):
            previous = previous_sources.get(source_id)
            current = current_sources.get(source_id)
            reasons: List[str] = []
            if previous is None:
                reasons.append("source_added")
            elif current is None:
                reasons.append("source_removed")
            else:
                for field_name in (
                    "content_hash",
                    "metadata_hash",
                    "privacy_scope",
                    "source_ref",
                    "state_hash",
                    "user_id",
                ):
                    if previous.get(field_name) != current.get(field_name):
                        reasons.append(f"source_{field_name}_changed")
                for field_name in ("deleted_at", "redacted_at", "redaction_reason"):
                    if previous.get(field_name) != current.get(field_name):
                        reasons.append(f"source_{field_name}_changed")
            if reasons:
                dirty_sources[source_id] = {
                    "current_hash": current.get("state_hash") if current else None,
                    "previous_hash": previous.get("state_hash") if previous else None,
                    "reasons": reasons,
                }

        previous_targets = previous_manifest.get("targets", {}) or {}
        current_targets = current_manifest.get("targets", {}) or {}
        dirty_targets: Dict[str, Dict[str, Any]] = {}
        unchanged_targets: List[str] = []
        removed_targets: List[str] = []

        for target_id in sorted(set(previous_targets) | set(current_targets)):
            previous = previous_targets.get(target_id)
            current = current_targets.get(target_id)
            reasons: List[str] = []
            if previous is None:
                reasons.append("target_added")
            elif current is None:
                reasons.append("target_removed")
                removed_targets.append(target_id)
            else:
                previous_version = previous.get("extractor_version")
                current_version = current.get("extractor_version")
                if previous.get("extractor") != current.get("extractor"):
                    reasons.append("extractor_changed")
                if previous_version != current_version:
                    reasons.append(f"extractor_version_changed:{previous_version}->{current_version}")
                if previous.get("payload_hash") != current.get("payload_hash"):
                    reasons.append("payload_changed")
                if previous.get("privacy_scope") != current.get("privacy_scope"):
                    reasons.append("privacy_scope_changed")
                if previous.get("user_id") != current.get("user_id"):
                    reasons.append("user_id_changed")
                reasons.extend(
                    self._source_hash_reasons(
                        previous.get("source_hashes", {}) or {},
                        current.get("source_hashes", {}) or {},
                    )
                )
                reasons.extend(
                    self._dependency_hash_reasons(
                        previous.get("dependency_hashes", {}) or {},
                        current.get("dependency_hashes", {}) or {},
                    )
                )
                if previous.get("target_hash") != current.get("target_hash") and not reasons:
                    reasons.append("target_hash_changed")
            if reasons:
                dirty_targets[target_id] = {
                    "current_hash": current.get("target_hash") if current else None,
                    "previous_hash": previous.get("target_hash") if previous else None,
                    "reasons": reasons,
                }
            else:
                unchanged_targets.append(target_id)

        self._propagate_dirty_sources(previous_manifest, current_manifest, dirty_sources, dirty_targets)
        self._propagate_dirty_targets(previous_manifest, current_manifest, dirty_targets)
        unchanged_targets = [target_id for target_id in unchanged_targets if target_id not in dirty_targets]
        changed = bool(dirty_sources or dirty_targets)
        return {
            "changed": changed,
            "removed_targets": sorted(removed_targets),
            "sources": dirty_sources,
            "targets": dirty_targets,
            "unchanged_targets": sorted(unchanged_targets),
        }

    def _stats(
        self,
        *,
        source_records: Mapping[str, SourceRecord],
        targets: Mapping[str, TargetState],
        manifest: Mapping[str, Any],
        dirty: Mapping[str, Any],
        skipped_targets: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        dirty_targets = dirty.get("targets", {}) or {}
        removed = set(dirty.get("removed_targets", []) or [])
        added = [
            target_id
            for target_id, entry in dirty_targets.items()
            if "target_added" in entry.get("reasons", [])
        ]
        changed = [
            target_id
            for target_id in dirty_targets
            if target_id not in removed and target_id not in added
        ]
        active_sources = [source for source in source_records.values() if source.active]
        return {
            "active_source_count": len(active_sources),
            "added_target_count": len(added),
            "changed": bool(dirty.get("changed")),
            "changed_target_count": len(changed),
            "dirty_source_count": len(dirty.get("sources", {}) or {}),
            "dirty_target_count": len(dirty_targets),
            "edge_count": len(manifest.get("dependency_edges", []) or []),
            "extractor_count": len(self._extractors),
            "removed_target_count": len(removed),
            "skipped_source_count": len(source_records) - len(active_sources),
            "skipped_target_count": len(skipped_targets),
            "source_count": len(source_records),
            "target_count": len(targets),
            "unchanged_target_count": len(dirty.get("unchanged_targets", []) or []),
        }

    def _source_hash_reasons(self, previous: Mapping[str, str], current: Mapping[str, str]) -> List[str]:
        reasons: List[str] = []
        for source_id in sorted(set(previous) | set(current)):
            if source_id not in previous:
                reasons.append(f"source_added:{source_id}")
            elif source_id not in current:
                reasons.append(f"source_removed:{source_id}")
            elif previous[source_id] != current[source_id]:
                reasons.append(f"source_changed:{source_id}")
        return reasons

    def _dependency_hash_reasons(self, previous: Mapping[str, str], current: Mapping[str, str]) -> List[str]:
        reasons: List[str] = []
        for dependency_id in sorted(set(previous) | set(current)):
            if dependency_id not in previous:
                reasons.append(f"dependency_added:{dependency_id}")
            elif dependency_id not in current:
                reasons.append(f"dependency_removed:{dependency_id}")
            elif previous[dependency_id] != current[dependency_id]:
                reasons.append(f"dependency_changed:{dependency_id}")
        return reasons

    def _propagate_dirty_sources(
        self,
        previous_manifest: Mapping[str, Any],
        current_manifest: Mapping[str, Any],
        dirty_sources: Mapping[str, Mapping[str, Any]],
        dirty_targets: Dict[str, Dict[str, Any]],
    ) -> None:
        downstream = self._downstream_source_targets(previous_manifest)
        for upstream, targets in self._downstream_source_targets(current_manifest).items():
            downstream.setdefault(upstream, set()).update(targets)

        for source_id in sorted(dirty_sources):
            for downstream_id in sorted(downstream.get(source_id, ())):
                if downstream_id not in current_manifest.get("targets", {}):
                    continue
                if downstream_id in dirty_targets:
                    continue
                dirty_targets[downstream_id] = {
                    "current_hash": current_manifest["targets"][downstream_id].get("target_hash"),
                    "previous_hash": (previous_manifest.get("targets", {}) or {}).get(downstream_id, {}).get(
                        "target_hash"
                    ),
                    "reasons": [f"source_dirty:{source_id}"],
                }

    def _propagate_dirty_targets(
        self,
        previous_manifest: Mapping[str, Any],
        current_manifest: Mapping[str, Any],
        dirty_targets: Dict[str, Dict[str, Any]],
    ) -> None:
        downstream = self._downstream_targets(previous_manifest)
        for upstream, targets in self._downstream_targets(current_manifest).items():
            downstream.setdefault(upstream, set()).update(targets)

        queue = sorted(dirty_targets)
        seen = set(queue)
        while queue:
            upstream = queue.pop(0)
            for downstream_id in sorted(downstream.get(upstream, ())):
                if downstream_id not in current_manifest.get("targets", {}):
                    continue
                entry = dirty_targets.setdefault(
                    downstream_id,
                    {
                        "current_hash": current_manifest["targets"][downstream_id].get("target_hash"),
                        "previous_hash": (previous_manifest.get("targets", {}) or {}).get(downstream_id, {}).get(
                            "target_hash"
                        ),
                        "reasons": [],
                    },
                )
                reason = f"dependency_dirty:{upstream}"
                if reason not in entry["reasons"]:
                    entry["reasons"].append(reason)
                if downstream_id not in seen:
                    seen.add(downstream_id)
                    queue.append(downstream_id)

    def _downstream_source_targets(self, manifest: Mapping[str, Any]) -> Dict[str, Set[str]]:
        downstream: Dict[str, Set[str]] = {}
        for edge in manifest.get("dependency_edges", []) or []:
            if edge.get("kind") != SOURCE_EDGE:
                continue
            downstream.setdefault(str(edge.get("upstream")), set()).add(str(edge.get("downstream")))
        return downstream

    def _downstream_targets(self, manifest: Mapping[str, Any]) -> Dict[str, Set[str]]:
        downstream: Dict[str, Set[str]] = {}
        for edge in manifest.get("dependency_edges", []) or []:
            if edge.get("kind") != TARGET_EDGE:
                continue
            downstream.setdefault(str(edge.get("upstream")), set()).add(str(edge.get("downstream")))
        return downstream

    def _extractor_drift(
        self,
        expected_manifest: Mapping[str, Any],
        current_manifest: Mapping[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        expected = expected_manifest.get("extractors", {}) or {}
        current = current_manifest.get("extractors", {}) or {}
        drift: Dict[str, Dict[str, Any]] = {}
        for name in sorted(set(expected) | set(current)):
            previous_version = (expected.get(name) or {}).get("version")
            current_version = (current.get(name) or {}).get("version")
            if previous_version != current_version:
                drift[name] = {
                    "current_version": current_version,
                    "previous_version": previous_version,
                }
        return drift

    def _verify_manifest_graph(self, manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        sources = manifest.get("sources", {}) or {}
        targets = manifest.get("targets", {}) or {}
        edge_keys: Set[Tuple[str, str, str, str]] = set()
        for edge in manifest.get("dependency_edges", []) or []:
            key = _edge_key(edge)
            if key in edge_keys:
                issues.append({"code": "duplicate_dependency_edge", "edge": dict(edge)})
            edge_keys.add(key)
            kind = edge.get("kind")
            upstream = str(edge.get("upstream"))
            downstream = str(edge.get("downstream"))
            if downstream not in targets:
                issues.append({"code": "dangling_dependency_edge", "edge": dict(edge)})
            if kind == SOURCE_EDGE and upstream not in sources:
                issues.append({"code": "missing_source_reference", "edge": dict(edge)})
            if kind == TARGET_EDGE and upstream not in targets:
                issues.append({"code": "dangling_target_dependency", "edge": dict(edge)})

        for source_id, source in sorted(sources.items()):
            if not source.get("source_ref"):
                issues.append({"code": "missing_source_reference", "source_id": source_id})
            if source.get("schema_version") != SOURCE_RECORD_SCHEMA_VERSION:
                issues.append({"code": "unsupported_source_schema", "source_id": source_id})

        dependency_graph: Dict[str, Set[str]] = {}
        for target_id, target in sorted(targets.items()):
            if target.get("schema_version") != TARGET_STATE_SCHEMA_VERSION:
                issues.append({"code": "unsupported_target_schema", "target_id": target_id})
            source_ids = [str(item) for item in target.get("source_ids", [])]
            for source_id in source_ids:
                if source_id not in sources:
                    issues.append({"code": "missing_source_reference", "source_id": source_id, "target_id": target_id})
            expected_refs = [sources[source_id].get("source_ref") for source_id in source_ids if source_id in sources]
            if list(target.get("source_refs", [])) != expected_refs:
                issues.append({"code": "source_ref_mismatch", "target_id": target_id})
            expected_scope = _strictest_scope(
                [sources[source_id].get("privacy_scope") for source_id in source_ids if source_id in sources]
                + [target.get("metadata_privacy_scope")]
            )
            if source_ids and _privacy_rank(str(target.get("privacy_scope"))) < _privacy_rank(expected_scope):
                issues.append(
                    {
                        "code": "privacy_scope_mismatch",
                        "expected_at_least": expected_scope,
                        "target_id": target_id,
                    }
                )
            dependencies = [str(item) for item in target.get("dependencies", [])]
            dependency_graph[target_id] = set(dependencies)
            for dependency_id in dependencies:
                if dependency_id not in targets:
                    issues.append(
                        {
                            "code": "dangling_target_dependency",
                            "dependency_id": dependency_id,
                            "target_id": target_id,
                        }
                    )
        issues.extend(_find_dependency_cycles(dependency_graph))
        return issues

    def _verify_payload_file(
        self,
        store: FileContextStore,
        target_id: str,
        entry: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        path = store.target_path(target_id, entry)
        if not path.exists():
            return [{"code": "target_payload_missing", "path": str(path), "target_id": target_id}]
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except JSONDecodeError as exc:
            return [
                {
                    "code": "target_payload_corrupt",
                    "message": str(exc),
                    "path": str(path),
                    "target_id": target_id,
                }
            ]
        issues: List[Dict[str, Any]] = []
        try:
            payload_matches = _payload_record_matches_manifest(record, target_id, entry)
        except (TypeError, ValueError) as exc:
            issues.append(
                {
                    "code": "target_payload_corrupt",
                    "message": str(exc),
                    "path": str(path),
                    "target_id": target_id,
                }
            )
            payload_matches = True
        if not payload_matches:
            issues.append({"code": "target_payload_hash_mismatch", "path": str(path), "target_id": target_id})
        if record.get("privacy_scope") != entry.get("privacy_scope"):
            issues.append({"code": "privacy_scope_mismatch", "path": str(path), "target_id": target_id})
        return issues


def _payload_record_matches_manifest(record: Mapping[str, Any], target_id: str, entry: Mapping[str, Any]) -> bool:
    if record.get("schema_version") != TARGET_PAYLOAD_SCHEMA_VERSION:
        return False
    if record.get("id") != target_id:
        return False
    payload_hash = stable_hash(
        {
            "kind": record.get("kind"),
            "metadata": record.get("metadata", {}),
            "payload": record.get("payload"),
        }
    )
    return (
        payload_hash == entry.get("payload_hash")
        and record.get("payload_hash") == entry.get("payload_hash")
        and record.get("target_hash") == entry.get("target_hash")
    )


def _find_dependency_cycles(graph: Mapping[str, Set[str]]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node: str, lineage: Tuple[str, ...]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = list(lineage + (node,))
            issues.append({"code": "dependency_cycle", "cycle": cycle})
            return
        visiting.add(node)
        for dependency in sorted(graph.get(node, ())):
            if dependency in graph:
                visit(dependency, lineage + (node,))
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, ())
    return issues


def materialize_context(
    sources: Iterable[SourceLike],
    extractors: Iterable[ContextExtractor],
    *,
    previous_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper for a one-shot deterministic rebuild."""

    return IncrementalContextEngine(extractors).rebuild(sources, previous_manifest=previous_manifest)


def verify_context_manifest(
    sources: Iterable[SourceLike],
    extractors: Iterable[ContextExtractor],
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    """Convenience wrapper for one-shot manifest verification."""

    return IncrementalContextEngine(extractors).verify(sources, manifest)


def manifest_to_json(manifest: Mapping[str, Any]) -> str:
    """Serialize a manifest with stable key order and a trailing newline."""

    normalized = _normalize_manifest(manifest)
    return json.dumps(normalized, indent=2, sort_keys=True) + "\n"


def manifest_from_json(data: Union[str, bytes]) -> Dict[str, Any]:
    """Parse and normalize a manifest JSON document."""

    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return _normalize_manifest(json.loads(data))


__all__ = [
    "ContextBuildResult",
    "ContextExtractor",
    "ContextStore",
    "DependencyCycleError",
    "DependencyEdge",
    "DuplicateTargetError",
    "ExtractedTarget",
    "ExtractionContext",
    "FileContextStore",
    "IncrementalContextEngine",
    "IncrementalContextError",
    "MissingDependencyError",
    "SCHEMA_VERSION",
    "SOURCE_RECORD_SCHEMA_VERSION",
    "SourceRecord",
    "TARGET_STATE_SCHEMA_VERSION",
    "TargetState",
    "UnsupportedManifestError",
    "canonical_json",
    "manifest_from_json",
    "manifest_to_json",
    "materialize_context",
    "stable_hash",
    "verify_context_manifest",
]
