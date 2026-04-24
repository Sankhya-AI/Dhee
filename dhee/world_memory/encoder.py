from __future__ import annotations

import base64
import hashlib
import io
import os
from dataclasses import dataclass
from typing import List, Optional

import requests

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


class DeterministicFrameEncoder:
    """Deterministic stand-in for a frozen vision encoder."""

    def __init__(self, dims: int = 32):
        self.dims = max(4, int(dims))

    def encode_frame(self, frame_ref: str, extra_text: str = "") -> List[float]:
        seed = f"{frame_ref}::{extra_text}".encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        values: List[float] = []
        while len(values) < self.dims:
            digest = hashlib.sha256(digest + seed).digest()
            for idx in range(0, len(digest), 2):
                if len(values) >= self.dims:
                    break
                pair = int.from_bytes(digest[idx:idx + 2], "big")
                values.append((pair / 65535.0) * 2.0 - 1.0)
        return _normalize(values)

    def encode_text(self, text: str) -> List[float]:
        return self.encode_frame(f"text://{text}")


class ContentAwareFrameEncoder:
    """Content-aware encoder for screenshots and structured text."""

    def __init__(self, dims: int = 64, fallback: DeterministicFrameEncoder | None = None):
        self.dims = max(16, int(dims))
        self.fallback = fallback or DeterministicFrameEncoder(dims=min(self.dims, 32))

    def encode_frame(self, frame_ref: str, extra_text: str = "") -> List[float]:
        if frame_ref.startswith("data:image/"):
            image_bytes = _decode_data_url(frame_ref)
            if image_bytes is not None:
                features = self._encode_image_bytes(image_bytes)
                if features:
                    return _blend_with_text(features, self.encode_text(extra_text))

        if os.path.exists(frame_ref):
            lower = frame_ref.lower()
            if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                try:
                    with open(frame_ref, "rb") as handle:
                        features = self._encode_image_bytes(handle.read())
                    if features:
                        return _blend_with_text(features, self.encode_text(extra_text))
                except OSError:
                    pass
            try:
                with open(frame_ref, "rb") as handle:
                    payload = handle.read()
                return _blend_with_text(self._encode_text_bytes(payload), self.encode_text(extra_text))
            except OSError:
                pass
        return self.fallback.encode_frame(frame_ref, extra_text)

    def encode_text(self, text: str) -> List[float]:
        return self._encode_text_bytes(text.encode("utf-8"))

    def _encode_image_bytes(self, raw: bytes) -> List[float]:
        if Image is None:
            return []
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image = image.resize((8, 8))
        except Exception:
            return []
        pixels = list(image.getdata())
        features: List[float] = []
        for channel in range(3):
            channel_values = [pixel[channel] / 255.0 for pixel in pixels]
            features.extend(_summarize_series(channel_values))
        grayscale = [(r + g + b) / (3.0 * 255.0) for r, g, b in pixels]
        features.extend(_summarize_series(grayscale))
        features.extend(grayscale[: min(24, len(grayscale))])
        return _project_or_pad(features, self.dims)

    def _encode_text_bytes(self, raw: bytes) -> List[float]:
        text = raw.decode("utf-8", errors="ignore")
        collapsed = " ".join(text.split())
        if not collapsed:
            return self.fallback.encode_frame(hashlib.sha256(raw).hexdigest())
        words = collapsed.lower().split()
        word_lengths = [len(word) / 32.0 for word in words[:128]]
        features: List[float] = []
        features.extend(_summarize_series(word_lengths))
        features.extend(
            [
                min(len(collapsed) / 4000.0, 1.0),
                min(len(words) / 500.0, 1.0),
                collapsed.count("<") / max(1.0, len(collapsed)),
                collapsed.count(">") / max(1.0, len(collapsed)),
                collapsed.count("/") / max(1.0, len(collapsed)),
            ]
        )
        features.extend(_char_bucket_features(collapsed))
        return _project_or_pad(features, self.dims)


@dataclass
class FrameEmbeddingPayload:
    input_value: str
    modality: str


class NvidiaVLFrameEncoder:
    """NVIDIA NIM-backed screen encoder with local fallback."""

    def __init__(
        self,
        *,
        model: str = "nvidia/llama-nemotron-embed-vl-1b-v2",
        api_key: Optional[str] = None,
        base_url: str = "https://integrate.api.nvidia.com/v1/embeddings",
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
        fallback: Optional[ContentAwareFrameEncoder] = None,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self.base_url = base_url
        self.timeout_s = float(timeout_s)
        self.session = session or requests.Session()
        self.fallback = fallback or ContentAwareFrameEncoder(dims=64)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def encode_frame(self, frame_ref: str, extra_text: str = "") -> List[float]:
        payload = _build_frame_payload(frame_ref, extra_text=extra_text)
        if not payload or not self.is_available():
            return self.fallback.encode_frame(frame_ref, extra_text)
        try:
            return self._embed_inputs(
                inputs=[payload.input_value],
                modalities=[payload.modality],
                input_type="passage",
            )[0]
        except Exception:
            return self.fallback.encode_frame(frame_ref, extra_text)

    def encode_text(self, text: str) -> List[float]:
        if not self.is_available():
            return self.fallback.encode_text(text)
        try:
            return self._embed_inputs(
                inputs=[text],
                modalities=["text"],
                input_type="query",
            )[0]
        except Exception:
            return self.fallback.encode_text(text)

    def _embed_inputs(
        self,
        *,
        inputs: List[str],
        modalities: List[str],
        input_type: str,
    ) -> List[List[float]]:
        response = self.session.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": inputs,
                "encoding_format": "float",
                "input_type": input_type,
                "truncate": "NONE",
                "modality": modalities,
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        body = response.json()
        rows = sorted(body.get("data", []), key=lambda item: int(item.get("index", 0)))
        return [list(item.get("embedding", [])) for item in rows]


def create_default_encoder(dims: int = 64):
    fallback = ContentAwareFrameEncoder(dims=dims)
    if os.environ.get("NVIDIA_API_KEY"):
        return NvidiaVLFrameEncoder(fallback=fallback)
    return fallback


def _build_frame_payload(frame_ref: str, *, extra_text: str = "") -> FrameEmbeddingPayload | None:
    if frame_ref.startswith("data:image/"):
        return FrameEmbeddingPayload(input_value=frame_ref, modality="image")
    if os.path.exists(frame_ref):
        lower = frame_ref.lower()
        if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            try:
                payload = base64.b64encode(open(frame_ref, "rb").read()).decode("ascii")
            except OSError:
                return None
            return FrameEmbeddingPayload(input_value=f"data:image/png;base64,{payload}", modality="image")
        try:
            text = open(frame_ref, "r", encoding="utf-8", errors="ignore").read()
        except OSError:
            return None
        return FrameEmbeddingPayload(input_value="\n".join([extra_text, text]).strip(), modality="text")
    if extra_text:
        return FrameEmbeddingPayload(input_value=extra_text, modality="text")
    return None


def _decode_data_url(value: str) -> bytes | None:
    try:
        payload = value.split(",", 1)[1]
    except IndexError:
        return None
    try:
        return base64.b64decode(payload)
    except Exception:
        return None


def _normalize(values: List[float]) -> List[float]:
    norm = sum(v * v for v in values) ** 0.5
    if norm <= 0.0:
        return [0.0 for _ in values]
    return [v / norm for v in values]


def _summarize_series(values: List[float]) -> List[float]:
    if not values:
        return [0.0, 0.0, 0.0, 0.0]
    mean = sum(values) / len(values)
    return [mean, min(values), max(values), sum(v * v for v in values) / len(values)]


def _char_bucket_features(text: str) -> List[float]:
    total = max(1.0, float(len(text)))
    return [
        sum(ch.isalpha() for ch in text) / total,
        sum(ch.isdigit() for ch in text) / total,
        sum(ch.isspace() for ch in text) / total,
        sum(ch in "<>/=_-:" for ch in text) / total,
        sum(ch in "[](){}" for ch in text) / total,
        sum(ch in ".,;!?" for ch in text) / total,
    ]


def _project_or_pad(features: List[float], dims: int) -> List[float]:
    if len(features) >= dims:
        return _normalize(features[:dims])
    padded = list(features)
    seed = "|".join(f"{value:.6f}" for value in features).encode("utf-8")
    while len(padded) < dims:
        digest = hashlib.sha256(seed + len(padded).to_bytes(4, "big")).digest()
        for idx in range(0, len(digest), 2):
            if len(padded) >= dims:
                break
            pair = int.from_bytes(digest[idx:idx + 2], "big")
            padded.append((pair / 65535.0) * 2.0 - 1.0)
    return _normalize(padded[:dims])


def _blend_with_text(primary: List[float], secondary: List[float]) -> List[float]:
    if not primary:
        return secondary
    if not secondary:
        return primary
    size = min(len(primary), len(secondary))
    values = [(0.75 * primary[idx]) + (0.25 * secondary[idx]) for idx in range(size)]
    return _normalize(values)
