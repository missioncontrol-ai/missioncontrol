import hashlib
import math
from typing import List
import numpy as np

EMBED_DIM = 384


def _tokenize(text: str) -> List[str]:
    return [t for t in text.lower().split() if t.strip()]


def _hash_token(token: str) -> int:
    return int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)


def embed_text(text: str) -> List[float]:
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * EMBED_DIM

    vec = np.zeros(EMBED_DIM, dtype=np.float32)
    for token in tokens:
        idx = _hash_token(token) % EMBED_DIM
        vec[idx] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm

    return vec.tolist()


def embed_texts(texts: List[str]) -> List[List[float]]:
    return [embed_text(t) for t in texts]
