"""Text embeddings (384 dims). fastembed (all-MiniLM-L6-v2) when available, else a hashing embedder.

The hashing embedder needs no model download, so the project runs offline and in CI.
Both produce 384-dim, L2-normalised vectors; seed data records which backend made them and is
re-embedded automatically if the backend changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re

log = logging.getLogger("shadowgraph.embed")
DIM = 384
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be", "by", "with",
    "it", "this", "that", "as", "at", "we", "our", "us", "do", "did", "does", "what", "which", "who", "how",
    "from", "into", "until", "after", "before", "all", "any", "can", "you", "i", "me", "my", "so", "if",
}


def _hash_embed(text: str) -> list[float]:
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
    feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    vec = [0.0] * DIM
    for feat in feats:
        digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign * (0.5 if "_" in feat else 1.0)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _model_hosts_reachable() -> bool:
    """fastembed retries with long sleeps when offline; check quickly first."""
    import httpx

    for url in ("https://huggingface.co", "https://storage.googleapis.com"):
        try:
            httpx.head(url, timeout=3.0)
            return True
        except Exception:
            continue
    return False


def _load_fastembed(model: str):
    from fastembed import TextEmbedding  # type: ignore

    try:  # already downloaded (the Docker image ships it): no network needed
        return TextEmbedding(model_name=model, local_files_only=True)
    except Exception:
        pass
    if not _model_hosts_reachable():
        raise RuntimeError("model not cached and the model hosts are unreachable")
    return TextEmbedding(model_name=model)


class Embedder:
    def __init__(self, backend: str = "auto", model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model
        self._model = None
        self.name = "hash"
        if backend in ("auto", "fastembed"):
            try:
                self._model = _load_fastembed(model)
                probe = list(self._model.embed(["probe"]))[0]
                if len(probe) != DIM:
                    raise ValueError(f"model returns {len(probe)} dims, expected {DIM}")
                self.name = f"fastembed:{model}"
            except Exception as exc:  # no network, no model, or wrong size
                if backend == "fastembed":
                    raise
                log.warning("fastembed unavailable (%s); using the hashing embedder", exc)
                self._model = None
                self.name = "hash"

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            return [_hash_embed(t) for t in texts]
        return [[float(x) for x in vec] for vec in self._model.embed(texts)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            return self.embed_sync(texts)
        return await asyncio.to_thread(self.embed_sync, texts)

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
