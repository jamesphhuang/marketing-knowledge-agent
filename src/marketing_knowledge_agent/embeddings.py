from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable, List


LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
CJK_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> List[str]:
    tokens = [match.group(0).lower() for match in LATIN_TOKEN_RE.finditer(text)]
    for sequence in CJK_SEQUENCE_RE.findall(text):
        tokens.extend(sequence)
        tokens.extend(_ngrams(sequence, 2))
    return tokens


def embed_text(text: str, dimensions: int = 64) -> List[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % dimensions
        sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
        vector[index] += sign
    return _normalize(vector)


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if not left_values or not right_values or len(left_values) != len(right_values):
        return 0.0
    return sum(a * b for a, b in zip(left_values, right_values))


def _normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _ngrams(text: str, size: int) -> List[str]:
    if len(text) < size:
        return []
    return [text[index : index + size] for index in range(0, len(text) - size + 1)]
