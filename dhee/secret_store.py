"""Encrypted local storage for provider API keys.

This is intentionally lightweight:

- encrypted at rest with a local Fernet master key under ``~/.dhee``
- environment variables still win for backward compatibility
- provider key "rotation" means storing a new active version and
  retiring the previous stored version

It is not a hardware-backed vault, but it is materially safer than
storing raw API keys in ``config.json``.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.fernet import Fernet

from dhee.configs.base import _dhee_data_dir

_STORE_VERSION = "1"
_MASTER_KEY_ENV = "DHEE_SECRET_STORE_KEY"
_MASTER_KEY_FILE = "secret_store.key"
_STORE_FILE = "secret_store.enc.json"

_PROVIDER_SPECS: Dict[str, Dict[str, Any]] = {
    "openai": {
        "label": "OpenAI",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "gemini": {
        "label": "Gemini",
        "env_vars": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    },
    "nvidia": {
        "label": "NVIDIA",
        "env_vars": [
            "NVIDIA_API_KEY",
            "NVIDIA_QWEN_API_KEY",
            "NVIDIA_EMBEDDING_API_KEY",
            "NVIDIA_EMBED_API_KEY",
            "NVIDIA_LLAMA_4_MAV_API_KEY",
        ],
    },
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_dir() -> Path:
    path = Path(_dhee_data_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chmod_owner_only(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)


def _master_key_path() -> Path:
    return _config_dir() / _MASTER_KEY_FILE


def _store_path() -> Path:
    return _config_dir() / _STORE_FILE


def _ensure_master_key() -> bytes:
    env_key = os.environ.get(_MASTER_KEY_ENV)
    if env_key:
        return env_key.encode("utf-8")

    key_path = _master_key_path()
    if key_path.exists():
        raw = key_path.read_bytes().strip()
        if raw:
            return raw

    key = base64.urlsafe_b64encode(os.urandom(32))
    key_path.write_bytes(key + b"\n")
    _chmod_owner_only(key_path)
    return key


def _fernet() -> Fernet:
    return Fernet(_ensure_master_key())


def _empty_store() -> Dict[str, Any]:
    return {"version": _STORE_VERSION, "providers": {}}


def _load_store() -> Dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _empty_store()
    raw = path.read_bytes()
    if not raw:
        return _empty_store()
    data = json.loads(_fernet().decrypt(raw).decode("utf-8"))
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", _STORE_VERSION)
    data.setdefault("providers", {})
    return data


def _save_store(data: Dict[str, Any]) -> None:
    path = _store_path()
    payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(_fernet().encrypt(payload))
    _chmod_owner_only(path)


def _provider_spec(provider: str) -> Dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    if normalized not in _PROVIDER_SPECS:
        raise ValueError(f"Unsupported provider: {provider}")
    return _PROVIDER_SPECS[normalized]


def provider_ids() -> list[str]:
    return list(_PROVIDER_SPECS.keys())


def provider_label(provider: str) -> str:
    spec = _PROVIDER_SPECS.get(str(provider or "").strip().lower())
    return str(spec.get("label") if spec else provider)


def get_env_api_key(provider: str) -> Tuple[Optional[str], Optional[str]]:
    spec = _provider_spec(provider)
    for env_var in spec.get("env_vars", []):
        value = os.environ.get(env_var)
        if value:
            return value, env_var
    return None, None


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 4 else key
    return f"****{tail}"


def get_stored_api_key(provider: str) -> Optional[str]:
    normalized = str(provider or "").strip().lower()
    data = _load_store()
    p = (data.get("providers") or {}).get(normalized) or {}
    active_id = p.get("active_version_id")
    for version in p.get("versions") or []:
        if version.get("id") == active_id:
            token = version.get("token")
            if not token:
                return None
            return _fernet().decrypt(str(token).encode("utf-8")).decode("utf-8")
    return None


def get_api_key(provider: str) -> Tuple[Optional[str], str, Optional[str]]:
    env_key, env_var = get_env_api_key(provider)
    if env_key:
        return env_key, "env", env_var
    stored = get_stored_api_key(provider)
    if stored:
        return stored, "stored", None
    return None, "none", None


def _provider_versions_meta(provider_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    active_id = provider_data.get("active_version_id")
    out: list[Dict[str, Any]] = []
    for version in provider_data.get("versions") or []:
        out.append(
            {
                "id": version.get("id"),
                "label": version.get("label") or "",
                "createdAt": version.get("created_at"),
                "retiredAt": version.get("retired_at"),
                "preview": version.get("preview") or "",
                "active": version.get("id") == active_id,
            }
        )
    return out


def get_provider_status(provider: str) -> Dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    spec = _provider_spec(normalized)
    data = _load_store()
    provider_data = (data.get("providers") or {}).get(normalized) or {}
    env_key, env_var = get_env_api_key(normalized)
    stored_versions = _provider_versions_meta(provider_data)
    active_entry = next((v for v in stored_versions if v.get("active")), None)
    active_source = "env" if env_key else ("stored" if active_entry else "none")
    active_preview = _mask_key(env_key) if env_key else str((active_entry or {}).get("preview") or "")
    return {
        "provider": normalized,
        "label": spec["label"],
        "envVars": list(spec.get("env_vars") or []),
        "hasEnvKey": bool(env_key),
        "hasStoredKey": bool(active_entry),
        "activeSource": active_source,
        "activeEnvVar": env_var,
        "activePreview": active_preview,
        "storedVersions": stored_versions,
        "storedVersionsCount": len(stored_versions),
        "updatedAt": provider_data.get("updated_at"),
        "rotatedAt": provider_data.get("rotated_at"),
        "note": (
            f"Environment variable {env_var} currently overrides the stored key."
            if env_key and active_entry and env_var
            else ""
        ),
    }


def list_provider_statuses() -> list[Dict[str, Any]]:
    data = _load_store()
    provider_names = set(_PROVIDER_SPECS.keys())
    provider_names.update((data.get("providers") or {}).keys())
    return [get_provider_status(name) for name in sorted(provider_names)]


def store_api_key(provider: str, api_key: str, *, label: Optional[str] = None) -> Dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    _provider_spec(normalized)
    raw_key = str(api_key or "").strip()
    if len(raw_key) < 8:
        raise ValueError("API key looks too short")

    data = _load_store()
    providers = data.setdefault("providers", {})
    provider_data = providers.setdefault(normalized, {"versions": []})
    provider_data.setdefault("versions", [])

    now = _utcnow()
    active_id = provider_data.get("active_version_id")
    if active_id:
        for version in provider_data["versions"]:
            if version.get("id") == active_id and not version.get("retired_at"):
                version["retired_at"] = now

    version_id = f"{normalized}-{int(datetime.now(timezone.utc).timestamp())}-{secrets.token_hex(4)}"
    provider_data["versions"].append(
        {
            "id": version_id,
            "label": label or f"{provider_label(normalized)} key",
            "created_at": now,
            "preview": _mask_key(raw_key),
            "token": _fernet().encrypt(raw_key.encode("utf-8")).decode("utf-8"),
        }
    )
    provider_data["active_version_id"] = version_id
    provider_data["updated_at"] = now
    provider_data["rotated_at"] = now if active_id else provider_data.get("rotated_at")
    _save_store(data)
    return get_provider_status(normalized)


def rotate_api_key(provider: str, api_key: str, *, label: Optional[str] = None) -> Dict[str, Any]:
    return store_api_key(provider, api_key, label=label or f"{provider_label(provider)} rotated key")
